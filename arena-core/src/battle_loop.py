import asyncio
import json
import logging
import time
import httpx
from config import settings
from event_bus import publish_event
from models import BattleSession
from registry import get_registry
from retry_util import retry_call
from trace_writer import write_trace

_log = logging.getLogger(__name__)


def _adapter_retry(fn):
    """Retry an outbound adapter call on transient failures, per .env policy.
    A persistent failure re-raises (the round then aborts and the pre-round
    barrier detects the disconnect and finalizes the battle)."""
    return retry_call(
        fn,
        attempts=settings.adapter_retry_attempts,
        backoff=settings.adapter_retry_backoff,
    )


async def _autosave_report(session_id: str) -> None:
    """Ask report-composer to render + persist the report to disk at battle end.

    Fire-and-forget: a run is then saved under the project's reports/ directory
    regardless of whether anyone has the UI open, so a browser refresh never
    loses the result and no manual "Save PDF" is needed. Never blocks battle
    finalization — generating the LLM narrative can take a while, so use a
    generous timeout but swallow all errors.
    """
    url = f"{settings.report_composer_url}/v1/reports/{session_id}/save"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url)
            if r.status_code == 200:
                _log.info("Report auto-saved for %s -> %s", session_id, r.json().get("dir"))
            else:
                _log.warning("Report auto-save failed for %s: HTTP %s", session_id, r.status_code)
    except Exception as exc:
        _log.warning("Report auto-save error for %s: %s", session_id, exc)


async def _run_comprehension(session_id: str) -> dict:
    """Pre-battle READ-ONLY project comprehension for both sides. Best-effort:
    asks the analyzer to summarise each plugged-in project and propose a strategy
    suited to it. Returns {red:{...}, blue:{...}} (empty on failure). Emits a
    progress event for the UI. Never modifies any project."""
    if not settings.comprehension_enabled:
        return {}
    out: dict = {}
    await publish_event(session_id, "battle.comprehension.start", {
        "message": "Analyzing connected projects before battle…",
    })
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            for team in ("red", "blue"):
                try:
                    r = await client.post(
                        f"{settings.comprehension_url}/v1/comprehend",
                        params={"team": team},
                    )
                    if r.status_code == 200:
                        out[team] = r.json()
                except Exception as exc:
                    _log.info("comprehension %s failed: %s", team, exc)
    except Exception as exc:
        _log.info("comprehension unavailable: %s", exc)
    await publish_event(session_id, "battle.comprehension.done", {
        "red": out.get("red", {}),
        "blue": out.get("blue", {}),
    })
    # Surface each side's REAL comprehension analysis into the per-role thinking
    # chat (Recon Analyst). This is genuine LLM output, not fabricated flavor.
    for team in ("red", "blue"):
        prof = out.get(team) or {}
        parts = [prof.get("architecture_summary", ""), prof.get("suggested_strategy", "")]
        focus = prof.get("focus_areas") or []
        if focus:
            parts.append("Focus: " + ", ".join(str(f) for f in focus))
        text = "  ".join(p for p in parts if p).strip()
        if text:
            await _emit_reasoning(session_id, team, "recon", text, 0)
    return out


async def _emit_reasoning(session_id: str, team: str, role: str, text: str, round_n: int) -> None:
    """Publish one real per-role reasoning line for the thinking chat. `text` is
    always platform-computed real analysis (comprehension, self-challenge, or judge
    hints) — never fabricated. Callers gate on the inner-loop flag."""
    await publish_event(session_id, "agent.reasoning", {
        "team": team,
        "role": role,
        "text": text,
        "round": round_n,
    })


async def _finalize_disconnected(session: BattleSession, down_roles: list) -> None:
    """A connected adapter went unreachable and did not recover within the
    reconnect window. End the battle
    gracefully: mark it stopped with a reason and tell the UI why. The main
    loop's finalize block then persists the stopped state, emits `battle.stopped`
    (so the agents walk home), and saves the report over the rounds that
    completed. Because the interrupted round wrote no trace, the report carries
    no partial-round data."""
    session.status = "stopped"
    session.stop_reason = "adapter_disconnected"
    _log.warning(
        "Battle %s: role(s) %s unreachable past the reconnect window; "
        "stopping and finalizing over %d completed round(s).",
        session.id, down_roles, session.current_round,
    )
    await publish_event(session.id, "battle.disconnect_stopped", {
        "down_roles": down_roles,
        "completed_rounds": session.current_round,
        "reconnect_window_seconds": settings.disconnect_reconnect_window,
        "message": (f"Role(s) {down_roles} became unreachable and did not reconnect "
                    f"within {int(settings.disconnect_reconnect_window)}s. Battle stopped; "
                    f"report saved over {session.current_round} completed round(s)."),
    })


async def _await_adapters_in_position(session: BattleSession,
                                      down_grace: float = 120.0) -> None:
    """Hold before a round until EVERY role is in position and effective.

    A round only starts when all four roles — red adapter, blue adapter,
    target-ai and judge — answer /health.

    A role that is unhealthy is an unexplained fault (a model/adapter fell
    over). That we bound by `down_grace`; if it does
    not recover, we PAUSE the battle and surface it (`battle.needs_attention`)
    rather than running a degraded round against a dead role. Respects user STOP.
    """
    reg = get_registry()
    try:
        red_url = reg[session.red_service_id].url
        blue_url = reg[session.blue_service_id].url
    except Exception:
        return
    probes = (
        ("red", f"{red_url}/health"),
        ("blue", f"{blue_url}/health"),
        ("target-ai", f"{settings.target_ai_url}/health"),
        ("judge", f"{settings.judge_url}/health"),
    )
    announced = False
    down_waited = 0.0
    while session.status not in ("stopped",):
        down: list[str] = []
        async with httpx.AsyncClient(timeout=6.0) as c:
            for name, url in probes:
                try:
                    hr = await c.get(url)
                    ok = hr.status_code == 200 and (hr.json() or {}).get("status") == "ok"
                    if not ok:
                        down.append(name)
                except Exception:
                    down.append(name)

        if not down:
            if announced:
                await publish_event(session.id, "battle.resumed_ready",
                                    {"message": "All roles in position — resuming."})
            return

        if not announced:
            await publish_event(session.id, "battle.holding", {
                "unhealthy": down,
                "message": "Holding — a role is not yet in position; the battle waits "
                           "until every role is ready.",
            })
            announced = True

        if down:
            down_waited += 3.0
            # A role that stays down is a real disconnect. After the reconnect
            # window, stop the battle and finalize over the
            # rounds that completed (see _finalize_disconnected) rather than
            # pausing forever or running a round against a dead role.
            if down_waited >= settings.disconnect_reconnect_window:
                await _finalize_disconnected(session, down)
                return
        await asyncio.sleep(3.0)


async def _persist_session_progress(session: BattleSession) -> None:
    """Sync scoreboard columns to Postgres each round so report-composer sees live totals."""
    try:
        from trace_writer import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE battle_sessions
                SET red_wins = $1,
                    blue_wins = $2,
                    current_round = $3,
                    tokens_used = $4,
                    status = $5
                WHERE id = $6
                """,
                session.red_wins,
                session.blue_wins,
                session.current_round,
                session.tokens_used,
                session.status,
                session.id,
            )
    except Exception as exc:
        _log.error(
            "Persist session progress failed — Postgres ledger may lag arena memory "
            "(reports will show zeros until fixed). session=%s err=%s",
            session.id,
            exc,
            exc_info=True,
        )


def _arena_adapter_metadata(
    session: BattleSession | None = None,
    recent_strategies: list | None = None,
    target_actions: list | None = None,
) -> dict:
    """Passed to red/blue adapters so users can steer goals via .env (not code).

    Precedence is explicit override, then the engagement, then the .env global. The
    engagement belongs in that chain because the judge scores against the objective the
    scenario declared: if the attacker were told something else, the platform would be
    grading one goal while instructing another, and a scenario could never change what
    the attacker actually tries. An operator who set an override still wins, since that
    is a deliberate statement about this battle.
    """
    scenario = session.scenario if session and isinstance(session.scenario, dict) else {}
    meta: dict = {
        "arena_target_context": (session.target_context if session and session.target_context
                                 else settings.arena_target_context),
        "red_team_objective":   (session.red_team_objective if session and session.red_team_objective
                                 else scenario.get("objective")
                                 or settings.red_team_objective),
        "blue_team_objective":  (session.blue_team_objective if session and session.blue_team_objective
                                 else scenario.get("defender_objective")
                                 or settings.blue_team_objective),
    }
    # What the target can be made to do. Without this an attacker aiming at an agentic
    # objective has to guess the action surface, and the names are not ours to publish —
    # they belong to whichever toolpacks the target has loaded.
    if target_actions:
        meta["arena_target_actions"] = target_actions
    if recent_strategies:
        meta["recent_strategies"] = recent_strategies
    return meta


async def _load_recent_strategies(session_id: str, limit: int = 10) -> list[dict]:
    """Load recent strategy records from PostgreSQL to seed evolution context."""
    try:
        from trace_writer import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT team, round, mutation_type, strategy_hint, avoid_patterns
                FROM strategy_records
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                session_id, limit,
            )
        return [
            {
                "team": r["team"],
                "round": r["round"],
                "mutation_type": r["mutation_type"],
                "strategy_hint": r["strategy_hint"],
                "avoid_patterns": json.loads(r["avoid_patterns"]) if r["avoid_patterns"] else [],
            }
            for r in rows
        ]
    except Exception:
        return []


def _operator_taxonomy() -> list[str]:
    """Optional operator-supplied attack-type checklist from .env. The platform
    ships NO built-in attack taxonomy — it stays method-agnostic so any external
    adapter's own attack types are first-class. Empty by default."""
    raw = (settings.attack_taxonomy or "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


async def _scan_attack_coverage(session_id: str) -> dict:
    """Inspect execution_traces for the attack types this session actually produced.

    Method-agnostic. Returns:
        used         — attack types the adapter actually emitted this session
        blind_spots  — types from the OPTIONAL operator checklist not yet seen
                       (empty unless the operator configured a taxonomy)
        overused     — types repeated >= the diversity threshold (drives a generic
                       "vary your approach" nudge without naming any method)
    """
    try:
        from trace_writer import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT attack_type, COUNT(*) AS n
                FROM execution_traces
                WHERE session_id = $1 AND attack_type IS NOT NULL AND attack_type <> ''
                GROUP BY attack_type
                """,
                session_id,
            )
        counts = {r["attack_type"]: int(r["n"]) for r in rows}
        used = list(counts.keys())
        taxonomy = _operator_taxonomy()
        blind_spots = [t for t in taxonomy if t not in counts]
        threshold = int(settings.attack_repeat_nudge_threshold)
        overused = [t for t, n in counts.items() if n >= threshold]
        return {"used": used, "blind_spots": blind_spots, "overused": overused}
    except Exception:
        return {"used": [], "blind_spots": [], "overused": []}


async def _run_post_battle_meta(session_id: str) -> None:
    """Layer 3 meta-optimization: aggregate per-attack-type stats and persist.

    Runs once after a battle completes.  Writes one strategy_record per team
    summarising what worked and what didn't across the whole session.
    """
    import hashlib as _hashlib
    try:
        from trace_writer import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT attack_type,
                       COUNT(*) AS total,
                       SUM(CASE WHEN red_success THEN 1 ELSE 0 END) AS red_ok,
                       SUM(CASE WHEN blue_success THEN 1 ELSE 0 END) AS blue_ok,
                       AVG(harmfulness_score) AS avg_harm
                FROM execution_traces
                WHERE session_id = $1
                GROUP BY attack_type
                """,
                session_id,
            )
            if not rows:
                return

            # Red meta-record: rank attack types by success rate
            type_stats = [
                {
                    "attack_type": r["attack_type"] or "unknown",
                    "total": r["total"],
                    "red_success_rate": round(r["red_ok"] / r["total"], 3) if r["total"] else 0,
                    "blue_success_rate": round(r["blue_ok"] / r["total"], 3) if r["total"] else 0,
                    "avg_harm": round(float(r["avg_harm"] or 0), 3),
                }
                for r in rows
            ]
            best_red  = max(type_stats, key=lambda x: x["red_success_rate"])
            worst_red = min(type_stats, key=lambda x: x["red_success_rate"])

            red_hint = (
                f"Best attack type: {best_red['attack_type']} "
                f"({best_red['red_success_rate']*100:.0f}% success). "
                f"Worst: {worst_red['attack_type']} "
                f"({worst_red['red_success_rate']*100:.0f}%)."
            )
            red_fp_raw = f"red:meta:{best_red['attack_type']}"
            red_fp = _hashlib.md5(red_fp_raw.encode()).hexdigest()[:16]

            # Blue meta-record: rank by defense success rate
            best_blue = max(type_stats, key=lambda x: x["blue_success_rate"])
            blue_hint = (
                f"Best defended type: {best_blue['attack_type']} "
                f"({best_blue['blue_success_rate']*100:.0f}% block rate). "
                f"Avg harmfulness: {best_blue['avg_harm']:.2f}."
            )
            blue_fp_raw = f"blue:meta:{best_blue['attack_type']}"
            blue_fp = _hashlib.md5(blue_fp_raw.encode()).hexdigest()[:16]

            total_rounds = sum(r["total"] for r in rows)
            await conn.executemany(
                """
                INSERT INTO strategy_records
                  (session_id, team, round, mutation_type, strategy_hint,
                   avoid_patterns, pattern_fingerprint, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                """,
                [
                    (session_id, "red",  total_rounds, "meta_summary", red_hint,  "[]", red_fp),
                    (session_id, "blue", total_rounds, "meta_summary", blue_hint, "[]", blue_fp),
                ],
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Post-battle meta-optimization failed: %s", exc)


def _looks_like_refusal(text: str) -> bool:
    """Heuristic: did the model return a safety refusal instead of real content?

    A red adapter backed by a safety-aligned model sometimes returns "I'm sorry,
    I can't help with that" as its *attack payload*; likewise a blue/judge model
    may refuse. That is not a genuine adversarial exchange — per the truthful-
    surfaces rule we must detect it and surface it, never present a refusal as a
    real attack. This is a content-shape guard, not an attack taxonomy: it only
    flags very short outputs dominated by common refusal phrasing.
    """
    if not text:
        return True
    t = " ".join(text.lower().split())
    if len(t) > 320:                      # a real attack is rarely this short
        return False
    markers = (
        "i'm sorry", "i am sorry", "i cannot assist", "i can't assist",
        "i cannot help", "i can't help", "cannot comply", "can't comply",
        "unable to assist", "unable to help", "i won't", "i will not",
        "cannot fulfill", "can't fulfill", "not able to help",
    )
    return any(m in t for m in markers)


def _adapter_endpoint(service_id: str, *, through_wrapper: bool = False) -> str:
    """Where this side's calls actually go.

    The evolution wrapper is plumbing, not an opponent: the operator picks their own
    project, and the platform decides whether to put the in-context layers in front of
    it. A wrapper proxies to exactly one downstream, which it reports on its own health
    check, so the wrapper for a given project is found by asking rather than by holding
    a mapping here — a project registered under any name is matched by where the wrapper
    actually points.

    Without this the wrapper was registered, hidden from the picker as internal
    plumbing, and then never called: enabling the in-context loop ran the per-round
    judge hints but not the batch analysis, the cross-session memory, or the prompt
    variant layer, because those live in the wrapper.
    """
    registry = get_registry()
    svc = registry[service_id]
    if not through_wrapper:
        return svc.url
    for candidate in registry.values():
        if candidate.type != svc.type:
            continue
        caps = candidate.capabilities or {}
        if not caps.get("evolution_wrapper"):
            continue
        if str(caps.get("downstream") or "").rstrip("/") == svc.url.rstrip("/"):
            return candidate.url
    return svc.url


async def _call_red(
    service_id: str,
    session_id: str,
    round_n: int,
    evolution_hints: dict | None = None,
    metadata: dict | None = None,
    conversation: list[dict] | None = None,
    through_wrapper: bool = False,
) -> dict:
    registry = get_registry()
    svc = registry[service_id]
    endpoint = _adapter_endpoint(service_id, through_wrapper=through_wrapper)
    headers = {"Authorization": f"Bearer {svc.token}"} if svc.token else {}

    async def _do():
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{endpoint}/v1/generate-attack", headers=headers, json={
                "session_id": session_id,
                "round": round_n,
                "target_context": settings.arena_target_context or "",
                "evolution_hints": evolution_hints or {},
                "metadata": metadata or _arena_adapter_metadata(),
                # Prior turns of THIS session's conversation (attacker msg + target
                # reply). Lets a multi-turn adapter follow up on partial disclosures
                # instead of sending disjoint one-shots. Additive + optional — adapters
                # that ignore it keep working single-turn.
                "conversation": conversation or [],
            })
            r.raise_for_status()
            return r.json()

    return await _adapter_retry(_do)


async def _call_blue(
    service_id: str,
    session_id: str,
    round_n: int,
    attack_payload: str,
    evolution_hints: dict | None = None,
    metadata: dict | None = None,
    through_wrapper: bool = False,
) -> dict:
    registry = get_registry()
    svc = registry[service_id]
    endpoint = _adapter_endpoint(service_id, through_wrapper=through_wrapper)
    headers = {"Authorization": f"Bearer {svc.token}"} if svc.token else {}

    async def _do():
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{endpoint}/v1/evaluate-defense", headers=headers, json={
                "session_id": session_id,
                "round": round_n,
                "attack_payload": attack_payload,
                "evolution_hints": evolution_hints or {},
                "metadata": metadata or _arena_adapter_metadata(),
            })
            r.raise_for_status()
            return r.json()

    return await _adapter_retry(_do)


async def _call_blue_filter_output(
    service_id: str,
    session_id: str,
    round_n: int,
    *,
    attack_payload: str,
    raw_response: str,
    input_decision: str,
    input_reason: str,
    evolution_hints: dict | None = None,
    metadata: dict | None = None,
    through_wrapper: bool = False,
) -> dict[str, object]:
    """POST /v1/filter-output — falls back to passthrough when missing or unreachable."""
    registry = get_registry()
    svc = registry[service_id]
    endpoint = _adapter_endpoint(service_id, through_wrapper=through_wrapper)
    headers = {"Authorization": f"Bearer {svc.token}"} if svc.token else {}
    body = {
        "session_id": session_id,
        "round": round_n,
        "attack_payload": attack_payload,
        "raw_response": raw_response,
        "input_decision": input_decision,
        "input_reason": input_reason,
        "evolution_hints": evolution_hints or {},
        "metadata": metadata or _arena_adapter_metadata(),
    }
    fallback = {
        "final_response": (raw_response or "").strip(),
        "was_modified": False,
        "modification_reason": "",
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            r = await client.post(f"{endpoint}/v1/filter-output", headers=headers, json=body)
            if r.status_code == 404:
                return fallback
            r.raise_for_status()
            data = r.json()
            fin = str(data.get("final_response") or "").strip()
            if not fin:
                fin = fallback["final_response"] or "—"
            return {
                "final_response": fin,
                "was_modified": bool(data.get("was_modified", False)),
                "modification_reason": str(data.get("modification_reason") or ""),
            }
        except Exception:
            return {
                **fallback,
                "final_response": fallback["final_response"] or "—",
            }


async def _target_actions(enabled_tools) -> list:
    """Ask the target what it can be made to do, narrowed to what this battle enables.

    The platform holds no copy of the catalogue. An operator who adds a toolpack should
    not have to edit anything here for an attacker to know the action exists.
    """
    if not enabled_tools:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{settings.target_ai_url}/capabilities")
            r.raise_for_status()
            actions = r.json().get("actions") or []
    except Exception as exc:
        _log.info("Target action surface unavailable (%s); the attacker will not be told it", exc)
        return []
    if "*" not in enabled_tools:
        actions = [a for a in actions if a.get("name") in set(enabled_tools)]
    return [{k: a.get(k) for k in ("name", "description", "effect", "risk",
                                   "requires_authorisation")} for a in actions]


async def _resolve_scenario(scenario):
    """Expand a scenario reference into the declaration itself.

    An engagement may be declared inline or named, and a named one lives in a file this
    service does not read — the judge owns scenario resolution, because it is the service
    that scores against it. So a name is resolved by asking. Parsing it here as well
    would mean two parsers and two sets of defaults, free to drift apart.

    A failure to resolve is not fatal: the battle proceeds conversationally, which is what
    a battle that declared nothing does anyway.
    """
    if scenario is None or isinstance(scenario, dict):
        return scenario
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{settings.judge_url}/scenario/resolve",
                                  json={"scenario": scenario})
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        _log.warning("Could not resolve scenario %r (%s); running conversationally", scenario, exc)
        return None


def _scenario_view(scenario):
    """Which target actions this engagement enables, and the rule it must uphold.

    Reads only what it needs from whatever form the scenario was supplied in, so a
    battle that declares nothing keeps the conversational-only behaviour.
    """
    if not isinstance(scenario, dict):
        return {"tools": [], "policy": ""}
    caps = scenario.get("target_capabilities") or []
    tools = list((scenario.get("enabled_tools") or [])) if "tools" in caps else []
    if "tools" in caps and not tools:
        # Capability enabled without naming actions means "whatever this target has".
        # The names are deliberately not repeated here: the target owns its action
        # catalogue, and a copy in this file would have to be edited every time an
        # operator adds one. The target resolves the wildcard.
        tools = ["*"]
    # Passed through in whatever form the engagement declared it: prose configures the
    # target's own restraint, a declaration also configures the boundary around its
    # actions. Coercing to text here would silently discard the latter.
    policy = scenario.get("target_policy") or "" if "policy" in caps else ""
    return {"tools": tools, "policy": policy}


async def _call_target_ai(session_id: str, message: str, *,
                          enabled_tools=(), policy=""):
    """Return (response_text, tool_calls).

    A target that can take actions reports which ones this turn invoked, because a
    scenario may treat invoking a particular action as proof that its objective was
    met. A target with no actions simply reports none.

    The records are passed on whole rather than reduced to names. Whether a call was
    permitted to take effect is the more interesting half of an agentic engagement: a
    target that attempts to act outside its authority has already failed, even when the
    boundary stops it, and only the record distinguishes the two. A target that reports
    bare names still works — it is normalised to the same shape with the outcome unknown.
    """
    async def _do():
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{settings.target_ai_url}/chat",
                                  json={"session_id": session_id, "message": message,
                                        "enabled_tools": list(enabled_tools or []),
                                        "policy": policy if policy else ""})
            r.raise_for_status()
            data = r.json()
            records = []
            for c in (data.get("tool_calls") or []):
                if isinstance(c, dict):
                    if c.get("name"):
                        records.append(c)
                elif str(c):
                    records.append({"name": str(c)})
            return data["response"], records

    return await _adapter_retry(_do)


async def _target_confidential_inventory() -> list:
    """What the target says it holds, each item with the kind of material it is.

    Asked of the target because the target owns the data; the platform does not keep a
    second copy to drift out of date, and the referee never guesses a kind from the
    shape of a string. An empty answer simply means scoring proceeds as it did before.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{settings.target_ai_url}/confidential-inventory")
            r.raise_for_status()
            items = (r.json() or {}).get("items") or []
        return [i for i in items if isinstance(i, dict) and i.get("value")]
    except Exception as exc:
        _log.info("Target published no confidential inventory (%s); scoring will use "
                  "the engagement's declared markers alone", exc)
        return []


async def _adapter_profile(service_id: str) -> dict:
    """Whatever a participant publishes on /health, verbatim. Never required."""
    try:
        svc = get_registry()[service_id]
    except Exception:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{svc.url}/health")
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        _log.info("No profile from %s (%s)", service_id, exc)
        return {}


async def _agree_scoring_brief(session) -> str:
    """Settle, once, what this battle is judged against.

    Any project may connect with any objective, so the referee is told what each side
    declared before the first round rather than inferring intent from each payload.
    Advisory: on any failure the battle runs with no brief and scores exactly as it
    did before.
    """
    try:
        red_profile, blue_profile = await asyncio.gather(
            _adapter_profile(session.red_service_id),
            _adapter_profile(session.blue_service_id),
        )
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{settings.judge_url}/v1/brief", json={
                "red_profile": red_profile,
                "blue_profile": blue_profile,
                "red_objective": session.red_team_objective or "",
                "blue_objective": session.blue_team_objective or "",
                "scenario": session.scenario,
                # The kinds in here are what the declared objective is read against.
                # Fetched before this call for exactly that reason.
                "confidential_inventory": list(getattr(session, "_confidential_inventory", ()) or []),
            })
            r.raise_for_status()
            answer = r.json() or {}
        brief = str(answer.get("brief") or "").strip()
        session._objective_kinds = [str(k) for k in (answer.get("objective_kinds") or [])]
        session._objective_basis = str(answer.get("objective_basis") or "")
        if brief:
            _log.info("Scoring brief agreed for %s (%d chars); success basis: %s (%s)",
                      session.id, len(brief),
                      ", ".join(session._objective_kinds) or "not narrowed",
                      session._objective_basis or "unstated")
        return brief
    except Exception as exc:
        _log.warning("No scoring brief for %s (%s); rounds are judged on the "
                     "engagement declaration alone", session.id, exc)
        session._objective_kinds = []
        session._objective_basis = ("the brief could not be agreed, so no kind of "
                                    "material was excluded")
        return ""


async def _call_judge(
    session_id: str,
    round_n: int,
    *,
    attack_payload: str,
    raw_target_response: str,
    final_target_response: str,
    blue_decision: str = "allow",
    blue_confidence: float = 0.5,
    blue_reason: str = "",
    scenario=None,
    tool_calls=(),
    confidential_inventory=(),
    brief="",
    objective_kinds=(),
) -> dict:
    async def _do():
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{settings.judge_url}/evaluate", json={
                "session_id":              session_id,
                "round":                   round_n,
                "attack_payload":          attack_payload,
                # Legacy single-field clients see only target_response
                "target_response":         final_target_response,
                "raw_target_response":     raw_target_response,
                "final_target_response":   final_target_response,
                "blue_decision":           blue_decision,
                "blue_confidence":         blue_confidence,
                "blue_reason":             blue_reason,
                # What this engagement is about. The judge scores against the
                # scenario's declared objective; omitted means the bundled default.
                "scenario":                scenario,
                "tool_calls":              list(tool_calls or []),
                # What the target holds, so the referee can tell a disclosure of THIS
                # engagement's objective from a disclosure of something else.
                "confidential_inventory":  list(confidential_inventory or []),
                # What each side declared it was trying to do, agreed once at the start.
                "brief":                   brief or "",
                # Which kinds of the target's material serve the declared objective.
                # Resolved from the declarations, never chosen by the platform.
                "objective_kinds":         list(objective_kinds or []),
            })
            r.raise_for_status()
            return r.json()

    return await _adapter_retry(_do)


async def _run_round(
    session: BattleSession,
    round_n: int,
    red_hints: dict | None = None,
    blue_hints: dict | None = None,
    metadata: dict | None = None,
    conversation: list[dict] | None = None,
) -> tuple[str, dict] | None:
    """Run one round and return (verdict, evolution_hints_from_judge), or ``None`` if user STOP before trace write.

    `conversation` carries prior (attacker, target) turns of this session so a
    multi-turn red adapter can follow up. It is appended to in place after the
    target responds, capped to the configured memory window.
    """
    t0 = time.monotonic()

    await publish_event(session.id, "battle.round.start", {"round": round_n})
    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None

    # The in-context layers live in the wrapper, so enabling the loop is what puts
    # the wrapper in the path. With the loop off the adapter is called directly and
    # no analysis models are spent.
    via_wrapper = bool(session.inner_loop_enabled)
    attack = await _call_red(session.red_service_id, session.id, round_n, red_hints,
                             metadata, conversation=conversation or [],
                             through_wrapper=via_wrapper)
    # If the attacker's model refused to produce an attack, its "payload" is a
    # refusal string, not a genuine attack. Give the adapter one more chance
    # (adapters often vary technique per call), then surface the outcome
    # truthfully rather than parading a refusal as a real attack.
    attacker_refused = _looks_like_refusal(attack.get("attack_payload", ""))
    if attacker_refused and session.status != "stopped":
        retry = await _call_red(session.red_service_id, session.id, round_n, red_hints,
                                metadata, conversation=conversation or [],
                                through_wrapper=via_wrapper)
        if not _looks_like_refusal(retry.get("attack_payload", "")):
            attack = retry
            attacker_refused = False
    if attacker_refused:
        await publish_event(session.id, "red.attack.refused", {
            "round": round_n,
            "message": "Attacker model declined to generate an attack this round "
                       "(safety refusal) — no genuine attack was produced.",
        })
    attack["attacker_refused"] = attacker_refused
    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None
    await publish_event(session.id, "red.attack.sent", {
        "round": round_n,
        "attack_payload": attack["attack_payload"],
        "attack_type": attack.get("attack_type", ""),
        "confidence": attack.get("confidence", 0.0),
        "attacker_refused": attacker_refused,
        # strategy_metadata is optional — adapters may declare their current strategy type
        "strategy_metadata": attack.get("strategy_metadata", {}),
    })

    # Capability-aware: skip input-guard call if blue adapter doesn't support it.
    blue_caps = get_registry()[session.blue_service_id].capabilities or {}
    supports_input  = bool(blue_caps.get("supports_input_guard",  True))
    supports_output = bool(blue_caps.get("supports_output_guard", False))

    if supports_input:
        defense = await _call_blue(
            session.blue_service_id, session.id, round_n, attack["attack_payload"],
            blue_hints, metadata, through_wrapper=via_wrapper,
        )
    else:
        # No input guard declared → automatic allow, attack reaches Target AI.
        defense = {
            "decision":   "allow",
            "reason":     "blue adapter declares no input guard — passthrough",
            "confidence": 0.0,
        }
    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None
    decision = defense["decision"]

    verdict = "failure"
    harm_score = 0.0
    judge_reason = ""
    new_evolution_hints: dict = {}

    blue_confidence = defense.get("confidence", 0.0)
    blue_reason_str = defense.get("reason", "")

    # Prompt actually sent INTO Target AI (blocked path still runs real Target)
    payload_to_target: str
    if decision == "block":
        await publish_event(session.id, "blue.defense.blocked", {
            "round": round_n,
            "reason": blue_reason_str,
            "confidence": blue_confidence,
        })
        payload_to_target = attack["attack_payload"]
    else:
        payload_to_target = defense.get("rewritten_payload") or attack["attack_payload"]
        await publish_event(session.id, "blue.defense.allowed", {
            "round": round_n,
            "decision":   decision,
            "reason":     blue_reason_str,
            "confidence": blue_confidence,
            "rewritten_payload": payload_to_target,
        })

    # Hybrid gate semantics:
    #  - decision=block → the attack is STOPPED in production: we do NOT deliver
    #    the target's raw output. But we still SHADOW-PROBE the target so the
    #    judge can measure red's true potency (raw harm / PSS) — this keeps the
    #    co-evolution gradient alive even against a perfect blue. The probe
    #    result is for scoring only and is never delivered downstream.
    #  - decision=allow → normal flow: target output goes through the output gate.
    blocked = decision == "block"
    # The engagement decides what the target can do and what rule it must uphold.
    _sc = _scenario_view(session.scenario)
    raw_target_response, target_tool_calls = await _call_target_ai(
        session.id, payload_to_target,
        enabled_tools=_sc["tools"], policy=_sc["policy"])
    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None

    await publish_event(session.id, "target.ai.responded", {
        "round": round_n,
        "prompt_sent_to_target": payload_to_target,
        "raw_response": raw_target_response,
        # When blocked, the target was only shadow-probed for measurement —
        # the response is NOT delivered to the observer.
        "shadow_probe": blocked,
        "delivered": not blocked,
    })

    # Record this turn so the red adapter can follow up next round. We feed the
    # RAW target response (what the target actually said) so a multi-turn adapter
    # can supply whatever the target asked for and complete an extraction across
    # turns. Cap to the configured window to bound the prompt size.
    if conversation is not None:
        conversation.append({"attacker": attack["attack_payload"], "target": raw_target_response})
        keep = max(1, int(settings.conversation_memory_turns))
        if len(conversation) > keep:
            del conversation[:-keep]

    if blocked:
        # Input gate stopped the attack. Nothing reaches the observer; the
        # output gate is irrelevant. Final delivered text is the blocked notice.
        final_response = "[BLOCKED BY BLUE INPUT GATE — attack stopped, not delivered]"
        was_modified = True
        modification_reason = (
            "input gate blocked the attack; target was shadow-probed for "
            "red-potency measurement only (not delivered)"
        )
    elif supports_output:
        # Capability-aware: only call output-guard when allowed + declared.
        out_f = await _call_blue_filter_output(
            session.blue_service_id,
            session.id,
            round_n,
            attack_payload=attack["attack_payload"],
            raw_response=raw_target_response,
            input_decision=decision,
            input_reason=blue_reason_str,
            evolution_hints=blue_hints,
            metadata=metadata,
            through_wrapper=via_wrapper,
        )
        final_response = str(out_f["final_response"])
        was_modified = bool(out_f["was_modified"])
        modification_reason = str(out_f.get("modification_reason") or "")
    else:
        # Allowed + no output guard declared → raw response passes through.
        final_response = (raw_target_response or "").strip()
        was_modified = False
        modification_reason = "blue adapter declares no output guard — passthrough"

    await publish_event(session.id, "blue.output.filtered", {
        "round": round_n,
        "raw_response": raw_target_response,
        "final_response": final_response,
        "was_modified": was_modified,
        "modification_reason": modification_reason,
        "shadow_probe": blocked,
        "delivered": not blocked,
    })

    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None

    judge_result = await _call_judge(
        session.id,
        round_n,
        attack_payload=attack["attack_payload"],
        raw_target_response=raw_target_response,
        final_target_response=final_response,
        blue_decision=decision,
        blue_confidence=blue_confidence,
        blue_reason=blue_reason_str,
        scenario=session.scenario,
        confidential_inventory=getattr(session, "_confidential_inventory", ()),
        brief=getattr(session, "_scoring_brief", ""),
        objective_kinds=getattr(session, "_objective_kinds", ()),
        tool_calls=target_tool_calls,
    )
    if session.status == "stopped":
        await publish_event(session.id, "battle.round.cancelled", {"round": round_n, "reason": "user_stop"})
        return None

    verdict = judge_result.get("verdict", "failure")
    harm_score = judge_result.get("harm_score", 0.0)
    _dims = judge_result.get("dimensions", {}) or {}
    raw_harm_score = _dims.get("raw_harm_score", harm_score)
    judge_reason = judge_result.get("reason", "")
    new_evolution_hints = judge_result.get("evolution_hints", {})
    await publish_event(session.id, "judge.verdict", {
        "round": round_n,
        "verdict":       verdict,
        "harm_score":    harm_score,
        "reason":        judge_reason,
        "red_verdict":   judge_result.get("red_verdict", verdict),
        "blue_verdict":  judge_result.get("blue_verdict", "failure"),
        "dimensions":    judge_result.get("dimensions", {}),
        "evolution_hints": new_evolution_hints,
        })

    await write_trace(
        session_id=session.id,
        round_n=round_n,
        attack_payload=attack["attack_payload"],
        attack_type=attack.get("attack_type", ""),
        attack_confidence=attack.get("confidence", 0.0),
        defense_decision=decision,
        defense_confidence=defense.get("confidence", 0.0),
        defense_reason=defense.get("reason", ""),
        final_payload=payload_to_target,
        raw_target_response=raw_target_response,
        final_target_response=final_response,
        output_was_modified=was_modified,
        output_modification_reason=modification_reason,
        red_success=(verdict == "success"),
        blue_success=(verdict == "failure"),
        # Kept as-is: both booleans mean a STRICT win for that side. The grade
        # itself is stored beside them so a partial round is recorded as partial
        # instead of as neither.
        round_verdict=verdict,
        objective_achieved=bool(judge_result.get("objective_achieved", verdict == "success")),
        evidence_matched=str(judge_result.get("evidence_matched") or ""),
        incidental_disclosures=judge_result.get("incidental_disclosures") or [],
        harm_score=harm_score,
        raw_harm_score=raw_harm_score,
        judge_reasoning=judge_reason,
        attacker_refused=bool(attack.get("attacker_refused", False)),
        target_tool_calls=target_tool_calls,
    )

    await publish_event(session.id, "battle.round.complete", {"round": round_n, "verdict": verdict})

    cool = settings.post_round_cooldown_seconds
    if cool > 0:
        elapsed = 0.0
        while elapsed < cool:
            if session.status == "stopped":
                break
            await asyncio.sleep(min(0.5, cool - elapsed))
            elapsed += 0.5

    _name_the_next_move(session, new_evolution_hints, attack, verdict)

    return verdict, new_evolution_hints


def _declared_strategy_label(attack: dict) -> str:
    """The word the ATTACKER used for what it just did.

    Read from the fields an adapter may declare, most specific first. The platform
    has no taxonomy of its own here and does not parse or decompose the label — it
    only repeats it.
    """
    for source in (attack.get("metadata"), attack.get("strategy_metadata")):
        if isinstance(source, dict):
            label = source.get("technique")
            if isinstance(label, str) and label.strip():
                return label.strip()
    label = attack.get("attack_type")
    return label.strip() if isinstance(label, str) and label.strip() else ""


def _name_the_next_move(session: BattleSession, hints: dict, attack: dict, verdict: str) -> None:
    """Turn the judge's direction into a strategy name the attacker can act on.

    Only the attacker's own words are used. A round that succeeded names the label
    the attacker itself just declared, so the technique that worked is the one
    reinforced; nothing is named otherwise, which leaves the attacker free to
    explore rather than being pushed toward a word that means nothing to it.

    Before this, the judge invented the name. Three of its five possible names
    matched no technique in the bundled attacker, including the one it emitted on
    a WIN — so winning taught nothing — while the one it emitted on a low-evasion
    LOSS did match, was forced, kept losing, and was forced again. A 30-round
    diagnostic showed the attacker win rounds 3-9 with one technique and never
    return to it.
    """
    red = hints.get("red")
    if not isinstance(red, dict):
        return

    label = _declared_strategy_label(attack)
    vocabulary = getattr(session, "_declared_strategies", None)
    if not isinstance(vocabulary, set):
        vocabulary = set()
    if label:
        vocabulary.add(label)
    session._declared_strategies = vocabulary

    # Additive context an adapter may use or ignore.
    red["last_strategy"] = label
    red["last_outcome"] = verdict
    red["strategy_vocabulary"] = sorted(vocabulary)

    named = label if (verdict == "success" and label) else ""
    if named:
        # Both keys: an adapter reached directly reads mutation_type, and the
        # in-context wrapper reads suggested_mutation and passes it down as
        # mutation_type. Same word either way.
        red["suggested_mutation"] = named
        red["mutation_type"] = named
    else:
        red.pop("mutation_type", None)
        # Never hand down a name the attacker has not used itself.
        candidate = red.get("suggested_mutation")
        if not isinstance(candidate, str) or candidate not in vocabulary:
            red.pop("suggested_mutation", None)


def _check_user_stop_conditions(session: BattleSession, round_n: int) -> str:
    """Evaluate user-defined stop conditions. Returns exit_reason or '' if none fired.

    Conditions:
      target_asr        : stop when red ASR >= target_asr
      target_dr         : stop when blue DR  >= target_dr
      target_win_streak : stop when either side has N consecutive wins
      asr_uplift_pct    : stop when ASR has improved by N percentage points
                          vs baseline_asr (captured at battle start)
    """
    total = session.red_wins + session.blue_wins
    if total == 0:
        return ""

    # The operator can ask for a rate to hold over a window rather than from the
    # first round. Without this the rate is the running average from round one, so
    # one won round reads as 100% attack success and a battle asked to stop at 30%
    # stops immediately — a stop condition triggered by a single sample.
    # A streak is already a window over consecutive rounds, so it answers on its own
    # terms and is not held back waiting for the rate window below to fill.
    if session.target_win_streak is not None and session.target_win_streak > 0 \
            and session._current_streak >= session.target_win_streak:
        return (
            f"win_streak_reached ({session._last_winner} "
            f"streak={session._current_streak} >= {session.target_win_streak})"
        )

    window = session.stop_window_rounds or 1
    if window > 1:
        if total < window:
            return ""            # not enough judged rounds to answer yet
        recent = session._recent_outcomes[-window:]
        red_recent = sum(1 for won_by_red in recent if won_by_red)
        asr = red_recent / len(recent)
        dr = 1.0 - asr
    else:
        asr = session.red_wins / total
        dr  = session.blue_wins / total

    if session.target_asr is not None and session.target_asr > 0 and asr >= session.target_asr:
        return f"target_asr_reached ({asr:.2%} >= {session.target_asr:.2%})"
    if session.target_dr is not None and session.target_dr > 0 and dr >= session.target_dr:
        return f"target_dr_reached ({dr:.2%} >= {session.target_dr:.2%})"
    if session.asr_uplift_pct is not None and session.asr_uplift_pct > 0 \
            and session.baseline_asr is not None:
        delta_pp = (asr - session.baseline_asr) * 100.0
        if delta_pp >= session.asr_uplift_pct:
            return (
                f"asr_uplift_reached (+{delta_pp:.1f}pp >= "
                f"{session.asr_uplift_pct:.1f}pp; baseline={session.baseline_asr:.2%} "
                f"current={asr:.2%})"
            )
    return ""


async def run_battle(session: BattleSession) -> None:
    import time as _time
    session.started_at = _time.monotonic()
    error_count = 0
    round_n = 0
    # Included in battle.complete payload so the UI can explain unexpected finishes.
    loop_exit_reason = "unknown"

    # How long to wait out a recoverable fault before trying the round again, and how
    # many times. Six holds of five minutes tolerates half an hour of provider trouble;
    # beyond that the run ends rather than sitting idle for the whole poll budget.
    _RECOVERABLE_HOLD_SECONDS = 300.0
    _MAX_RECOVERABLE_HOLDS = 6
    recoverable_holds = 0

    # Terminal model faults already announced this battle. Without this the same dead
    # account would raise an alert every round for the rest of the run, which trains the
    # operator to ignore the alert that matters.
    _announced_faults: set[str] = set()

    # Expand the engagement declaration once, up front. A named scenario has to be
    # resolved before its target actions and standing rule can be handed to the target,
    # and resolving per round would repeat the call for every round of the battle.
    session.scenario = await _resolve_scenario(session.scenario)

    # Two more things settled once, before the first round: what the target holds, and
    # what each side declared it was trying to do. Both are then carried into every
    # round's scoring, so a verdict is made against this battle's own terms.
    session._confidential_inventory = await _target_confidential_inventory()
    session._scoring_brief = await _agree_scoring_brief(session)
    # Announce the basis a verdict will rest on before any round is scored, so it is
    # part of the record rather than something a reader has to reconstruct.
    await publish_event(session.id, "battle.scoring_basis", {
        "objective_kinds": list(session._objective_kinds),
        "how": session._objective_basis,
        "brief": session._scoring_brief[:600],
        "message": ("Scoring basis: "
                    + (", ".join(session._objective_kinds) or "every kind of material")
                    + f" — {session._objective_basis or 'not stated'}"),
    })

    # Load any existing strategy records for this session (useful on resume).
    recent_strategies = await _load_recent_strategies(session.id)
    target_actions = await _target_actions(_scenario_view(session.scenario)["tools"])
    metadata = _arena_adapter_metadata(session=session,
                                       recent_strategies=recent_strategies or None,
                                       target_actions=target_actions or None)

    # Pre-battle comprehension: understand each connected project first, then
    # carry its advisory strategy into the battle as metadata (never forced).
    # This is part of the INNER loop (assisting-model read-only analysis); when
    # the inner loop is off, skip it so a plain battle runs no assist behaviour.
    comprehension = await _run_comprehension(session.id) if session.inner_loop_enabled else {}
    if comprehension:
        metadata = {**metadata, "comprehension": comprehension}

    # Per-team evolution hints — updated after every round from judge feedback.
    # Seed each side's hints with its comprehension-suggested strategy (advisory).
    evolution_hints: dict = {"red": {}, "blue": {}}
    for _team in ("red", "blue"):
        prof = comprehension.get(_team) if comprehension else None
        if prof and prof.get("suggested_strategy"):
            evolution_hints[_team]["comprehension_strategy"] = prof["suggested_strategy"]
            if prof.get("focus_areas"):
                evolution_hints[_team]["comprehension_focus"] = prof["focus_areas"]

    # Multi-turn conversation memory for this session (attacker + target turns).
    # Passed to red each round so it can follow up; appended to in _run_round.
    conversation: list[dict] = []

    # Phase C: self-challenge interval (check attack coverage every N rounds).
    challenge_interval: int = settings.challenge_interval_rounds

    # True infinite mode: only user STOP (or fatal adapter errors) ends the loop.
    # Ignore stray max_rounds / token pause / wall-clock — those apply to deathmatch only.
    is_infinite_mode = session.mode == "infinite"

    while True:
        # User-initiated stop — break immediately
        if session.status == "stopped":
            loop_exit_reason = "user_stop"
            break

        round_n += 1

        # Stopping conditions — max_rounds / token / time do NOT apply to infinite mode.
        if (
            not is_infinite_mode
            and session.max_rounds is not None
            and session.max_rounds > 0
            and round_n > session.max_rounds
        ):
            loop_exit_reason = "max_rounds"
            break

        if session.mode == "deathmatch" and session.win_threshold:
            if session.red_wins >= session.win_threshold or session.blue_wins >= session.win_threshold:
                loop_exit_reason = "win_threshold"
                break

        if not is_infinite_mode and session.tokens_used >= session.token_budget:
            await publish_event(session.id, "battle.paused", {"reason": "token_budget_exhausted"})
            session.status = "paused"
            while session.status == "paused":
                await asyncio.sleep(0.5)
            if session.status not in ("running",):
                loop_exit_reason = (
                    "user_stop" if session.status == "stopped"
                    else f"status_{session.status}"
                )
                break

        if (
            not is_infinite_mode
            and session.time_limit_seconds
            and session.started_at
            and _time.monotonic() - session.started_at > session.time_limit_seconds
        ):
            loop_exit_reason = "time_limit"
            break

        # Honor pause flag — also break immediately if user stops while paused
        while session.status == "paused":
            await asyncio.sleep(0.5)
            if session.status == "stopped":
                break
        if session.status == "stopped":
            loop_exit_reason = "user_stop"
            break

        # ── Sync gate: wait until BOTH sides are in position before this round ──
        # (no side mid-improvement, both adapters healthy). If one side is being
        # modified, the other waits too; also catches a model/adapter that died.
        await _await_adapters_in_position(session)
        if session.status == "stopped":
            loop_exit_reason = "user_stop"
            break

        # ── Phase C: self-challenge every N rounds (method-agnostic) ──
        # We never prescribe attack methods. If the operator configured a coverage
        # checklist we surface its gaps; otherwise we only nudge for DIVERSITY when
        # the adapter keeps repeating the same self-reported attack_type.
        if (session.inner_loop_enabled
                and round_n > 1 and round_n % challenge_interval == 0):
            coverage = await _scan_attack_coverage(session.id)
            if coverage["blind_spots"]:
                metadata = {
                    **metadata,
                    "challenge_mode": True,
                    "suggested_attack_types": coverage["blind_spots"],
                    "vulnerability_blind_spots": coverage["blind_spots"],
                }
                await publish_event(session.id, "battle.challenge_mode", {
                    "round": round_n,
                    "blind_spots": coverage["blind_spots"],
                })
                await _emit_reasoning(
                    session.id, "red", "strategy",
                    "Coverage gap — attack types not yet tried: "
                    + ", ".join(str(b) for b in coverage["blind_spots"]),
                    round_n,
                )
            elif coverage["overused"]:
                metadata = {
                    **metadata,
                    "challenge_mode": True,
                    "diversify": True,
                    "overused_attack_types": coverage["overused"],
                }
                await publish_event(session.id, "battle.challenge_mode", {
                    "round": round_n,
                    "overused": coverage["overused"],
                })
                await _emit_reasoning(
                    session.id, "red", "strategy",
                    "Over-using attack types (diversify): "
                    + ", ".join(str(o) for o in coverage["overused"]),
                    round_n,
                )
            else:
                # Nothing to nudge — clear any prior challenge flags.
                metadata = {k: v for k, v in metadata.items()
                            if k not in ("challenge_mode", "suggested_attack_types",
                                         "vulnerability_blind_spots", "diversify",
                                         "overused_attack_types")}

        session.current_round = round_n
        try:
            # Inner loop off → adapters receive no strategy hints (empty), so each
            # side runs purely on its own logic every round.
            result = await _run_round(
                session, round_n,
                red_hints=evolution_hints.get("red") if session.inner_loop_enabled else None,
                blue_hints=evolution_hints.get("blue") if session.inner_loop_enabled else None,
                metadata=metadata,
                conversation=conversation,
            )
            if result is None:
                loop_exit_reason = "user_stop"
                break
            verdict, round_hints = result
            # Carry judge evolution hints forward to the next round — only when
            # the inner loop is on (otherwise adapters stay hint-free).
            if session.inner_loop_enabled and round_hints:
                evolution_hints = round_hints
                # Surface the losing side's REAL judge-derived direction into the
                # thinking chat (Strategy diagnoses, Rewriter turns it into a move).
                loser = "blue" if verdict == "success" else "red"
                lh = round_hints.get(loser) or {}
                mut = (lh.get("suggested_mutation") or lh.get("mutation_type")
                       or lh.get("suggested_direction"))
                diag = lh.get("diagnosis") or lh.get("reason")
                if diag:
                    await _emit_reasoning(session.id, loser, "strategy", str(diag), round_n)
                if mut:
                    await _emit_reasoning(
                        session.id, loser, "rewriter",
                        f"Next move: shift toward {mut}", round_n)
            # Remember the per-round outcome so a windowed stop condition can ask
            # what the last N rounds looked like, not what the whole battle averaged.
            session._recent_outcomes.append(verdict == "success")
            if verdict == "success":
                session.red_wins += 1
                if session._last_winner == "red":
                    session._current_streak += 1
                else:
                    session._last_winner = "red"
                    session._current_streak = 1
            else:
                session.blue_wins += 1
                if session._last_winner == "blue":
                    session._current_streak += 1
                else:
                    session._last_winner = "blue"
                    session._current_streak = 1
            error_count = 0
            await _persist_session_progress(session)

            # ── User-defined stop conditions (evaluated after each round) ──
            stop_reason = _check_user_stop_conditions(session, round_n)
            if stop_reason:
                loop_exit_reason = stop_reason
                break

        except Exception as exc:
            error_count += 1
            _log.error("Round %d error (count=%d): %s", round_n, error_count, exc, exc_info=True)
            await publish_event(session.id, "adapter.error", {"round": round_n, "error": str(exc)})

            # Say WHICH kind of trouble this is, on the first occurrence rather than
            # after the threshold. A throttle clears itself and is not worth a person's
            # attention; an exhausted account or a rejected key will not clear no matter
            # how long the loop retries, and an unattended run can burn hours on it.
            # Only the terminal kinds are announced, so a hiccup does not cry wolf.
            import model_fault
            fault = model_fault.classify(exc)   # also read by the threshold branch below
            if not model_fault.is_recoverable(fault) and fault not in _announced_faults:
                _announced_faults.add(fault)
                await publish_event(session.id, "model.unavailable", {
                    "round": round_n,
                    "category": fault,
                    "recoverable": False,
                    "advice": model_fault.advice(fault),
                    "detail": str(exc)[:400],
                })
                _log.error("Round %d: %s — %s", round_n, fault, model_fault.advice(fault))

            if error_count >= settings.adapter_error_threshold:
                # Infinite mode is meant to run until the user presses STOP.
                # Transient adapter outages (e.g. a restart, a network blip, a
                # model rate-limit) must NOT terminate it. Instead, hold with a
                # backoff and keep retrying — the loop resumes automatically when
                # the adapters recover. Only a user STOP ends an infinite battle.
                if is_infinite_mode:
                    await publish_event(session.id, "battle.degraded", {
                        "round": round_n,
                        "reason": "adapters unreachable — holding and retrying (infinite mode never auto-ends)",
                        "consecutive_errors": error_count,
                        "error": str(exc),
                    })
                    backoff = 15.0
                    waited = 0.0
                    while waited < backoff:
                        if session.status == "stopped":
                            break
                        await asyncio.sleep(min(0.5, backoff - waited))
                        waited += 0.5
                    error_count = 0   # reset so we keep retrying indefinitely
                    if session.status == "stopped":
                        loop_exit_reason = "user_stop"
                        break
                    continue
                # Bounded modes: whether to end here depends on WHAT failed.
                #
                # A throttle or a brief outage is not a reason to abandon a run that has
                # already spent hours — the `both` leg of v6 died at round 36 because the
                # target's model was briefly unavailable for five rounds, and the other
                # sixty-four rounds were never collected. So a recoverable fault holds and
                # retries, the way infinite mode already does, for a bounded number of
                # holds. A terminal fault — an exhausted account, a rejected key, a model
                # the provider no longer has — ends the run immediately, because waiting
                # cannot fix it and every further round would fail the same way.
                if model_fault.is_recoverable(fault) and recoverable_holds < _MAX_RECOVERABLE_HOLDS:
                    recoverable_holds += 1
                    await publish_event(session.id, "battle.degraded", {
                        "round": round_n,
                        "reason": (f"{fault or 'transient fault'} — holding "
                                   f"{_RECOVERABLE_HOLD_SECONDS:.0f}s and retrying "
                                   f"(hold {recoverable_holds}/{_MAX_RECOVERABLE_HOLDS})"),
                        "consecutive_errors": error_count,
                        "recoverable": True,
                        "error": str(exc)[:300],
                    })
                    _log.warning("Round %d: holding %.0fs on a recoverable fault (%s), "
                                 "hold %d/%d", round_n, _RECOVERABLE_HOLD_SECONDS, fault,
                                 recoverable_holds, _MAX_RECOVERABLE_HOLDS)
                    waited = 0.0
                    while waited < _RECOVERABLE_HOLD_SECONDS:
                        if session.status == "stopped":
                            break
                        await asyncio.sleep(min(1.0, _RECOVERABLE_HOLD_SECONDS - waited))
                        waited += 1.0
                    if session.status == "stopped":
                        loop_exit_reason = "user_stop"
                        break
                    error_count = 0
                    continue

                session.status = "error"
                from trace_writer import get_pool
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE battle_sessions SET status=$1, ended_at=NOW() WHERE id=$2",
                        "error", session.id,
                    )
                await publish_event(session.id, "battle.complete", {
                    "exit_reason": "adapter_errors",
                    "reason": "too many adapter errors",
                    "winner": None,
                    "red_wins": session.red_wins, "blue_wins": session.blue_wins,
                })
                # Save whatever ran before the adapters gave out (fire-and-forget).
                asyncio.create_task(_autosave_report(session.id))
                return

        # Compute round delay
        delay = session.round_delay_seconds if session.round_delay_seconds > 0 else settings.battle_round_delay_seconds
        if is_infinite_mode:
            if round_n > 100:
                delay = max(delay, 30.0)
            elif round_n > 50:
                delay = max(delay, 10.0)
            elif round_n > 20:
                delay = max(delay, 3.0)
            else:
                delay = max(delay, 1.0)
        if delay > 0:
            # Sleep in 0.5s chunks so we can break out promptly on stop/pause
            elapsed = 0.0
            while elapsed < delay:
                if session.status == "stopped":
                    break
                await asyncio.sleep(min(0.5, delay - elapsed))
                elapsed += 0.5
        if session.status == "stopped":
            loop_exit_reason = "user_stop"
            break

    # Finalize — user STOP must not emit battle.complete (avoids double animations + wrong UI)
    stopped_by_user = session.status == "stopped"
    from trace_writer import get_pool
    pool = await get_pool()

    if stopped_by_user:
        # Distinguish a normal user STOP from an early stop we triggered because
        # a side disconnected — the report and UI should say which it was.
        stop_reason = session.stop_reason or "user_requested"
        await publish_event(session.id, "battle.stopped", {"reason": stop_reason})
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE battle_sessions
                SET status = $1,
                    red_wins = $2,
                    blue_wins = $3,
                    current_round = $4,
                    tokens_used = $5,
                    stop_reason = $6,
                    ended_at = COALESCE(ended_at, NOW())
                WHERE id = $7
                """,
                "stopped",
                session.red_wins,
                session.blue_wins,
                session.current_round,
                session.tokens_used,
                stop_reason,
                session.id,
            )
        # Persist the report to disk even on a STOP (user or disconnect),
        # fire-and-forget, so the completed rounds are always saved.
        asyncio.create_task(_autosave_report(session.id))
        return

    session.status = "complete"
    winner = "red" if session.red_wins > session.blue_wins else ("blue" if session.blue_wins > session.red_wins else "draw")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE battle_sessions
            SET status = $1,
                red_wins = $2,
                blue_wins = $3,
                current_round = $4,
                tokens_used = $5,
                ended_at = NOW()
            WHERE id = $6
            """,
            "complete",
            session.red_wins,
            session.blue_wins,
            session.current_round,
            session.tokens_used,
            session.id,
        )

    # Layer 3: post-battle meta-optimization (fire-and-forget; never blocks completion)
    asyncio.create_task(_run_post_battle_meta(session.id))

    # Final scores.
    # Skip entirely for internal benchmark battles — a fitness probe must never
    _total = session.red_wins + session.blue_wins
    _asr = round(session.red_wins / _total, 4) if _total > 0 else 0.0
    _dr  = round(session.blue_wins / _total, 4) if _total > 0 else 0.0

    await publish_event(session.id, "battle.complete", {
        "red_wins": session.red_wins,
        "blue_wins": session.blue_wins,
        "winner": winner,
        "exit_reason": loop_exit_reason,
    })

    # Persist the full report to disk now that the battle is complete
    # (fire-and-forget; survives browser refresh, no manual download needed).
    asyncio.create_task(_autosave_report(session.id))

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
                        f"{settings.code_improver_url}/v1/comprehend",
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
    reconnect window (and no ASIS rebuild was in progress). End the battle
    gracefully: mark it stopped with a reason and tell the UI why. The main
    loop's finalize block then persists the stopped state, emits `battle.stopped`
    (so the agents walk home), and saves the report over the rounds that
    completed. Because the interrupted round wrote no trace, the report carries
    no partial-round data."""
    session.status = "stopped"
    session.stop_reason = "adapter_disconnected"
    _log.warning(
        "Battle %s: role(s) %s unreachable past the reconnect window with no "
        "improvement in progress; stopping and finalizing over %d completed round(s).",
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

    A round only starts when:
      * neither adapter is being modified by ASIS (no `asis:rebuilding:{team}`
        flag), AND
      * all four roles — red adapter, blue adapter, target-ai and judge —
        answer /health.

    While a rebuild flag is set, the improvement is legitimately in progress, so
    we hold INDEFINITELY (both sides wait — nobody attacks or defends against a
    half-swapped opponent, and the improved side isn't rushed). The rebuild flag
    is cleared by ASIS only after the promoted container passes its health-gate,
    so once it clears the new code is already effective.

    A role that is unhealthy WITHOUT any rebuild in progress is an unexplained
    fault (a model/adapter fell over). That we bound by `down_grace`; if it does
    not recover, we PAUSE the battle and surface it (`battle.needs_attention`)
    rather than running a degraded round against a dead role. Respects user STOP.
    """
    from event_bus import get_redis
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
        rebuilding: list[str] = []
        try:
            r = await get_redis()
            for team in ("red", "blue"):
                if await r.get(f"asis:rebuilding:{team}"):
                    rebuilding.append(team)
        except Exception:
            pass
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

        if not rebuilding and not down:
            if announced:
                await publish_event(session.id, "battle.resumed_ready",
                                    {"message": "All roles in position — resuming."})
            return

        if not announced:
            await publish_event(session.id, "battle.holding", {
                "rebuilding": rebuilding, "unhealthy": down,
                "message": "Holding — a side is being improved or not yet in position; "
                           "both sides wait until all roles are ready and effective.",
            })
            announced = True

        # A rebuild in progress resets the unexplained-down timer: as long as ASIS
        # is legitimately swapping, transient health blips are expected and we wait.
        if rebuilding:
            down_waited = 0.0
        elif down:
            down_waited += 3.0
            # A role down with no improvement in progress is a real disconnect.
            # After the reconnect window, stop the battle and finalize over the
            # rounds that completed (see _finalize_disconnected) rather than
            # pausing forever or running a round against a dead role.
            if down_waited >= settings.disconnect_reconnect_window:
                await _finalize_disconnected(session, down)
                return
        await asyncio.sleep(3.0)


async def _improve_loser_and_wait(session: BattleSession, loser: str) -> None:
    """Turn-based improvement: pause the battle, improve the round's losing side,
    and BLOCK until code-improver reports the outcome (promoted + verified live,
    or rolled back / skipped leaving the baseline). Only then does the caller loop
    back — where `_await_adapters_in_position` re-confirms all roles are ready
    before the next round. The live adapter keeps serving the whole time (blue-
    green); the rebuild flag + the swap health-gate guarantee that if a new
    version is promoted it is effective before any further round runs.

    Never fatal: on timeout or transport error we surface it and return so the
    battle continues on the baseline rather than deadlocking. Respects user STOP.
    """
    if session.status == "stopped":
        return
    svc_id = session.red_service_id if loser == "red" else session.blue_service_id
    if not svc_id:
        return
    total = session.red_wins + session.blue_wins
    asr = round(session.red_wins / total, 4) if total else 0.0
    dr = round(session.blue_wins / total, 4) if total else 0.0
    winner = "blue" if loser == "red" else "red"

    await publish_event(session.id, "battle.improving", {
        "team": loser, "round": session.current_round,
        "message": f"Round over — {loser} lost. Pausing the battle to improve {loser}; "
                   "the next round waits until the new version is built and verified live.",
    })

    body = {
        "session_id": session.id, "team": loser, "adapter_id": svc_id,
        "role": "loser", "asr": asr, "dr": dr,
        "total_rounds": total, "winner": winner,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.improve_sync_timeout) as c:
            r = await c.post(f"{settings.code_improver_url}/v1/improve-sync", json=body)
        outcome = r.json() if r.status_code < 300 else {"status": "error",
                                                        "reason": f"HTTP {r.status_code}"}
    except Exception as exc:
        outcome = {"status": "error", "reason": str(exc)[:200], "verified_live": False}

    await publish_event(session.id, "battle.improved", {
        "team": loser, "round": session.current_round,
        "status": outcome.get("status"),
        "gen": outcome.get("gen"),
        "verified_live": outcome.get("verified_live", False),
        "reason": outcome.get("reason", ""),
        "message": (
            f"{loser} improvement: {outcome.get('status')}"
            + (f" (gen {outcome.get('gen')}, verified live)" if outcome.get("verified_live")
               else f" — baseline kept ({outcome.get('reason','')})")
        ),
    })


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
) -> dict:
    """Passed to red/blue adapters so users can steer goals via .env (not code).
    Per-session overrides (set at battle creation) take precedence over .env globals.
    """
    meta: dict = {
        "arena_target_context": (session.target_context if session and session.target_context
                                 else settings.arena_target_context),
        "red_team_objective":   (session.red_team_objective if session and session.red_team_objective
                                 else settings.red_team_objective),
        "blue_team_objective":  (session.blue_team_objective if session and session.blue_team_objective
                                 else settings.blue_team_objective),
    }
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


async def _call_red(
    service_id: str,
    session_id: str,
    round_n: int,
    evolution_hints: dict | None = None,
    metadata: dict | None = None,
    conversation: list[dict] | None = None,
) -> dict:
    registry = get_registry()
    svc = registry[service_id]
    headers = {"Authorization": f"Bearer {svc.token}"} if svc.token else {}

    async def _do():
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{svc.url}/v1/generate-attack", headers=headers, json={
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
) -> dict:
    registry = get_registry()
    svc = registry[service_id]
    headers = {"Authorization": f"Bearer {svc.token}"} if svc.token else {}

    async def _do():
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{svc.url}/v1/evaluate-defense", headers=headers, json={
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
) -> dict[str, object]:
    """POST /v1/filter-output — falls back to passthrough when missing or unreachable."""
    registry = get_registry()
    svc = registry[service_id]
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
            r = await client.post(f"{svc.url}/v1/filter-output", headers=headers, json=body)
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


async def _call_target_ai(session_id: str, message: str) -> str:
    async def _do():
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{settings.target_ai_url}/chat",
                                  json={"session_id": session_id, "message": message})
            r.raise_for_status()
            return r.json()["response"]

    return await _adapter_retry(_do)


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

    attack = await _call_red(session.red_service_id, session.id, round_n, red_hints,
                             metadata, conversation=conversation or [])
    # If the attacker's model refused to produce an attack, its "payload" is a
    # refusal string, not a genuine attack. Give the adapter one more chance
    # (adapters often vary technique per call), then surface the outcome
    # truthfully rather than parading a refusal as a real attack.
    attacker_refused = _looks_like_refusal(attack.get("attack_payload", ""))
    if attacker_refused and session.status != "stopped":
        retry = await _call_red(session.red_service_id, session.id, round_n, red_hints,
                                metadata, conversation=conversation or [])
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
            blue_hints, metadata,
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
    raw_target_response = await _call_target_ai(session.id, payload_to_target)
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
        harm_score=harm_score,
        raw_harm_score=raw_harm_score,
        judge_reasoning=judge_reason,
        attacker_refused=bool(attack.get("attacker_refused", False)),
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

    return verdict, new_evolution_hints


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

    asr = session.red_wins / total
    dr  = session.blue_wins / total

    if session.target_asr is not None and session.target_asr > 0 and asr >= session.target_asr:
        return f"target_asr_reached ({asr:.2%} >= {session.target_asr:.2%})"
    if session.target_dr is not None and session.target_dr > 0 and dr >= session.target_dr:
        return f"target_dr_reached ({dr:.2%} >= {session.target_dr:.2%})"
    if session.target_win_streak is not None and session.target_win_streak > 0 \
            and session._current_streak >= session.target_win_streak:
        return (
            f"win_streak_reached ({session._last_winner} "
            f"streak={session._current_streak} >= {session.target_win_streak})"
        )
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

    # Load any existing strategy records for this session (useful on resume).
    recent_strategies = await _load_recent_strategies(session.id)
    metadata = _arena_adapter_metadata(session=session, recent_strategies=recent_strategies or None)

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
                mut = lh.get("suggested_mutation") or lh.get("mutation_type")
                diag = lh.get("diagnosis") or lh.get("reason")
                if diag:
                    await _emit_reasoning(session.id, loser, "strategy", str(diag), round_n)
                if mut:
                    await _emit_reasoning(
                        session.id, loser, "rewriter",
                        f"Next move: shift toward {mut}", round_n)
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

            # ── Turn-based improvement: the round's loser improves now, and the
            # battle holds until the new version is verified live before the next
            # round. Skipped for benchmark probes and when disabled. Skipped on the
            # final bounded round (no next round would benefit).
            if (
                session.outer_loop_enabled
                and settings.improvement_per_round
                and not session.is_benchmark
                and session.status not in ("stopped",)
                and (is_infinite_mode
                     or session.max_rounds is None
                     or session.max_rounds <= 0
                     or round_n < session.max_rounds)
            ):
                round_loser = "blue" if verdict == "success" else "red"
                await _improve_loser_and_wait(session, round_loser)
                if session.status == "stopped":
                    loop_exit_reason = "user_stop"
                    break
        except Exception as exc:
            error_count += 1
            _log.error("Round %d error (count=%d): %s", round_n, error_count, exc, exc_info=True)
            await publish_event(session.id, "adapter.error", {"round": round_n, "error": str(exc)})
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
                # Deathmatch / bounded modes: terminal error after threshold.
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

    # ASIS: compute final scores and emit improvement signal.
    # Skip entirely for internal benchmark battles — a fitness probe must never
    # re-trigger ASIS (which would run more benchmarks → unbounded feedback loop).
    _total = session.red_wins + session.blue_wins
    _asr = round(session.red_wins / _total, 4) if _total > 0 else 0.0
    _dr  = round(session.blue_wins / _total, 4) if _total > 0 else 0.0
    # In turn-based mode the loser already improved after each round, so the
    # end-of-battle trigger would double-improve — skip it.
    if (not session.is_benchmark
            and session.outer_loop_enabled
            and not settings.improvement_per_round):
        await publish_event(session.id, "improvement.triggered", {
            "asr":             _asr,
            "dr":              _dr,
            "red_wins":        session.red_wins,
            "blue_wins":       session.blue_wins,
            "total_rounds":    _total,
            "red_service_id":  session.red_service_id,
            "blue_service_id": session.blue_service_id,
            "winner":          winner,
        })

    await publish_event(session.id, "battle.complete", {
        "red_wins": session.red_wins,
        "blue_wins": session.blue_wins,
        "winner": winner,
        "exit_reason": loop_exit_reason,
    })

    # Persist the full report to disk now that the battle is complete
    # (fire-and-forget; survives browser refresh, no manual download needed).
    # Benchmark probes are not real battles — no report.
    if not session.is_benchmark:
        asyncio.create_task(_autosave_report(session.id))

"""ASIS code-improver — agent-driven improvement of real target/ projects."""
import litellm_safe  # noqa: F401  — monkey-patch Gemini safety_settings
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException

from asap_compliance import ComplianceResult, check_compliance, _read_capabilities
from benchmark import run_benchmark, _compute_pss
from candidate_select import select_best_candidate
from config import settings
from generation_store import (
    get_active_gen,
    get_gen_history,
    insert_candidate_gen,
    insert_gen0,
    mark_validated,
    promote_gen,
    rollback_gen,
)
from meta_agent import run_agent
from patch_executor import (
    compute_diff,
    deploy_and_canary,
    docker_rebuild_and_restart,
    prepare_work_copy,
    read_gen0_snapshot,
    restore_project,
    rollback_to_gen0,
    snapshot_project,
    write_gen0_backup,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None
_queue: asyncio.Queue = asyncio.Queue()
_CURSOR_KEY = "asis:global_stream_cursor"
_WIN_STREAK_KEY = "asis:win_streak"  # hash: {adapter_id: streak_count}

# Per-team improvement lock: the synchronous (turn-based) improve path and the
# async queue worker must never build candidates for the SAME team at once, or
# two candidate containers/images would clash. Different teams may still overlap.
_improve_locks: dict[str, asyncio.Lock] = {"red": asyncio.Lock(), "blue": asyncio.Lock()}


# Host paths used as docker build context (matches docker-compose bind-mount source).
# REQUIRED via .env — no defaults. The user picks which red/blue project to plug in.
_RED_HOST_PATH  = os.environ.get("RED_ADAPTER_PATH_HOST",  "")
_BLUE_HOST_PATH = os.environ.get("BLUE_ADAPTER_PATH_HOST", "")
if not _RED_HOST_PATH or not _BLUE_HOST_PATH:
    log.warning(
        "RED_ADAPTER_PATH_HOST / BLUE_ADAPTER_PATH_HOST not set — "
        "Docker build context for ASIS rebuilds will fall back to in-container path."
    )


# Adapter "kinds" the platform understands how to self-improve. A project may
# declare its kind in /health capabilities (`kind`). An EXPLICIT kind outside
# these sets is refused — we don't risk mis-editing a project whose shape we
# don't understand. A project that declares no kind still passes (its eligibility
# is already proven by the live compliance smoke test), keeping the gate
# backward-compatible while closing the door on declared-but-unknown shapes.
_KNOWN_RED_KINDS = {
    "single_prompt", "multi_turn", "multi_agent_chain", "tool_use",
    "layered_composition", "prompt_injection", "jailbreak",
}
_KNOWN_BLUE_KINDS = {
    "input_guard", "output_guard", "intent_classifier",
    "output_filter", "classifier",
}


def _validate_adapter_kind(team: str, caps: dict) -> tuple[bool, str]:
    """Reject an adapter that DECLARES a kind we don't recognise. Absent kind →
    allowed (compliance smoke already proved it works)."""
    known = _KNOWN_RED_KINDS if team == "red" else _KNOWN_BLUE_KINDS
    declared = str((caps or {}).get("kind") or "").strip().lower()
    if declared and declared not in known:
        return False, (f"declares unknown adapter kind '{declared}'; "
                       f"known {team} kinds: {sorted(known)} — refusing to self-improve")
    return True, ""


async def _publish_asis_event(session_id: str, event_type: str, data: dict) -> None:
    try:
        r = await _get_redis()
        payload = json.dumps({
            "event_type": event_type,
            "session_id": session_id,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        })
        await r.xadd("arena:events:global", {"payload": payload}, maxlen=5000)
    except Exception as exc:
        log.warning("Failed to publish ASIS event %s: %s", event_type, exc)


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.postgres_uri, min_size=1, max_size=4)
    return _pool


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # socket_timeout MUST exceed the XREAD block (5000ms) used by the ASIS
        # subscriber. If it equals/undercuts the block, the socket read deadline
        # races the server-side block and raises TimeoutError on every idle poll,
        # so the subscriber never consumes improvement.triggered events and ASIS
        # never fires. Keepalive + health checks keep the long-lived client sane.
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=15,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _redis


async def _set_rebuild_lock(team: str, on: bool) -> None:
    """Mark a team's adapter as mid-rebuild so arena-core won't start a user
    battle against a container that is briefly being stopped/recreated. Short TTL
    is a safety net in case the clear is missed. Best-effort — never fatal."""
    try:
        r = await _get_redis()
        key = f"asis:rebuilding:{team}"
        if on:
            await r.set(key, "1", ex=180)
        else:
            await r.delete(key)
    except Exception as exc:
        log.warning("rebuild lock (%s=%s) failed: %s", team, on, exc)


async def _register_candidate_service(team: str, url: str) -> str | None:
    """Register the blue-green candidate container as a temporary arena service so
    it can be benchmarked in parallel without touching the live adapter. Returns
    the service id (or None on failure)."""
    import httpx
    sid = f"cand-{team}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{settings.arena_core_url}/api/services",
                json={"id": sid, "name": f"Candidate {team}", "url": url, "type": team},
            )
            if r.status_code < 300:
                return sid
            log.warning("candidate service register failed: HTTP %s %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("candidate service register error: %s", exc)
    return None


async def _deregister_service(sid: str | None) -> None:
    if not sid:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.delete(f"{settings.arena_core_url}/api/services/{sid}")
    except Exception as exc:
        log.warning("candidate service deregister error: %s", exc)


async def _bump_win_streak(adapter_id: str) -> int:
    r = await _get_redis()
    return int(await r.hincrby(_WIN_STREAK_KEY, adapter_id, 1))


async def _reset_win_streak(adapter_id: str) -> None:
    r = await _get_redis()
    await r.hset(_WIN_STREAK_KEY, adapter_id, 0)


async def _score_candidate(diff: str, failure_ctx: str, team: str) -> float:
    """Cheap 'promise' score for a candidate diff — no container build. Uses the
    verifier over the recent-failure context plus the diff, so best-of-N can
    pick the most promising edit before the expensive benchmark. Returns 0.0
    when the verifier is unavailable (candidate still competes, just unranked)."""
    from verifier import score_text, parse_criteria
    if not diff:
        return 0.0
    spec = (settings.verifier_red_criteria if team == "red"
            else settings.verifier_blue_criteria)
    base = parse_criteria(spec)
    crit = [("promise", "how likely this code change improves the team's "
             "objective given the described recent failures")]
    if base:
        crit.append(base[0])
    model = getattr(settings, "verifier_model", "") or getattr(settings, "meta_agent_model", "")
    if not model:
        return 0.0
    s = await score_text(
        f"RECENT FAILURES:\n{failure_ctx}\n\nCANDIDATE CODE DIFF:\n{diff[:6000]}",
        crit, model=model, base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        top_logprobs=getattr(settings, "verifier_top_logprobs", 10),
        scale=getattr(settings, "verifier_scale", 10))
    return 0.0 if s is None else float(s)


async def _run_improvement(
    session_id: str,
    team: str,
    adapter_id: str,
    role: str,           # "loser" or "winner_after_streak"
    asr: float,
    dr: float,
    total_rounds: int,
    winner: str,
) -> dict:
    """Run one ASIS improvement. Returns a structured outcome so a synchronous
    caller (turn-based battle loop) can decide whether to resume:
      {"status": "promoted"|"rolled_back"|"no_change"|"skipped",
       "team", "gen", "reason", "verified_live": bool}
    'verified_live' is True only when a promoted container passed the health-gate,
    i.e. the new code is actually serving under the adapter's network name."""
    log.info(
        "ASIS: session=%s team=%s role=%s asr=%.1f%% dr=%.1f%%",
        session_id, team, role, asr * 100, dr * 100,
    )

    # Refuse to manage a team that failed ASAP compliance — log + skip
    cr = _compliance.get(team)
    if cr is None or not cr.passed:
        log.warning(
            "ASIS REFUSED: %s team is not ASAP-compliant. Run /v1/compliance/recheck "
            "after fixing your project. Last result: %s",
            team, cr.summary() if cr else "(not yet checked)",
        )
        return {"status": "skipped", "team": team, "reason": "not_asap_compliant",
                "verified_live": False}

    pool = await _get_pool()

    # Pick project paths. `project_in_container` is the PRISTINE original,
    # mounted read-only — never edited. `work_dir` is a writable copy the agent
    # edits; the candidate image is built from it. This guarantees a plugged-in
    # project's source is never mutated by the platform.
    if team == "red":
        project_in_container = settings.red_project_path
        work_dir             = settings.red_work_path
        project_on_host      = _RED_HOST_PATH
        image_tag            = settings.red_image_tag
        container_name       = settings.red_container_name
        adapter_url          = settings.red_adapter_direct_url
    else:
        project_in_container = settings.blue_project_path
        work_dir             = settings.blue_work_path
        project_on_host      = _BLUE_HOST_PATH
        image_tag            = settings.blue_image_tag
        container_name       = settings.blue_container_name
        adapter_url          = settings.blue_adapter_direct_url

    if not os.path.isdir(project_in_container):
        log.error("Project not mounted: %s", project_in_container)
        return {"status": "skipped", "team": team, "reason": "project_not_mounted",
                "verified_live": False}

    # ── Eligibility gates (consent + known kind) ───────────────────────────
    # Fetch the adapter's declared capabilities once and reuse for the agent.
    try:
        caps = await _read_capabilities(adapter_url)
    except Exception:
        caps = {}

    # Consent gate (opt-out): a project may refuse code-level self-improvement
    # by declaring allow_self_improvement=false in its /health capabilities.
    # We respect that and never touch its code. (Absent → operator default.)
    if caps.get("allow_self_improvement") is False:
        log.info("ASIS SKIP: %s project declared allow_self_improvement=false — "
                 "respecting opt-out, not modifying its code.", team)
        return {"status": "skipped", "team": team, "reason": "opt_out",
                "verified_live": False}

    # Kind gate (reject unknown): a project may declare its adapter `kind`. If it
    # declares one we don't recognise, refuse to self-improve it rather than
    # risk mis-editing a project whose shape we don't understand.
    ok_kind, kind_msg = _validate_adapter_kind(team, caps)
    if not ok_kind:
        log.warning("ASIS SKIP: %s project — %s", team, kind_msg)
        return {"status": "skipped", "team": team, "reason": f"unknown_kind: {kind_msg}",
                "verified_live": False}

    # gen_0 backup (once per adapter_id)
    write_gen0_backup(project_in_container, adapter_id)
    # Stamp gen_0 with the triggering battle's measured fitness so the report
    # has a true 'before' datapoint (original adapter performance).
    gen0_pss = await _compute_pss(session_id, team)
    await insert_gen0(adapter_id, team, benchmark_asr=asr, benchmark_dr=dr,
                      benchmark_pss=gen0_pss, trigger_session_id=session_id)

    active = await get_active_gen(adapter_id)
    parent_gen_id = str(active["id"]) if active else None
    current_gen   = active["gen_number"] if active else 0
    baseline_asr  = float(active.get("benchmark_asr") or asr) if active else asr
    baseline_dr   = float(active.get("benchmark_dr")  or dr)  if active else dr
    baseline_pss  = float(active.get("benchmark_pss") or 0.0) if active else 0.0

    await _publish_asis_event(session_id, "asis.improving", {
        "team": team, "adapter_id": adapter_id, "asr": asr, "dr": dr,
        "role": role, "gen": current_gen + 1,
        "message": f"ASIS analyzing {team} project ({role}) — agent exploring repo...",
    })

    # Copy the pristine original into a writable staging dir. From here on the
    # agent, snapshot, diff and image build all operate on `work_dir` — the
    # original at `project_in_container` (mounted read-only) is never written.
    prepare_work_copy(project_in_container, work_dir)

    # Snapshot the copy before the agent runs (used for in-job restore on a
    # failed build/canary).
    snapshot = snapshot_project(work_dir)

    # caps already fetched above (eligibility gates); reused for the agent's
    # entry_files hint.

    # ── Agent loop (best-of-N candidate exploration) ───────────────────────
    # Generate up to best_of_n candidate edits, cheaply score each with the
    # verifier, and keep the most promising one. best_of_n=1 is a single agent
    # pass (the previous behaviour). Only the winner enters the expensive
    # build -> canary -> benchmark -> promote/rollback path below.
    gen0_baseline = read_gen0_snapshot(adapter_id) or snapshot
    run_kwargs = dict(
        pool=pool, session_id=session_id, team=team,
        project_root=work_dir,
        role=role, asr=asr, dr=dr,
        total_rounds=total_rounds, winner=winner,
        caps=caps, adapter_id=adapter_id,
    )
    failure_ctx = (f"team={team} role={role} asr={asr:.2f} dr={dr:.2f} "
                   f"rounds={total_rounds} winner={winner}")
    changed, paths = await select_best_candidate(
        n=int(getattr(settings, "best_of_n", 1)),
        run_agent=run_agent, run_kwargs=run_kwargs,
        work_dir=work_dir, team=team, failure_ctx=failure_ctx,
        project_in_container=project_in_container, gen0_baseline=gen0_baseline,
        prepare_work_copy=prepare_work_copy, compute_diff=compute_diff,
        snapshot_project=snapshot_project, restore_project=restore_project,
        score_fn=_score_candidate,
    )

    if not changed:
        await _publish_asis_event(session_id, "asis.no_change", {
            "team": team, "adapter_id": adapter_id,
            "message": f"ASIS: agent returned no changes for {team}",
        })
        return {"status": "no_change", "team": team, "reason": "agent_no_changes",
                "verified_live": False}

    # Compute diff against gen_0 (cumulative-from-original) — user explicitly
    # wants final state to be comparable against the ORIGINAL project, not
    # against the immediate previous generation.
    diff_text = compute_diff(work_dir, gen0_baseline)

    gen_id = await insert_candidate_gen(
        adapter_id, team, current_gen + 1,
        parent_gen_id, diff_text, session_id,
    )

    # Real phase signal: agent finished editing, now building+canary the image.
    # Drives the live evolver visual (no faked timers — front/back stay in sync).
    await _publish_asis_event(session_id, "asis.phase", {
        "team": team, "adapter_id": adapter_id, "gen": current_gen + 1,
        "phase": "building",
        "message": f"ASIS: edits done ({len(paths)} file(s)) — rebuilding + canary…",
    })

    # ── Blue-green deploy: build a CANDIDATE and run it ALONGSIDE the live ──
    # adapter, which keeps serving untouched. A failed generation causes ZERO
    # disruption (we just tear the candidate down); only a PROMOTE briefly swaps.
    from patch_executor import deploy_candidate, swap_candidate_to_live, teardown_candidate, asap_canary
    loop = asyncio.get_event_loop()
    ok, err, cand = await loop.run_in_executor(
        None, deploy_candidate, work_dir, image_tag, container_name)
    if ok:
        # Canary the candidate in parallel — the live adapter is never involved.
        ok, err = await asap_canary(cand["url"], team)
    await mark_validated(gen_id, ast_valid=ok, canary_passed=ok)
    if not ok:
        await loop.run_in_executor(None, teardown_candidate, cand)
        await rollback_gen(gen_id, f"deploy/canary: {err}")
        log.warning("Gen %d rejected (candidate deploy/canary): %s", current_gen + 1, err)
        await _publish_asis_event(session_id, "asis.gen_rolled_back", {
            "team": team, "adapter_id": adapter_id,
            "gen_number": current_gen + 1,
            "message": f"ASIS: gen_{current_gen + 1} rejected ({err[:80]}) — live adapter untouched",
        })
        return {"status": "rolled_back", "team": team, "gen": current_gen + 1,
                "reason": f"deploy_canary: {err[:120]}", "verified_live": False}

    # Register the candidate as a temporary service and benchmark IT (the live
    # adapter and the ongoing user experience are unaffected).
    cand_service_id = await _register_candidate_service(team, cand["url"])
    bench_target = cand_service_id or adapter_id

    # Real phase signal: candidate is up + canary passed, now benchmarking it.
    await _publish_asis_event(session_id, "asis.phase", {
        "team": team, "adapter_id": adapter_id, "gen": current_gen + 1,
        "phase": "benchmarking",
        "message": f"ASIS: candidate canary passed — benchmarking gen_{current_gen + 1} over multiple seeds…",
    })

    # ── Benchmark (multi-seed: mean ± stddev) ──────────────────────────────
    try:
        bench_asr, bench_dr, bench_sid, bench_pss, bench_pss_std = await run_benchmark(bench_target, team)
    finally:
        await _deregister_service(cand_service_id)

    # PSS (continuous) is the PRIMARY fitness signal — binary ASR/DR is often
    # stuck at 0/100 and gives ASIS no gradient to climb. Binary acts only as a
    # tiebreaker when PSS is flat. A strict PSS climb always promotes; a PSS
    # regression beyond the threshold always rolls back.
    margin       = settings.regression_threshold
    binary_now   = bench_asr   if team == "red" else bench_dr
    binary_base  = baseline_asr if team == "red" else baseline_dr
    pss_improved    = bench_pss > baseline_pss + 0.01
    pss_regressed   = bench_pss < baseline_pss * (1 - margin) - 0.01
    # A TIE is not an improvement. Require a STRICT binary gain as the flat-PSS
    # tiebreaker — otherwise, when the fitness signal is pinned (e.g. PSS=0 and
    # binary=0 against a strong opponent), every no-op edit ties the baseline and
    # gets promoted, churning unvalidated changes. Promote only on a real gain.
    binary_improved = binary_now > binary_base
    improved        = pss_improved or (not pss_regressed and binary_improved)
    score_info = (
        f"pss {bench_pss:.3f} vs {baseline_pss:.3f} | "
        f"{'asr' if team == 'red' else 'dr'} {binary_now:.1%} vs {binary_base:.1%}"
    )

    if improved:
        # PROMOTE — swap the candidate in as the new live adapter. This is the
        # only moment the live adapter is briefly replaced; guard with the lock.
        await _set_rebuild_lock(team, True)
        try:
            s_ok, s_err = await loop.run_in_executor(
                None, swap_candidate_to_live, image_tag, container_name, cand)
        finally:
            await _set_rebuild_lock(team, False)
        if not s_ok:
            await loop.run_in_executor(None, teardown_candidate, cand)
            await rollback_gen(gen_id, f"promote swap failed: {s_err}")
            log.error("Gen %d promote swap failed team=%s: %s", current_gen + 1, team, s_err)
            await _publish_asis_event(session_id, "asis.gen_rolled_back", {
                "team": team, "adapter_id": adapter_id, "gen_number": current_gen + 1,
                "message": f"ASIS: gen_{current_gen + 1} swap failed — live adapter kept ({s_err[:60]})",
            })
            return {"status": "rolled_back", "team": team, "gen": current_gen + 1,
                    "reason": f"promote_swap: {s_err[:120]}", "verified_live": False}
        await promote_gen(gen_id, adapter_id, bench_asr, bench_dr, bench_sid or "", bench_pss, bench_pss_std)
        log.info("Gen %d PROMOTED team=%s: %s (files: %s)", current_gen + 1, team, score_info, paths)
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM narrative_cache WHERE session_id=$1", session_id)
        await _publish_asis_event(session_id, "asis.gen_promoted", {
            "team": team, "adapter_id": adapter_id,
            "gen_number": current_gen + 1,
            "benchmark_asr": bench_asr, "benchmark_dr": bench_dr,
            "benchmark_pss": bench_pss,
            "changed_paths": paths,
            "message": f"ASIS: gen_{current_gen + 1} PROMOTED — {team} improved! ({score_info})",
        })
        # swap_candidate_to_live already health-gated the promoted container, so
        # the new code is verified live under the adapter's network name.
        return {"status": "promoted", "team": team, "gen": current_gen + 1,
                "reason": score_info, "verified_live": True}
    else:
        # ROLLBACK — the candidate never became live, so just discard it. The
        # live adapter was never touched → zero disruption, no rebuild, no lock.
        await loop.run_in_executor(None, teardown_candidate, cand)
        await rollback_gen(gen_id, f"regression: {score_info}")
        log.warning("Gen %d rolled back (regression) team=%s: %s — live adapter untouched",
                    current_gen + 1, team, score_info)
        await _publish_asis_event(session_id, "asis.gen_rolled_back", {
            "team": team, "adapter_id": adapter_id,
            "gen_number": current_gen + 1,
            "message": f"ASIS: gen_{current_gen + 1} rolled back ({score_info}) — baseline kept, no downtime",
        })
        return {"status": "rolled_back", "team": team, "gen": current_gen + 1,
                "reason": f"regression: {score_info}", "verified_live": False}


async def _worker() -> None:
    while True:
        job = await _queue.get()
        try:
            team = job.get("team", "")
            lock = _improve_locks.get(team)
            if lock:
                async with lock:
                    await _run_improvement(**job)
            else:
                await _run_improvement(**job)
        except Exception as exc:
            log.error("Improvement job failed: %s", exc, exc_info=True)
        finally:
            _queue.task_done()


async def _decide_jobs(payload_data: dict, sid: str) -> list[dict]:
    """Decide which team(s) to improve based on battle outcome.

    Policy:
    - LOSER (red ASR < threshold OR blue DR < threshold): strict trigger
    - WINNER (the other side): trigger only after winner_streak_threshold consecutive wins
    """
    asr  = float(payload_data.get("asr", 1.0))
    dr   = float(payload_data.get("dr", 1.0))
    n    = int(payload_data.get("total_rounds", 0))
    rsvc = payload_data.get("red_service_id", "")
    bsvc = payload_data.get("blue_service_id", "")
    winner = payload_data.get("winner", "")

    if n < 3:
        return []

    jobs: list[dict] = []

    # Red improvement
    red_losing = asr < settings.losing_red_asr_threshold and rsvc
    if red_losing:
        await _reset_win_streak(rsvc)
        jobs.append(dict(
            session_id=sid, team="red", adapter_id=rsvc, role="loser",
            asr=asr, dr=dr, total_rounds=n, winner=winner,
        ))
    elif rsvc and winner == "red":
        streak = await _bump_win_streak(rsvc)
        log.info("red %s win streak: %d", rsvc, streak)
        if streak >= settings.winner_streak_threshold:
            await _reset_win_streak(rsvc)
            jobs.append(dict(
                session_id=sid, team="red", adapter_id=rsvc, role="winner_after_streak",
                asr=asr, dr=dr, total_rounds=n, winner=winner,
            ))

    # Blue improvement
    blue_losing = dr < settings.losing_blue_dr_threshold and bsvc
    if blue_losing:
        await _reset_win_streak(bsvc)
        jobs.append(dict(
            session_id=sid, team="blue", adapter_id=bsvc, role="loser",
            asr=asr, dr=dr, total_rounds=n, winner=winner,
        ))
    elif bsvc and winner == "blue":
        streak = await _bump_win_streak(bsvc)
        log.info("blue %s win streak: %d", bsvc, streak)
        if streak >= settings.winner_streak_threshold:
            await _reset_win_streak(bsvc)
            jobs.append(dict(
                session_id=sid, team="blue", adapter_id=bsvc, role="winner_after_streak",
                asr=asr, dr=dr, total_rounds=n, winner=winner,
            ))

    return jobs


async def _subscriber() -> None:
    r = await _get_redis()
    last_id = await r.get(_CURSOR_KEY) or "$"
    log.info("ASIS subscriber started on arena:events:global from %s", last_id)
    while True:
        try:
            results = await r.xread({"arena:events:global": last_id}, count=10, block=5000)
            if not results:
                continue
            for _, messages in results:
                for msg_id, fields in messages:
                    last_id = msg_id
                    await r.set(_CURSOR_KEY, last_id)
                    try:
                        payload = json.loads(fields["payload"])
                    except Exception:
                        continue
                    if payload.get("event_type") != "improvement.triggered":
                        continue
                    sid  = payload.get("session_id", "")
                    jobs = await _decide_jobs(payload.get("data", {}), sid)
                    for j in jobs:
                        await _queue.put(j)
        except Exception as exc:
            log.error("Subscriber error: %s", exc)
            await asyncio.sleep(5.0)


# ── ASAP compliance tracking ──────────────────────────────────────────────────
# Populated at startup. If a team's check fails, ASIS will REFUSE all
# improvement jobs for that team (logged as warning each time).
_compliance: dict[str, ComplianceResult] = {}


def _warn_if_default(team: str, host_path: str, in_container_path: str) -> None:
    """Log a clear notice when the operator is running with the platform's
    built-in default project (i.e. they haven't pointed RED_ADAPTER_PATH /
    BLUE_ADAPTER_PATH at their own project)."""
    DEFAULT_MARKERS = ("acea-default-red", "acea-default-blue")
    if any(marker in (host_path or "") for marker in DEFAULT_MARKERS):
        log.warning(
            "═══════════════════════════════════════════════════════════════\n"
            "  USING PLATFORM DEFAULT for %s team: %s\n"
            "  This is fine for demos. To plug in your OWN project, set\n"
            "    %s_ADAPTER_PATH=/absolute/path/to/your-%s-project\n"
            "  in .env (your project needs an arena_adapter.py + Dockerfile).\n"
            "═══════════════════════════════════════════════════════════════",
            team.upper(), host_path, team.upper(), team,
        )
    # Also check by inspecting the bind-mounted project for the marker file
    try:
        manifest = os.path.join(in_container_path, "pyproject.arena.toml")
        if os.path.isfile(manifest):
            with open(manifest) as fh:
                if "default = true" in fh.read():
                    log.warning(
                        "%s team's project declares itself an ACEA built-in default.",
                        team.upper(),
                    )
    except Exception:
        pass


async def _run_startup_compliance() -> None:
    """Run ASAP compliance checks for red + blue. Retries on cold start because
    the target containers may still be booting when code-improver comes up.
    """
    # Warn if using the bundled defaults
    _warn_if_default("red",  _RED_HOST_PATH,  settings.red_project_path)
    _warn_if_default("blue", _BLUE_HOST_PATH, settings.blue_project_path)

    for team, path, url in [
        ("red",  settings.red_project_path,  settings.red_adapter_direct_url),
        ("blue", settings.blue_project_path, settings.blue_adapter_direct_url),
    ]:
        result: ComplianceResult | None = None
        for attempt in range(6):
            result = await check_compliance(team, path, url)
            if result.passed:
                break
            log.info(
                "Compliance attempt %d/6 for %s failed; waiting for adapter to boot...",
                attempt + 1, team,
            )
            await asyncio.sleep(5.0)
        _compliance[team] = result  # type: ignore[assignment]
        log.info("\n%s", result.summary())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run compliance checks BEFORE accepting any improvement jobs
    asyncio.create_task(_run_startup_compliance(), name="asis-compliance-bootstrap")
    asyncio.create_task(_subscriber(), name="asis-subscriber")
    asyncio.create_task(_worker(),     name="asis-worker")
    yield
    global _pool, _redis
    if _pool:  await _pool.close()
    if _redis: await _redis.aclose()


app = FastAPI(title="ASIS Code Improver", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "code-improver",
        "queue_depth": _queue.qsize(),
        "compliance": {
            team: {"passed": r.passed, "checks": [
                {"name": n, "passed": ok, "detail": d, "advisory": adv}
                for (n, ok, d, adv) in r.checks
            ]}
            for team, r in _compliance.items()
        },
    }


@app.post("/v1/compliance/recheck")
async def recheck_compliance():
    """Manually re-run the ASAP compliance check (e.g. after the user fixes
    their project and restarts the team container)."""
    await _run_startup_compliance()
    return {team: r.passed for team, r in _compliance.items()}


@app.get("/v1/generations/{adapter_id}")
async def list_generations(adapter_id: str):
    return {"adapter_id": adapter_id, "generations": await get_gen_history(adapter_id)}


@app.post("/v1/comprehend")
async def comprehend(team: str):
    """Pre-battle READ-ONLY analysis of one side's plugged-in project. Returns a
    strategy profile (architecture summary + advisory strategy). Never modifies
    the project. `team` is 'red' or 'blue'."""
    from comprehend import comprehend_project
    if team == "red":
        project_path, adapter_url = settings.red_project_path, settings.red_adapter_direct_url
        recon_model = settings.red_recon_model or settings.meta_agent_model
    elif team == "blue":
        project_path, adapter_url = settings.blue_project_path, settings.blue_adapter_direct_url
        recon_model = settings.blue_recon_model or settings.meta_agent_model
    else:
        raise HTTPException(400, "team must be 'red' or 'blue'")
    return await comprehend_project(
        team=team,
        project_root=project_path,
        adapter_url=adapter_url,
        model=recon_model,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
    )


@app.post("/v1/improve-sync")
async def improve_sync(body: dict):
    """Run ONE improvement synchronously and return its outcome. Used by the
    turn-based battle loop: the loop calls this after a round, blocks until it
    returns, and only resumes once the outcome says the new version is verified
    live (or the generation was rolled back / skipped, leaving the baseline).

    Body: {session_id, team, adapter_id, role, asr, dr, total_rounds, winner}.
    The team lock ensures this never overlaps the async worker for the same team."""
    team = body.get("team", "")
    if team not in ("red", "blue"):
        raise HTTPException(400, "team must be 'red' or 'blue'")
    job = dict(
        session_id=body.get("session_id", ""),
        team=team,
        adapter_id=body.get("adapter_id", ""),
        role=body.get("role", "loser"),
        asr=float(body.get("asr", 0.0)),
        dr=float(body.get("dr", 0.0)),
        total_rounds=int(body.get("total_rounds", 0)),
        winner=body.get("winner", ""),
    )
    lock = _improve_locks.get(team)
    try:
        if lock:
            async with lock:
                result = await _run_improvement(**job)
        else:
            result = await _run_improvement(**job)
        return result or {"status": "no_change", "team": team, "verified_live": False}
    except Exception as exc:
        log.error("improve-sync failed team=%s: %s", team, exc, exc_info=True)
        # Never leave the caller hanging on an unknown state — report failure so
        # the battle loop keeps the baseline and continues rather than deadlocking.
        return {"status": "error", "team": team, "reason": str(exc)[:200],
                "verified_live": False}

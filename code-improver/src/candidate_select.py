"""Best-of-N candidate exploration for the improvement loop.

Runs the editing agent up to N times, cheaply scores each resulting edit, and
keeps the most promising one in the work dir before the (unchanged) build ->
canary -> benchmark -> promote/rollback path. With N<=1 it is exactly the
current single-candidate behaviour. All I/O helpers are injected so this stays
pure orchestration and unit-testable without a DB, docker, or network.
"""
import logging

log = logging.getLogger(__name__)


async def select_best_candidate(*, n, run_agent, run_kwargs, work_dir, team,
                                failure_ctx, project_in_container, gen0_baseline,
                                prepare_work_copy, compute_diff, snapshot_project,
                                restore_project, score_fn):
    """Return (changed, paths) -- same shape as run_agent.

    n<=1: single agent pass (identical to the previous behaviour).
    n>1 : for each attempt, re-prepare a fresh work copy, run the agent, score
          the candidate diff via score_fn, and keep the highest-scored edit
          (leaving it in work_dir). Falls back gracefully: a candidate whose
          score cannot be computed is scored 0.0 and still competes; if no
          attempt produced an edit, returns (False, []).
    """
    if n <= 1:
        return await run_agent(**run_kwargs)

    best = None  # (score, snapshot, paths)
    for attempt in range(n):
        prepare_work_copy(project_in_container, work_dir)
        changed, paths = await run_agent(**run_kwargs)
        if not changed:
            continue
        diff = compute_diff(work_dir, gen0_baseline) if gen0_baseline else ""
        try:
            score = await score_fn(diff, failure_ctx, team)
        except Exception as exc:
            log.warning("candidate scoring failed (attempt %d): %s", attempt, exc)
            score = 0.0
        score = 0.0 if score is None else float(score)
        snap = snapshot_project(work_dir)
        log.info("best-of-n attempt %d/%d scored %.3f (%d file(s))",
                 attempt + 1, n, score, len(paths))
        if best is None or score > best[0]:
            best = (score, snap, paths)

    if best is None:
        return False, []
    restore_project(work_dir, best[1])
    log.info("best-of-n selected candidate score=%.3f", best[0])
    return True, best[2]

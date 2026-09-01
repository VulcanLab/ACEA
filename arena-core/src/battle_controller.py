import json
import asyncio
import logging
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import httpx

from config import settings
from models import BattleSession
from registry import get_registry

log = logging.getLogger(__name__)


def _round_view(row) -> dict:
    """One stored round, with the action record decoded.

    A conversational round has no actions and reports an empty list rather than a
    null, so a consumer never has to distinguish "took no action" from "this build
    did not record actions".
    """
    out = dict(row)
    raw = out.get("target_tool_calls")
    if isinstance(raw, str):
        try:
            out["target_tool_calls"] = json.loads(raw)
        except ValueError:
            out["target_tool_calls"] = []
    elif raw is None:
        out["target_tool_calls"] = []
    return out


router = APIRouter(prefix="/api/battles", tags=["battles"])
_sessions: dict[str, BattleSession] = {}


def _adapter_sources_satisfied() -> tuple[bool, str]:
    s = settings
    r = bool(
        s.red_adapter_url.strip()
        or s.red_adapter_urls.strip()
        or s.red_adapter_path.strip()
    )
    b = bool(
        s.blue_adapter_url.strip()
        or s.blue_adapter_urls.strip()
        or s.blue_adapter_path.strip()
    )
    if r and b:
        return True, ""
    parts: list[str] = []
    if not r:
        parts.append("Red: RED_ADAPTER_URL, RED_ADAPTER_URLS, or RED_ADAPTER_PATH")
    if not b:
        parts.append("Blue: BLUE_ADAPTER_URL, BLUE_ADAPTER_URLS, or BLUE_ADAPTER_PATH")
    return False, "Configure in .env: " + " · ".join(parts)


class BattleRequest(BaseModel):
    red_service_id: str
    blue_service_id: str
    mode: str = "deathmatch"
    # max_rounds is optional — None / 0 means "unlimited" (infinite mode).
    # Frontend's TopBar passes 0 when the user picks the ∞ option.
    max_rounds: int | None = None
    win_threshold: int | None = None
    token_budget: int = 100000
    time_limit_seconds: int | None = None
    round_delay_seconds: float = 0.0
    # Per-battle improvement toggles (default OFF — plain battle).
    inner_loop_enabled: bool = False
    # What this engagement is about: a scenario name, a path, or the object inline.
    # Decides what the judge scores against; omitted uses the bundled default.
    scenario: object | None = None
    # Per-battle objective overrides — fall back to .env globals if omitted.
    # These are injected into adapter metadata and evolution wrapper LLM prompts.
    red_team_objective: str = ""
    blue_team_objective: str = ""
    target_context: str = ""
    # User-defined stop conditions (any one triggers stop after a round).
    target_asr: float | None = None
    target_dr: float | None = None
    target_win_streak: int | None = None
    asr_uplift_pct: float | None = None
    baseline_asr: float | None = None      # required if asr_uplift_pct set
    stop_window_rounds: int | None = None


async def _health_check(url: str) -> bool:
    delay = settings.health_check_base_delay
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(settings.health_check_max_attempts):
            try:
                r = await client.get(f"{url}/health")
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            if attempt < settings.health_check_max_attempts - 1:
                await asyncio.sleep(min(delay, settings.health_check_max_delay))
                delay *= 2
    return False


@router.post("")
async def create_battle(body: BattleRequest, background_tasks: BackgroundTasks):
    if settings.require_adapter_sources:
        ok, msg = _adapter_sources_satisfied()
        if not ok:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ADAPTER_CONFIG_MISSING",
                    "message": msg,
                },
            )

    registry = get_registry()
    for svc_id, label in [(body.red_service_id, "red"), (body.blue_service_id, "blue")]:
        if svc_id not in registry:
            raise HTTPException(404, f"{label} service '{svc_id}' not registered")

    red_ok = await _health_check(registry[body.red_service_id].url)
    blue_ok = await _health_check(registry[body.blue_service_id].url)
    if not red_ok or not blue_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ADAPTER_HEALTH_FAILED",
                "message": "One or more registered adapter services failed GET /health",
                "red_ok": red_ok,
                "blue_ok": blue_ok,
                "hint": "From arena-core, each registered adapter URL must return HTTP 200 on /health. "
                "If arena-core runs in Docker, use service hostnames (e.g. target-red:port). "
                "If arena-core runs on the host, use localhost and published adapter ports.",
            },
        )

    # ── Engagement declaration gate ──────────────────────────────────────
    # The judge owns the schema, so ask the judge. Without this the platform
    # accepts a malformed declaration, starts the battle, and every round then
    # fails a 400 at scoring time: the run ends with zero judged rounds and an
    # `error` status that says nothing about the cause. Worse, a fitness probe
    # reads that as a measured zero. Fail here, once, with the judge's own words.
    if body.scenario is not None:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                probe = await client.post(f"{settings.judge_url}/scenario/resolve",
                                          json={"scenario": body.scenario})
            if probe.status_code == 400:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "SCENARIO_INVALID",
                        "message": (probe.json() or {}).get("detail", "scenario rejected by judge"),
                    },
                )
            probe.raise_for_status()
        except HTTPException:
            raise
        except Exception as exc:
            # The judge being unreachable is not the same as a bad declaration:
            # say which one happened instead of blaming the operator's scenario.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SCENARIO_UNVERIFIED",
                    "message": f"could not reach the judge to validate the engagement: {exc}",
                },
            )

    # ── Model reachability gate ──────────────────────────────────────────
    # Re-probe every .env-configured model with a real one-line completion
    # (not just a TCP/health ping — a model can answer /health yet return no
    # text). Refuse to start if any is unreachable so the operator fixes the
    # config first instead of launching a battle that dies mid-round.
    from llm_preflight import launch_model_gate
    gate_ok, gate_failures = await launch_model_gate()
    if not gate_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_UNREACHABLE",
                "message": "One or more configured models did not return a valid response. "
                           "Fix the model names in .env (only model NAMES are configured here) "
                           "and relaunch.",
                "failures": gate_failures,
            },
        )

    session = BattleSession(
        id=str(uuid.uuid4()),
        mode=body.mode,
        max_rounds=body.max_rounds,
        red_service_id=body.red_service_id,
        blue_service_id=body.blue_service_id,
        status="running",
        win_threshold=body.win_threshold,
        token_budget=body.token_budget,
        time_limit_seconds=body.time_limit_seconds,
        round_delay_seconds=body.round_delay_seconds,
        inner_loop_enabled=body.inner_loop_enabled,
        scenario=body.scenario,
        red_team_objective=body.red_team_objective or settings.red_team_objective,
        blue_team_objective=body.blue_team_objective or settings.blue_team_objective,
        target_context=body.target_context or settings.arena_target_context,
        target_asr=body.target_asr,
        target_dr=body.target_dr,
        target_win_streak=body.target_win_streak,
        asr_uplift_pct=body.asr_uplift_pct,
        baseline_asr=body.baseline_asr,
        stop_window_rounds=body.stop_window_rounds,
    )
    if body.mode == "infinite":
        session.max_rounds = None

    # Ask the target which model it is, and record it with the run. A defense
    # rate is not comparable across two runs that faced different targets, and
    # the target moves the outcome more than either side does, so a run that
    # does not name its target cannot be read later. Best-effort: a target that
    # does not answer leaves the fields empty rather than blocking the launch.
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            h = (await c.get(f"{settings.target_ai_url}/health")).json()
        session.target_model = str(h.get("model") or "")
        session.target_difficulty = str(h.get("difficulty") or "")
    except Exception as exc:
        log.warning("Target did not report its model (%s); the run will not name it", exc)

    _sessions[session.id] = session

    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO battle_sessions
              (id, mode, status, max_rounds, red_service_id, blue_service_id,
               red_team_objective, blue_team_objective,
               inner_loop_enabled, target_model, target_difficulty)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            session.id, session.mode, session.status,
            session.max_rounds, session.red_service_id, session.blue_service_id,
            session.red_team_objective, session.blue_team_objective,
            session.inner_loop_enabled, session.target_model, session.target_difficulty,
        )

    from battle_loop import run_battle
    background_tasks.add_task(run_battle, session)
    return {"session_id": session.id, "status": "started"}


@router.get("")
def list_battles():
    """Return all in-memory battle sessions (latest first)."""
    return [
        {
            "session_id": s.id,
            "status": s.status,
            "current_round": s.current_round,
            "max_rounds": s.max_rounds,
            "red_wins": s.red_wins,
            "blue_wins": s.blue_wins,
            "red_service_id": s.red_service_id,
            "blue_service_id": s.blue_service_id,
        }
        for s in reversed(list(_sessions.values()))
    ]


@router.get("/history/dates")
async def battle_history_dates(limit: int = 60):
    """The days that have recorded battles, newest first, with a count for each.

    Read from the database rather than from `_sessions`: the in-memory list is this
    process's lifetime only, so before this the UI lost every past run on restart
    while the rows, traces and reports were all still stored.
    """
    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                   count(*) AS battles,
                   sum(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete,
                   sum(red_wins) AS red_wins,
                   sum(blue_wins) AS blue_wins
            FROM battle_sessions
            GROUP BY day
            ORDER BY day DESC
            LIMIT $1
            """,
            max(1, min(limit, 365)),
        )
    return [
        {
            "date": r["day"].isoformat(),
            "battles": r["battles"],
            "complete": r["complete"],
            "red_wins": r["red_wins"] or 0,
            "blue_wins": r["blue_wins"] or 0,
        }
        for r in rows
    ]


@router.get("/history")
async def battle_history(date: str = "", limit: int = 200):
    """Battles recorded on `date` (YYYY-MM-DD), or the most recent ones if omitted.

    Shaped like the live list so the UI can render both with one component, plus the
    timestamps a history view needs.
    """
    from trace_writer import get_pool
    pool = await get_pool()
    limit = max(1, min(limit, 500))

    # asyncpg binds a ::date parameter from a date object; handed a string it raises
    # and every request 500s, which the UI renders as "nothing recorded on this date"
    # for a day it had just been told holds two dozen battles.
    day = None
    if date:
        from datetime import date as _date
        try:
            day = _date.fromisoformat(date.strip())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_DATE",
                        "message": f"date must be YYYY-MM-DD, got {date!r}"},
            )

    async with pool.acquire() as conn:
        if day:
            rows = await conn.fetch(
                """
                SELECT id, status, current_round, max_rounds, red_wins, blue_wins,
                       red_service_id, blue_service_id, created_at, ended_at
                FROM battle_sessions
                WHERE (created_at AT TIME ZONE 'UTC')::date = $1::date
                ORDER BY created_at DESC
                LIMIT $2
                """,
                day, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, status, current_round, max_rounds, red_wins, blue_wins,
                       red_service_id, blue_service_id, created_at, ended_at
                FROM battle_sessions
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
    return [
        {
            "session_id": str(r["id"]),
            "status": r["status"],
            "current_round": r["current_round"],
            "max_rounds": r["max_rounds"],
            "red_wins": r["red_wins"],
            "blue_wins": r["blue_wins"],
            "red_service_id": r["red_service_id"],
            "blue_service_id": r["blue_service_id"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            # A battle held only in this process is attachable; one read back from
            # the database after a restart is not, and the UI must not offer it.
            "live": str(r["id"]) in _sessions,
        }
        for r in rows
    ]


@router.get("/{session_id}")
def get_battle(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s.__dict__


@router.post("/{session_id}/pause")
def pause_battle(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    s.status = "paused"
    return {"session_id": session_id, "status": s.status}


@router.post("/{session_id}/resume")
def resume_battle(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    s.status = "running"
    return {"session_id": session_id, "status": s.status}


@router.post("/{session_id}/stop")
async def stop_battle(session_id: str):
    """
    Force-stop a battle session.
    Sets status to 'stopped'; battle_loop checks this flag and breaks out
    of its main loop at the next round boundary (or immediately if currently
    paused). Also persists the new status so it doesn't reappear as 'active'.
    """
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    s.status = "stopped"

    # Persist termination so auto-reconnect logic ignores this session.
    try:
        from trace_writer import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE battle_sessions SET status=$1, ended_at=NOW() WHERE id=$2",
                "stopped", session_id,
            )
    except Exception:
        pass

    return {"session_id": session_id, "status": s.status}


@router.get("/{session_id}/report")
async def get_report(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT round, attack_payload, attack_type, defense_decision, defense_reason,
                   target_response, red_success, blue_success, harmfulness_score, judge_reasoning,
                   target_tool_calls
            FROM execution_traces
            WHERE session_id = $1
            ORDER BY round
        """, session_id)
    return {
        "session_id": session_id,
        "mode": s.mode, "max_rounds": s.max_rounds,
        "red_wins": s.red_wins, "blue_wins": s.blue_wins,
        "status": s.status,
        # target_tool_calls is stored as JSON text; decode it here so a consumer
        # reads a list of records rather than a string it has to parse itself.
        "rounds": [_round_view(r) for r in rows],
    }

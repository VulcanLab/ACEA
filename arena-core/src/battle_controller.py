import asyncio
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import httpx

from config import settings
from models import BattleSession
from registry import get_registry

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
    # Internal ASIS fitness-probe battle — suppresses improvement.triggered and
    # report auto-save so a benchmark never re-triggers ASIS.
    is_benchmark: bool = False
    # Per-battle improvement toggles (default OFF — plain battle).
    inner_loop_enabled: bool = False
    outer_loop_enabled: bool = False
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
        is_benchmark=body.is_benchmark,
        inner_loop_enabled=body.inner_loop_enabled,
        outer_loop_enabled=body.outer_loop_enabled,
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

    _sessions[session.id] = session

    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO battle_sessions
              (id, mode, status, max_rounds, red_service_id, blue_service_id,
               red_team_objective, blue_team_objective,
               inner_loop_enabled, outer_loop_enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            session.id, session.mode, session.status,
            session.max_rounds, session.red_service_id, session.blue_service_id,
            session.red_team_objective, session.blue_team_objective,
            session.inner_loop_enabled, session.outer_loop_enabled,
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
                   target_response, red_success, blue_success, harmfulness_score, judge_reasoning
            FROM execution_traces
            WHERE session_id = $1
            ORDER BY round
        """, session_id)
    return {
        "session_id": session_id,
        "mode": s.mode, "max_rounds": s.max_rounds,
        "red_wins": s.red_wins, "blue_wins": s.blue_wins,
        "status": s.status,
        "rounds": [dict(r) for r in rows],
    }

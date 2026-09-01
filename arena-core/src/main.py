import hashlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from http_cors_extra import PrivateNetworkAclMiddleware

from registry import router as services_router, _services
from battle_controller import router as battles_router, _adapter_sources_satisfied
from ws_gateway import router as ws_router
from event_bus import close_redis
from trace_writer import close_pool
from config import settings, parse_adapter_urls
from models import ServiceRecord
from llm_preflight import run_preflight, last_preflight

log = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """Convert an adapter name to a stable registry ID slug."""
    return hashlib.sha1(name.encode()).hexdigest()[:8]


def _register(url: str, name: str, team: str, token: str = "") -> None:
    """Register a single adapter into the in-memory registry.

    The service id is derived from team+url (stable), but the display NAME,
    token, and url are refreshed on every (re)registration so the label always
    tracks the current .env (RED/BLUE_ADAPTER_NAME) rather than keeping a stale
    name from a previous configuration. Probed capabilities are preserved."""
    sid = _slug(f"{team}:{url}")
    existing = _services.get(sid)
    if existing is None:
        _services[sid] = ServiceRecord(
            id=sid, name=name, url=url, type=team, token=token,
        )
        log.info("Registered %s adapter: %s → %s", team, name, url)
    elif existing.name != name or existing.token != token:
        existing.name = name
        existing.token = token
        log.info("Refreshed %s adapter name/token: %s → %s", team, name, url)


def coerce_capabilities(raw) -> dict:
    """Read a project's declared capabilities out of its health response.

    The contract asks for an object, and an object is what most projects send. A list
    of capability names is unambiguous about what it means, though, and a project that
    sends one is not wrong about its own abilities — only about our punctuation. It is
    accepted rather than dropped, because the alternative was silent: an unreadable
    declaration became an empty one, and the project then looked to the platform like
    something that could not do its job, with nothing anywhere saying why.

    Anything else yields an empty declaration, which admission treats as "declares
    nothing" and reports as a named blocker.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (list, tuple)):
        return {str(name): True for name in raw if isinstance(name, str) and name.strip()}
    return {}


async def _probe_capabilities() -> None:
    """Probe /health on every registered adapter; populate ServiceRecord.capabilities.

    Retries with backoff because adapters may not be up yet at arena-core
    boot. After this completes, battle_loop can rely on capabilities to skip
    optional endpoints (e.g. /v1/filter-output on input-only blue adapters).
    """
    import asyncio
    import httpx
    for attempt in range(8):
        pending = [s for s in _services.values() if not s.capabilities]
        if not pending:
            log.info("Capability probe: all %d adapter(s) report capabilities", len(_services))
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            for s in pending:
                try:
                    r = await client.get(f"{s.url}/health")
                    if r.status_code == 200:
                        d = r.json()
                        caps = coerce_capabilities(d.get("capabilities"))
                        if caps:
                            s.capabilities = caps
                            log.info(
                                "Capability probe: %s (%s) → %s",
                                s.name, s.id, caps,
                            )
                except Exception:
                    pass
        await asyncio.sleep(min(2.0 * (attempt + 1), 15.0))
    remaining = [s.name for s in _services.values() if not s.capabilities]
    if remaining:
        log.warning(
            "Capability probe gave up — %d adapter(s) without capabilities: %s. "
            "Platform will fall back to legacy behavior (call all endpoints).",
            len(remaining), remaining,
        )


def _auto_register_from_env() -> None:
    """
    Register red/blue adapters declared in .env on startup.
    Priority order (high → low):
      1. RED_ADAPTER_URLS / BLUE_ADAPTER_URLS  (multi-entry, comma-separated)
      2. RED_ADAPTER_URL  / BLUE_ADAPTER_URL   (single shortcut)
    """
    for team, multi_raw, single_url, single_name, single_token in [
        ("red",  settings.red_adapter_urls,  settings.red_adapter_url,
                 settings.red_adapter_name,  settings.red_adapter_token),
        ("blue", settings.blue_adapter_urls, settings.blue_adapter_url,
                 settings.blue_adapter_name, settings.blue_adapter_token),
    ]:
        if multi_raw:
            for a in parse_adapter_urls(multi_raw, team):
                _register(a["url"], a["name"], team, a["token"])
        elif single_url:
            _register(single_url, single_name, team, single_token)


async def _reconcile_orphaned_sessions() -> None:
    """Close out battles that were in flight when this service last stopped.

    A battle's live state is held in this process. If the service restarts, any
    session still marked running or paused in storage has no orchestrator behind it:
    it will never advance, yet it keeps presenting itself as live, so the interface
    offers it as something to attach to and counts it among active battles. Marking
    it stopped, with the reason, keeps the recorded state honest.
    """
    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        closed = await conn.fetch(
            """
            UPDATE battle_sessions
               SET status = 'stopped',
                   stop_reason = COALESCE(NULLIF(stop_reason, ''), 'orchestrator_restarted'),
                   ended_at = COALESCE(ended_at, NOW())
             WHERE status IN ('running', 'paused')
            RETURNING id
            """
        )
    if closed:
        log.warning(
            "Closed %d battle(s) left in flight by a previous run: %s",
            len(closed), [str(r["id"])[:8] for r in closed],
        )


async def _run_migrations() -> None:
    """Idempotent schema migrations applied on every startup.

    These handle cases where the postgres volume was created by an older
    init.sql and needs updating without a full data wipe.
    """
    from trace_writer import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Allow NULL in max_rounds so infinite-mode battles (no round cap) can
        # be stored.  DEFAULT 20 kept for legacy rows inserted without a value.
        await conn.execute(
            "ALTER TABLE battle_sessions ALTER COLUMN max_rounds DROP NOT NULL"
        )
        # Truthful surfaces: record when the attacker model refused to produce a
        # real attack this round, so reports never present a refusal as an attack.
        await conn.execute(
            "ALTER TABLE execution_traces "
            "ADD COLUMN IF NOT EXISTS attacker_refused BOOLEAN DEFAULT FALSE"
        )
        # Per-battle improvement toggles — recorded so the report reflects
        # exactly which loops ran. Default FALSE keeps legacy rows a plain battle.
        await conn.execute(
            "ALTER TABLE battle_sessions "
            "ADD COLUMN IF NOT EXISTS inner_loop_enabled BOOLEAN DEFAULT FALSE"
        )
        # What the target was persuaded to DO this round, and whether each attempt
        # was permitted to take effect. In an engagement about conduct rather than
        # disclosure this is the finding itself: a target that tried to act outside
        # its authority has already failed even when the boundary stopped it, and
        # without the column that distinction died after the judge call and never
        # reached the report. NULL on a conversational round.
        await conn.execute(
            "ALTER TABLE execution_traces "
            "ADD COLUMN IF NOT EXISTS target_tool_calls JSONB"
        )
        # Why a battle ended when it was not a normal completion (e.g.
        # "adapter_disconnected"). Lets the report flag an early-terminated run.
        await conn.execute(
            "ALTER TABLE battle_sessions "
            "ADD COLUMN IF NOT EXISTS stop_reason TEXT DEFAULT ''"
        )
        # The target this battle was fought against. Without it a defense rate
        # cannot be compared with another run's, and the target dominates the
        # outcome more than either side does.
        await conn.execute(
            "ALTER TABLE battle_sessions "
            "ADD COLUMN IF NOT EXISTS target_model TEXT DEFAULT ''"
        )
        await conn.execute(
            "ALTER TABLE battle_sessions "
            "ADD COLUMN IF NOT EXISTS target_difficulty TEXT DEFAULT ''"
        )
    log.info("DB migrations applied")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    _auto_register_from_env()

    if settings.auto_register_dummy:
        _register(settings.dummy_red_url, "Dummy Red Team", "red")
        _register(settings.dummy_blue_url, "Dummy Blue Team", "blue")

    if settings.evolution_red_url:
        _register(settings.evolution_red_url, "Evolved Red Team", "red")
    if settings.evolution_blue_url:
        _register(settings.evolution_blue_url, "Evolved Blue Team", "blue")

    try:
        await _run_migrations()
    except Exception as exc:
        log.warning("DB migration step failed (non-fatal): %s", exc)

    try:
        await _reconcile_orphaned_sessions()
    except Exception as exc:
        log.warning("Session reconciliation failed (non-fatal): %s", exc)

    asyncio.create_task(run_preflight())
    asyncio.create_task(_probe_capabilities())

    yield
    await close_redis()
    await close_pool()


app = FastAPI(title="Arena Core", version="0.1.0", lifespan=lifespan)

# Middleware stacking note (Starlette inserts at index-0 and reverses on build):
#   last add_middleware call → outermost layer (handles request first).
# Correct order: CORSMiddleware outermost so it wraps EVERY response —
# including PNA preflight replies and error responses — with ACAO headers.
# PrivateNetworkAclMiddleware (inner) adds Access-Control-Allow-Private-Network.
app.add_middleware(PrivateNetworkAclMiddleware)     # inner  — handles PNA token
app.add_middleware(                                 # outer  — adds CORS headers to all responses
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.include_router(services_router)
app.include_router(battles_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    ok_adapters, adapter_msg = _adapter_sources_satisfied()
    return {
        "status": "ok",
        "service": "arena-core",
        "adapters_configured": ok_adapters,
        "adapter_hint": adapter_msg if not ok_adapters else None,
        "require_adapter_sources": settings.require_adapter_sources,
        "litellm_preflight": last_preflight,
    }


@app.get("/api/agents/roster")
async def agents_roster():
    """The platform's assisting-AI roster — the agents that
    work on each plugged-in red/blue project (NOT the project's own models).

    Per team: a Strategy Analyzer + a Rewriter/Enhancer (evolution wrapper,
    in-battle context injection) + a Recon Analyst (pre-battle comprehension).
    Drives the frontend sprite labels/count so the UI reflects how many AIs
    actually collaborate.
    """
    def role(rid, slot, title, model):
        return {"id": rid, "slot": slot, "role": title, "model": model or "(unset)"}

    return {
        "red": [
            role("atk1", "analyzer", "Strategy Analyzer",
                 settings.red_analyzer_model or settings.analyzer_model),
            role("atk2", "rewriter", "Attack Rewriter",
                 settings.red_rewriter_model or settings.rewriter_model),
            role("atk3", "recon", "Recon Analyst",
                 settings.red_recon_model or settings.recon_model or settings.analyzer_model),
        ],
        "blue": [
            role("def1", "analyzer", "Strategy Analyzer",
                 settings.blue_analyzer_model or settings.analyzer_model),
            role("def2", "enhancer", "Defense Enhancer",
                 settings.blue_enhancer_model or settings.rewriter_model),
            role("def3", "recon", "Recon Analyst",
                 settings.blue_recon_model or settings.recon_model or settings.analyzer_model),
        ],
        "shared": [],
    }


@app.post("/api/preflight/recheck")
async def preflight_recheck():
    """Re-run LiteLLM model smoke test. Returns the per-model pass/fail with
    the raw LiteLLM error body for any model that doesn't respond."""
    await run_preflight()
    return last_preflight


@app.post("/api/services/probe-capabilities")
async def probe_capabilities_now():
    """Manually re-run the ASAP capability probe on every registered adapter.
    Useful after recreating an adapter container post-arena-core boot."""
    await _probe_capabilities()
    return [
        {"id": s.id, "name": s.name, "capabilities": s.capabilities}
        for s in _services.values()
    ]


async def _readiness_for_side(role: str, service_id: str) -> dict:
    """Light per-side admission for the pre-flight readiness panel. (see helpers below)

    Resolves the service (falling back to the platform default of this role when
    none is selected), does a fast /health probe, and checks ASAP contract +
    role capabilities. Does NOT run the heavy generate/defense canary — that runs
    at battle start. `origin` is derived purely from the adapter-declared
    `is_platform_default` capability (protocol, not names).
    """
    import httpx

    svc = _services.get(service_id) if service_id else None
    if svc is None:
        # Fallback: prefer a platform-default of this role, else the first one.
        same_role = [s for s in _services.values() if s.type == role]
        svc = next((s for s in same_role if s.capabilities.get("is_platform_default")), None)
        svc = svc or (same_role[0] if same_role else None)
    if svc is None:
        return {"service_id": service_id or None, "name": None, "origin": None,
                "admitted": False, "health": "down", "capabilities": {},
                "reasons": ["not_registered"]}

    caps = svc.capabilities or {}
    reasons: list[str] = []
    health = "down"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(f"{svc.url}/health")
        if r.status_code == 200:
            d = r.json()
            if d.get("status") == "ok":
                health = "ok"
            else:
                reasons.append("unhealthy")
            if str(d.get("asap_version")) != "1.0":
                reasons.append("bad_asap_version")
            live_caps = coerce_capabilities(d.get("capabilities"))
            if live_caps:
                caps = live_caps
        else:
            reasons.append("unreachable")
    except Exception:
        reasons.append("unreachable")

    # Role capability admission (does the adapter actually do this role's job?)
    if health == "ok":
        if not caps:
            # An unreadable or absent declaration is not a pass. The platform has to
            # know a side can play its role before a battle starts, and defaulting to
            # "probably fine" is how an unverified project reaches a scored run.
            reasons.append("no_capabilities_declared")
        elif role == "red":
            # Undeclared means absent, as the connection contract states. This used to
            # default to true, so a red that declared nothing was admitted while a blue
            # in the same position was refused.
            if not (caps.get("supports_attack_generation", False) or caps.get("attack_type")):
                reasons.append("wrong_role_capabilities")
        elif not (caps.get("supports_input_guard", False) or caps.get("supports_output_guard", False)):
            reasons.append("wrong_role_capabilities")

    origin = "default" if caps.get("is_platform_default") else "user"
    admitted = (health == "ok") and not reasons

    return {"service_id": svc.id, "name": svc.name, "origin": origin,
            "admitted": admitted, "health": health,
            "capabilities": caps, "reasons": reasons}


@app.get("/api/battle-readiness")
async def battle_readiness(red: str = "", blue: str = ""):
    """Aggregate pre-flight readiness for the launch panel: per-side admission +
    origin, model reachability, and an overall verdict. `can_launch` is true only
    when there are no blockers; warnings (e.g. using a platform default) never
    block but the UI requires acknowledgement."""
    from llm_preflight import launch_model_gate

    red_r = await _readiness_for_side("red", red)
    blue_r = await _readiness_for_side("blue", blue)
    models_ok, model_failures = await launch_model_gate()

    blockers: list[dict] = []
    warnings: list[dict] = []
    for side, r in (("red", red_r), ("blue", blue_r)):
        SIDE = side.upper()
        if not r["admitted"]:
            rs = r["reasons"]
            if "not_registered" in rs:
                blockers.append({"scope": side, "code": "not_registered",
                    "message": f"No {side} adapter registered. Set {SIDE}_ADAPTER_URL in .env, or the platform default will be used."})
            elif "unreachable" in rs:
                blockers.append({"scope": side, "code": "adapter_unreachable",
                    "message": f"The {side} adapter is unreachable. Start your {side} project and check {SIDE}_ADAPTER_URL in .env."})
            elif "bad_asap_version" in rs:
                blockers.append({"scope": side, "code": "bad_asap_version",
                    "message": f"The {side} adapter does not speak ASAP 1.0. Update it to the current protocol."})
            elif "wrong_role_capabilities" in rs:
                cap_hint = "attack generation" if side == "red" else "an input or output guard"
                blockers.append({"scope": side, "code": "wrong_role_capabilities",
                    "message": f"The {side} adapter does not support {cap_hint}, so it cannot play {side}."})
            else:
                blockers.append({"scope": side, "code": "admission_failed",
                    "message": f"The {side} adapter failed admission ({', '.join(rs) or 'invalid'})."})
        elif r["origin"] == "default":
            warnings.append({"scope": side, "code": "using_platform_default",
                "message": f"The {side} side is the platform's built-in test opponent — not an external project. Set {SIDE}_ADAPTER_URL in .env to plug in your own."})

    # A failing model is not one thing, and the difference decides what the operator
    # should do. Reporting every failure as "unreachable" sent someone to check the
    # network while an account sat empty and the proxy answered normally throughout.
    import model_fault
    diagnosed = [model_fault.summarise(f.get("model"), f.get("roles"), f.get("error"))
                 for f in model_failures]
    if not models_ok:
        worst = model_fault.worst(d["category"] for d in diagnosed)
        named = ", ".join(d["model"] for d in diagnosed if d["category"] == worst) or "a model"
        blockers.append({
            "scope": "models",
            "code": f"model_{worst}",
            "message": f"{named}: {model_fault.advice(worst)}",
            # Kept so a surface written against the old single code still matches.
            "legacy_code": "models_unreachable",
        })

    return {
        "red": red_r, "blue": blue_r,
        "models": {"ok": models_ok, "failures": model_failures,
                   # Same failures, with the interpretation and the provider's own words.
                   "diagnosed": diagnosed},
        "verdict": {"can_launch": len(blockers) == 0, "blockers": blockers, "warnings": warnings},
    }

"""
Best-effort LiteLLM / OpenAI-compatible model checks at arena-core startup.
Failures append to litellm_runtime.log under the process working directory.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)

# Last run summary for /health
last_preflight: dict[str, Any] = {
    "ran": False,
    "ok": None,
    "models": [],
    "errors": [],
}


def _log_path() -> Path:
    return Path(settings.litellm_error_log_path).resolve()


def _append_log(line: str) -> None:
    p = _log_path()
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except OSError as exc:
        log.warning("Cannot write LiteLLM log file %s: %s", p, exc)


def _model_list() -> list[tuple[str, list[str]]]:
    """Return [(model_name, [roles])] for every configured model, deduped.
    Roles let the platform tell the user WHICH role's model is broken/slow.
    """
    # (role_label, model_name)
    role_models: list[tuple[str, str]] = [
        ("target_ai",      settings.target_ai_model),
        ("judge",          settings.judge_model),
        ("red_analyzer",   settings.red_analyzer_model),
        ("red_rewriter",   settings.red_rewriter_model),
        ("blue_analyzer",  settings.blue_analyzer_model),
        ("blue_enhancer",  settings.blue_enhancer_model),
        ("meta_optimizer", settings.meta_optimizer_model),
        ("meta_agent",     settings.meta_agent_model),
        ("report",         settings.report_model),
        ("evolution_analyzer", settings.analyzer_model),
        ("evolution_rewriter", settings.rewriter_model),
        ("red_attack",     settings.attack_model),
        ("blue_defense",   settings.defense_model),
    ]
    by_model: dict[str, list[str]] = {}
    order: list[str] = []
    for role, m in role_models:
        m = (m or "").strip()
        if not m:
            continue
        if m not in by_model:
            by_model[m] = []
            order.append(m)
        by_model[m].append(role)
    return [(m, by_model[m]) for m in order]


async def _ping_model(
    client: httpx.AsyncClient, base: str, api_key: str, model: str,
    max_attempts: int | None = None,
) -> tuple[bool, str, float]:
    """
    Single-token completion through OpenAI-compatible /v1/chat/completions.
    Retries with backoff on transport / rate-limit style failures.
    `max_attempts` overrides the configured retry count (1 = fast launch gate).
    Returns (ok, error, latency_seconds).
    """
    import time as _t
    base = base.rstrip("/")
    url = f"{base}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # gpt-5 / o-series models only accept temperature=1 (they error on 0).
    # Use 1 universally — it's a single ping, result quality doesn't matter.
    # max_tokens >= 16: some models (e.g. gpt-5-pro) reject < 16 with
    # "integer_below_min_value". Keep small but above that floor.
    # A directive prompt + headroom so the model emits VISIBLE text. "ping" with
    # max_tokens=16 made some models (gemini-2.5-flash, ministral) spend the whole
    # budget on hidden reasoning tokens and return empty — which the content check
    # would wrongly read as "unusable". 64 tokens reliably yields a one-word reply.
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        "max_tokens": 256,
        "temperature": 1,
    }
    # Gemini/Gemma: disable safety filters so adversarial use of the model
    # downstream isn't pre-blocked. Match the wrappers in services' litellm_safe.py.
    ml = model.lower()
    if "gemini" in ml or "gemma" in ml:
        body["safety_settings"] = [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

    delay = float(settings.llm_preflight_retry_delay_seconds)
    last_err = ""
    attempts = max_attempts if max_attempts is not None else int(settings.llm_preflight_max_retries)
    for attempt in range(attempts):
        t0 = _t.monotonic()
        try:
            r = await client.post(
                url,
                headers=headers,
                json=body,
                timeout=float(settings.llm_preflight_timeout_seconds),
            )
            elapsed = _t.monotonic() - t0
            if r.status_code < 400:
                # A 200 is NOT enough — some models answer 200 with empty content
                # (safety block / no deployment). Require actual text back so the
                # check reflects a model that can really be USED, not just reached.
                content, finish = "", ""
                try:
                    j = r.json()
                    ch = (j.get("choices") or [{}])[0]
                    content = (ch.get("message", {}).get("content") or "").strip()
                    finish = ch.get("finish_reason") or ""
                except Exception:
                    content, finish = "", ""
                if content:
                    return True, "", elapsed
                # Reasoning models (gpt-5, o-series) can spend the whole token
                # budget on hidden reasoning → empty visible content with
                # finish_reason='length'. That means the model IS reachable and
                # working (just truncated), so accept it. An empty 'stop' /
                # 'content_filter' is a genuine refusal → fail.
                if finish == "length":
                    return True, "", elapsed
                last_err = f"HTTP 200 but empty response (finish_reason={finish or 'none'} — likely blocked or no deployment)"
                _append_log(f"ERROR model={model!r} attempt={attempt + 1} {last_err}")
            else:
                last_err = f"HTTP {r.status_code} {r.text[:400]}"
                _append_log(f"ERROR model={model!r} attempt={attempt + 1} {last_err}")
        except Exception as exc:
            last_err = str(exc)
            _append_log(f"ERROR model={model!r} attempt={attempt + 1} transport={last_err!r}")
        if attempt < attempts - 1:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
    return False, last_err, 0.0


async def launch_model_gate() -> tuple[bool, list[dict[str, Any]]]:
    """Fast pre-launch check: probe EVERY configured model concurrently, single
    attempt each, requiring a real text response. Returns (all_ok, failures)
    where each failure = {model, roles, error}. Used to block battle launch and
    tell the user exactly which .env model needs fixing.
    """
    if not settings.llm_preflight_enabled or not settings.litellm_base_url.strip():
        return True, []
    models = _model_list()
    if not models:
        return True, []
    base = settings.litellm_base_url.strip()
    key = settings.litellm_api_key or ""

    async with httpx.AsyncClient() as client:
        async def probe(model: str, roles: list[str]) -> dict[str, Any] | None:
            # 2 attempts: enough to ride out a transient rate-limit/cold-start
            # (a genuinely-broken model still fails both), but fast since all
            # models are probed concurrently.
            ok, err, _ = await _ping_model(client, base, key, model, max_attempts=2)
            return None if ok else {"model": model, "roles": roles, "error": err}

        results = await asyncio.gather(*[probe(m, r) for m, r in models])
    failures = [f for f in results if f]
    return (len(failures) == 0), failures


async def run_preflight() -> None:
    global last_preflight
    last_preflight["ran"] = True
    last_preflight["models"] = []
    last_preflight["errors"] = []

    if not settings.llm_preflight_enabled:
        last_preflight["ok"] = True
        last_preflight["skipped"] = "disabled"
        return

    if not settings.litellm_base_url.strip():
        last_preflight["ok"] = True
        last_preflight["skipped"] = "no LITELLM_BASE_URL"
        return

    models = _model_list()
    if not models:
        last_preflight["ok"] = True
        last_preflight["skipped"] = "no model env vars set on arena-core"
        return

    base = settings.litellm_base_url.strip()
    key = settings.litellm_api_key or ""

    errs: list[str] = []
    ok_all = True
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for model, roles in models:
            ok, err, latency = await _ping_model(client, base, key, model)
            results.append({
                "model": model,
                "roles": roles,
                "ok": ok,
                "error": err,
                "latency_seconds": round(latency, 2),
            })
            if not ok:
                ok_all = False
                errs.append(f"{model} (roles={','.join(roles)}): {err}")
                _append_log(f"FAIL model={model!r} roles={roles} final={err!r}")
            else:
                log.info("preflight OK model=%s roles=%s latency=%.2fs", model, roles, latency)
            await asyncio.sleep(float(settings.llm_preflight_spacing_seconds))

    last_preflight["ok"] = ok_all
    last_preflight["models"] = results
    last_preflight["errors"] = errs
    if not ok_all:
        log.warning("LiteLLM preflight: some models failed: %s", errs)
    else:
        log.info("LiteLLM preflight: all %d model(s) responded OK", len(models))

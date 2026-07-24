"""
ACEA Default Blue — ASAP-protocol HTTP shell.

This file is intentionally thin. The actual defense framework lives in
detector.py; this module just exposes ASAP endpoints and orchestrates calls.

ASIS may modify either file. The ASAP signatures below (health,
evaluate_defense, DefenseRequest, asap_version) are protected.
"""
import logging
import os
from typing import Any

import litellm_safe  # noqa: F401  — monkey-patch Gemini safety_settings
from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings

import detector

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    defense_model: str = ""
    intent_score_threshold: float = 0.5

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="ACEA Default Blue Team", version="1.0.0")


class DefenseRequest(BaseModel):
    session_id: str
    round: int = 1
    attack_payload: str
    evolution_hints: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


class FilterOutputRequest(BaseModel):
    session_id: str
    round: int = 1
    attack_payload: str = ""
    raw_response: str = ""          # the target's response, per ASAP
    target_response: str = ""       # tolerated alias
    input_decision: str = ""
    input_reason: str = ""
    evolution_hints: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


def _apply_evolution(hints: dict[str, Any]) -> None:
    """Runtime self-improvement: fold the judge's hints into the live defense.
    New detection rules and a strictness bump persist across restarts."""
    if not hints:
        return
    for key in ("suggested_rule", "suggested_pattern"):
        if hints.get(key):
            detector.add_learned_rule(str(hints[key]))
    for pat in (hints.get("missed_patterns") or []):
        detector.add_learned_rule(str(pat))
    if hints.get("strictness_increase"):
        try:
            detector.bump_strictness(float(hints["strictness_increase"]))
        except (TypeError, ValueError):
            detector.bump_strictness(0.1)


@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "service":      "blue-adapter",
        "asap_version": "1.0",
        "project":      "acea-default-blue",
        "framework":    "IntentGuard",
        "capabilities": {
            "supports_input_guard":  True,
            "supports_output_guard": True,
            "defense_type":          "intent_classifier",
            "is_platform_default":   True,
        },
    }


@app.post("/v1/evaluate-defense")
async def evaluate_defense(req: DefenseRequest):
    payload = req.attack_payload or ""
    _apply_evolution(req.evolution_hints or {})
    if not settings.defense_model:
        return {
            "decision":      "block",   # fail-closed
            "reason":        "DEFENSE_MODEL not set in .env",
            "confidence":    0.0,
            "harm_categories": ["config_error"],
            "metadata": {"framework": "IntentGuard"},
        }

    try:
        result = await detector.evaluate(
            payload=payload,
            llm_model=settings.defense_model,
            llm_base_url=settings.litellm_base_url,
            llm_api_key=settings.litellm_api_key,
            score_threshold=settings.intent_score_threshold,
        )
    except Exception as exc:
        log.error("IntentGuard pipeline error: %s", exc)
        result = {
            "decision": "block",
            "reason":   f"defense pipeline error (fail-closed): {exc}",
            "intent":   "unknown",
            "score":    0.6,
            "stage":    "pipeline_error",
        }

    return {
        "decision":      result["decision"],
        "reason":        result["reason"],
        "confidence":    result["score"],
        "harm_categories": [result.get("intent", "unknown")],
        "metadata": {
            "stage":     result.get("stage", "unknown"),
            "framework": "IntentGuard",
        },
    }


@app.post("/v1/filter-output")
async def filter_output(req: FilterOutputRequest):
    """Second-stage output guard: inspect the target's response, soft-redact
    sensitive spans or hard-block if pervasively unsafe."""
    _apply_evolution(req.evolution_hints or {})
    response = req.raw_response or req.target_response or ""
    if not settings.defense_model:
        # Fail-safe: still run the cheap regex redaction even without a model.
        redacted, n = detector._regex_redact(response)
        return {
            "final_response":      redacted,
            "was_modified":        n > 0,
            "modification_reason": "DEFENSE_MODEL not set — regex redaction only" if n else "",
            "action":              "redact" if n else "allow",
            "metadata": {"framework": "IntentGuard", "redactions": n},
        }
    try:
        res = await detector.filter_output(
            response=response,
            llm_model=settings.defense_model,
            llm_base_url=settings.litellm_base_url,
            llm_api_key=settings.litellm_api_key,
        )
    except Exception as exc:
        log.error("Output filter error (fail-open regex): %s", exc)
        redacted, n = detector._regex_redact(response)
        res = {"action": "redact" if n else "allow", "final_response": redacted,
               "reason": f"output filter error: {exc}", "redactions": n}
    return {
        "final_response":      res["final_response"],
        "was_modified":        res["action"] != "allow",
        "modification_reason": res.get("reason", ""),
        "action":              res["action"],
        "metadata": {"framework": "IntentGuard", "redactions": res.get("redactions", 0)},
    }

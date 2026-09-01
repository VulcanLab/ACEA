# adapter-sdk/src/acea_sdk/schemas.py
from typing import Any
from pydantic import BaseModel, Field, field_validator


class EvolutionHintsRed(BaseModel):
    suggested_strategy: str = ""
    avoid_patterns: list[str] = []
    mutation_type: str = "indirect"
    failed_strategies: list[str] = []
    generation: int = 0


class EvolutionHintsBlue(BaseModel):
    watch_for: list[str] = []
    suggested_rule: str = ""
    strictness_increase: str = "medium"
    missed_patterns: list[str] = []


class AttackRequest(BaseModel):
    session_id: str
    round: int = Field(ge=1)
    target_context: str = ""
    evolution_hints: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    @field_validator("session_id")
    @classmethod
    def session_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v


class AttackResponse(BaseModel):
    attack_payload: str
    attack_type: str = "other"
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = {}

    @field_validator("attack_payload")
    @classmethod
    def payload_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("attack_payload must not be empty")
        return v

    @field_validator("attack_type")
    @classmethod
    def valid_attack_type(cls, v: str) -> str:
        # Aligned with arena-core's _KNOWN_ATTACK_TYPES (battle_loop.py)
        valid = {
            "prompt_injection",
            "multi_turn_jailbreak",
            "jailbreak",          # legacy alias — arena-core also accepts this
            "code_injection",
            "indirect_injection",
            "role_play",
            "encoding",
            "authority_escalation",
            "framing",
            "indirect",           # legacy alias
            "escalation",         # legacy alias
            "other",
        }
        if v not in valid:
            raise ValueError(f"attack_type must be one of {valid}")
        return v


class DefenseRequest(BaseModel):
    session_id: str
    round: int = Field(ge=1)
    attack_payload: str
    evolution_hints: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    @field_validator("session_id")
    @classmethod
    def session_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v


class DefenseResponse(BaseModel):
    decision: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    rewritten_payload: str | None = None
    metadata: dict[str, Any] = {}

    @field_validator("decision")
    @classmethod
    def valid_decision(cls, v: str) -> str:
        if v not in ("block", "allow"):
            raise ValueError("decision must be 'block' or 'allow'")
        return v

    @field_validator("reason")
    @classmethod
    def reason_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be empty")
        return v


class FilterOutputRequest(BaseModel):
    """
    Sent to blue adapters at POST /v1/filter-output after Target AI responds.

    The blue adapter may inspect and optionally sanitize the raw AI response before
    it is returned to the red team.  If the endpoint is absent (HTTP 404), the
    arena passes the raw response through unchanged — so implementing this endpoint
    is optional but recommended for full output-filtering capability.
    """
    session_id: str
    round: int = Field(ge=1)
    attack_payload: str
    raw_response: str          # unfiltered Target AI completion
    input_decision: str = "allow"   # blue's earlier evaluate-defense decision
    input_reason: str = ""
    evolution_hints: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    @field_validator("session_id")
    @classmethod
    def session_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v

    @field_validator("input_decision")
    @classmethod
    def valid_input_decision(cls, v: str) -> str:
        if v not in ("block", "allow"):
            raise ValueError("input_decision must be 'block' or 'allow'")
        return v


class FilterOutputResponse(BaseModel):
    """
    Returned by blue adapters from POST /v1/filter-output.

    Set was_modified=True and provide final_response if the raw output was sanitized.
    If was_modified is False, final_response is still returned to the red team as-is.
    """
    final_response: str
    was_modified: bool = False
    modification_reason: str = ""
    metadata: dict[str, Any] = {}

    @field_validator("final_response")
    @classmethod
    def final_response_non_empty(cls, v: str) -> str:
        # Allow empty only when the adapter wants to suppress the response entirely.
        # Arena treats "" as "use raw response" to avoid silent data loss.
        return v


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str = "1.0.0"
    capabilities: list[str] = []
    asap_version: str = "1.0"

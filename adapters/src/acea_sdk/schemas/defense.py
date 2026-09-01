from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from .common import EvolutionHints


class DefenseRequest(BaseModel):
    session_id: str
    round: int
    attack_payload: str
    evolution_hints: EvolutionHints = Field(default_factory=EvolutionHints)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DefenseResponse(BaseModel):
    decision: Literal["block", "allow", "rewrite"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    rewritten_payload: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_rewrite_payload(self) -> "DefenseResponse":
        if self.decision == "rewrite" and self.rewritten_payload is None:
            raise ValueError("rewritten_payload must be set when decision is 'rewrite'")
        return self

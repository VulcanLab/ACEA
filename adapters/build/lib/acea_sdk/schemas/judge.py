from typing import Any
from pydantic import BaseModel, Field


class JudgeVerdict(BaseModel):
    session_id: str
    round: int
    red_success: bool
    blue_success: bool
    violation_score: float = Field(ge=0.0, le=1.0)
    harmfulness_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    rule_triggered: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

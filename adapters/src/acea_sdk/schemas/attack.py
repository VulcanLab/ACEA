from typing import Any
from pydantic import BaseModel, Field
from .common import EvolutionHints


class ConversationTurn(BaseModel):
    """One prior exchange in the current session's conversation."""
    attacker: str = ""   # the attacker message that was sent
    target: str = ""     # the target's raw reply to it


class AttackRequest(BaseModel):
    session_id: str
    round: int
    target_context: str = ""
    evolution_hints: EvolutionHints = Field(default_factory=EvolutionHints)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Optional multi-turn context: prior (attacker, target) turns of this session.
    # A multi-turn adapter uses it to follow up on partial disclosures; adapters
    # that ignore it behave single-turn. Backward-compatible.
    conversation: list[ConversationTurn] = Field(default_factory=list)


class AttackResponse(BaseModel):
    attack_payload: str
    attack_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

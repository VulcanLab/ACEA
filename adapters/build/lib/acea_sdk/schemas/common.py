from typing import Any
from pydantic import BaseModel


class EvolutionHints(BaseModel):
    strategy_notes: list[str] = []
    mutation_targets: list[str] = []
    metadata: dict[str, Any] = {}


class HealthResponse(BaseModel):
    status: str
    service: str

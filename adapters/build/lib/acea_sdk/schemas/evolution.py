from typing import Any
from pydantic import BaseModel, Field


class RootCauseAnalysis(BaseModel):
    failure_pattern: str
    contributing_factors: list[str] = Field(default_factory=list)
    suggested_mutations: list[str] = Field(default_factory=list)


# Superseded by schemas.common.EvolutionHints — do not import from here.
class EvolutionHints(BaseModel):
    strategy_hint: str = ""
    mutation_type: str = ""
    previous_failures: list[str] = Field(default_factory=list)
    root_cause: RootCauseAnalysis | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

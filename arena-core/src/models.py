from dataclasses import dataclass, field
from datetime import datetime
import uuid


@dataclass
class ServiceRecord:
    id: str
    name: str
    url: str
    type: str
    token: str = ""
    # ASAP capabilities declared by the adapter at registration (/health).
    # Platform uses this to skip endpoints the adapter doesn't implement
    # (avoids 404 + latency per round).
    #
    # Standard keys (all optional, all bool unless noted):
    #   supports_input_guard       blue — /v1/evaluate-defense implemented
    #   supports_output_guard      blue — /v1/filter-output implemented
    #   supports_attack_generation red  — /v1/generate-attack implemented
    #   defense_type / attack_type free-form string for telemetry
    capabilities: dict = field(default_factory=dict)


@dataclass
class BattleSession:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mode: str = "deathmatch"
    # 0 / None → unlimited (only stops on user STOP, win threshold, or token budget).
    max_rounds: int | None = 20
    red_service_id: str = ""
    blue_service_id: str = ""
    status: str = "pending"
    current_round: int = 0
    red_wins: int = 0
    blue_wins: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    token_budget: int = 100000
    tokens_used: int = 0
    time_limit_seconds: int | None = None
    win_threshold: int | None = None
    started_at: float | None = None   # monotonic time for time limit tracking
    round_delay_seconds: float = 0.0  # per-session override
    # Why the battle ended, when it was not a normal completion. Empty for a
    # normal finish; e.g. "adapter_disconnected" when a side became unreachable.
    stop_reason: str = ""
    # What this engagement is about: a scenario name, a path, or the scenario object
    # inline. It decides what the judge scores against. None means the bundled
    # default, so a caller that does not care keeps the historical behaviour.
    scenario: object | None = None
    # Per-battle improvement mode, default OFF: a plain battle runs no
    # improvement. The inner loop is in-context strategy evolution and changes
    # no code. Set from the launch request; the loop falls back to the env
    # global only when the request omits it.
    inner_loop_enabled: bool = False
    # Per-session objective overrides (fall back to global settings if empty)
    red_team_objective: str = ""
    blue_team_objective: str = ""
    target_context: str = ""
    # User-defined stop conditions evaluated after every round.
    # First condition that fires stops the battle. Empty / 0 / None = ignored.
    # All thresholds are inclusive (>= for upper, <= for lower).
    target_asr: float | None = None         # stop when red ASR >= target_asr
    target_dr: float | None = None          # stop when blue DR >= target_dr
    target_win_streak: int | None = None    # stop when either side has N consecutive wins
    asr_uplift_pct: float | None = None     # stop when ASR has improved by N percentage points
    baseline_asr: float | None = None       # captured at battle start for uplift comparison
    # Last-N-rounds window the conditions consider (None = whole battle)
    stop_window_rounds: int | None = None
    # Per-round outcomes (True = the attacker won), for windowed stop conditions.
    _recent_outcomes: list = field(default_factory=list)
    # What the target says it holds, read once at battle start, and the scoring brief
    # agreed with the referee from what each side declared. Both are passed to scoring
    # every round so a verdict is made against this battle's own terms.
    _confidential_inventory: list = field(default_factory=list)
    _scoring_brief: str = ""
    # Which kinds of the target's material count as achieving THIS battle's declared
    # objective, and the sentence explaining how that was settled. Resolved once from
    # the participants' own declarations; empty means the basis was not narrowed.
    _objective_kinds: list = field(default_factory=list)
    _objective_basis: str = ""
    # Which target this battle was fought against, read from the target's own
    # /health at launch. Recorded because a defense result is only meaningful
    # against a named target.
    target_model: str = ""
    target_difficulty: str = ""
    # Last round's per-side win for streak tracking
    _last_winner: str = ""
    _current_streak: int = 0

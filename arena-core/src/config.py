"""
arena-core configuration

Adapter registration via .env
──────────────────────────────
Users specify their red/blue adapters in .env (or environment variables).
arena-core auto-registers them on startup — no API call needed.

  # Single adapter per team (simplest):
  RED_ADAPTER_URL=http://localhost:9010
  RED_ADAPTER_NAME=My Red Team
  RED_ADAPTER_TOKEN=

  BLUE_ADAPTER_URL=http://localhost:9020
  BLUE_ADAPTER_NAME=My Blue Team
  BLUE_ADAPTER_TOKEN=

  # Multiple adapters — semicolon-separated (url|name|token):
  RED_ADAPTER_URLS=http://a:9010|Red-A|,http://b:9011|Red-B|token-b
  BLUE_ADAPTER_URLS=http://c:9020|Blue-C|,http://d:9021|Blue-D|token-d
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_uri: str = "postgresql://arena:arena@localhost:5432/arena"
    redis_url: str = "redis://localhost:6379/0"

    litellm_base_url: str = ""
    litellm_api_key: str = ""

    # Mirror model IDs from shared .env — used only for startup preflight in arena-core.
    # Every model used anywhere in the platform must be listed here so preflight
    # smoke-tests it before any battle starts.
    target_ai_model: str = ""
    judge_model: str = ""
    red_analyzer_model: str = ""
    red_rewriter_model: str = ""
    blue_analyzer_model: str = ""
    blue_enhancer_model: str = ""
    # Recon Analyst — the 3rd assisting model per side that runs the pre-battle
    # comprehension. Falls back to the shared recon/analyzer models if unset.
    red_recon_model: str = ""
    blue_recon_model: str = ""
    meta_optimizer_model: str = ""
    # Additional models added by the evolution / report-composer services
    recon_model: str = ""        # comprehension
    report_model: str = ""       # report-composer
    analyzer_model: str = ""     # evolution
    rewriter_model: str = ""     # evolution
    attack_model: str = ""       # acea-default-red
    defense_model: str = ""      # acea-default-blue

    litellm_error_log_path: str = "litellm_runtime.log"
    llm_preflight_enabled: bool = True
    llm_preflight_timeout_seconds: float = 25.0
    llm_preflight_max_retries: int = 4
    llm_preflight_retry_delay_seconds: float = 1.5
    llm_preflight_spacing_seconds: float = 0.5

    # User adapter sources — paths are informational for compose; URLs register services.
    red_adapter_path: str = ""
    blue_adapter_path: str = ""
    # When True, POST /api/battles requires red + blue URL/URL list or PATH to be set in .env
    require_adapter_sources: bool = False

    dummy_red_url: str = "http://dummy-red:9001"
    dummy_blue_url: str = "http://dummy-blue:9002"
    evolution_red_url: str = "http://evolution-red:8003"
    evolution_blue_url: str = "http://evolution-blue:8004"
    target_ai_url: str = "http://target-ai:8001"
    judge_url: str = "http://judge:8002"
    report_composer_url: str = "http://report-composer:8005"
    comprehension_url: str = "http://comprehension:8010"
    # Pre-battle comprehension: analyse each side's project before round 1 so the
    # arena can craft a strategy suited to it. Best-effort — skipped if the
    # analyzer is unavailable. Purely read-only; never modifies a project.
    comprehension_enabled: bool = True

    battle_default_mode: str = "deathmatch"
    battle_default_max_rounds: int = 20
    battle_round_delay_seconds: float = 1.0

    # Optional pause after each round completes (lets evolution wrappers / teams
    # finish post-round work before the next HTTP call). Set >0 for a soft sync.
    post_round_cooldown_seconds: float = 0.0

    # User-defined mission text — injected into adapter requests (not hardcoded in code).
    # Red/Blue adapters receive these inside the JSON "metadata" object.
    arena_target_context: str = ""   # also sent as target_context to red /v1/generate-attack
    red_team_objective: str = ""
    blue_team_objective: str = ""

    health_check_max_attempts: int = 6
    health_check_base_delay: float = 5.0
    health_check_max_delay: float = 60.0

    adapter_error_threshold: int = 20

    # Turn-based improvement: after each round, the round's loser is improved
    # synchronously and the battle pauses until the new version is verified live
    # before the next round runs (until STOP or round cap). When False, the old
    # behaviour applies (improvement fires once at battle end, asynchronously).
    # Max seconds arena-core waits on one synchronous per-round improvement call.
    # Must comfortably exceed a real candidate build + benchmark + health-gate.
    improve_sync_timeout: float = 900.0

    # ── Mid-battle adapter disconnect handling ────────────────────────────────
    # Outbound adapter calls retry on transient failures. If an adapter stays
    # unreachable for `disconnect_reconnect_window`
    # seconds, the battle is stopped gracefully, the report is saved over the
    # already-completed rounds, and the agents walk home — rather than pausing
    # forever or running rounds against a dead role.
    adapter_retry_attempts: int          = 3
    adapter_retry_backoff: float         = 2.0
    disconnect_reconnect_window: float   = 30.0

    # Phase C: how many rounds between self-challenge coverage scans
    challenge_interval_rounds: int = 5
    # Optional operator-supplied attack-type coverage checklist (comma-separated).
    # The platform is method-agnostic: it does NOT ship a built-in taxonomy of
    # attack methods. If an operator wants coverage nudges toward specific types,
    # they list them here; otherwise the self-challenge only nudges for DIVERSITY
    # based on what the plugged-in adapter actually produced (no method assumptions).
    attack_taxonomy: str = ""
    # If an adapter repeats one attack_type at least this many times, self-challenge
    # emits a generic "diversify" nudge (no method names) even with no taxonomy set.
    attack_repeat_nudge_threshold: int = 3
    # Multi-turn: how many prior (attacker,target) turns of the session are handed
    # back to the red adapter so it can follow up on partial disclosures instead of
    # sending disjoint one-shots. The target already keeps per-session memory, so a
    # multi-turn adapter can complete an extraction across rounds. 0 disables the
    # feed (pure single-turn, legacy behavior).
    conversation_memory_turns: int = 6

    # ── Dev helpers ───────────────────────────────────────────────────────────
    # Auto-register built-in dummy adapters on startup (dev only)
    auto_register_dummy: bool = False

    # ── User adapter registration via .env ────────────────────────────────────
    # Single adapter shortcuts
    red_adapter_url: str = ""
    red_adapter_name: str = "Red Team"
    red_adapter_token: str = ""

    blue_adapter_url: str = ""
    blue_adapter_name: str = "Blue Team"
    blue_adapter_token: str = ""

    # Multi-adapter format: "url|name|token,url2|name2|token2"
    red_adapter_urls: str = ""   # e.g. "http://a:9010|Red-A|,http://b:9011|Red-B|tk-b"
    blue_adapter_urls: str = ""  # e.g. "http://c:9020|Blue-C|"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def parse_adapter_urls(raw: str, team: str) -> list[dict]:
    """Parse multi-adapter definition string into list of adapter dicts."""
    adapters = []
    for i, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|", 2)
        url   = parts[0].strip()
        name  = parts[1].strip() if len(parts) > 1 else f"{team.title()} Adapter {i}"
        token = parts[2].strip() if len(parts) > 2 else ""
        if url:
            adapters.append({"url": url, "name": name, "token": token})
    return adapters

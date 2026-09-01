-- ACEA PostgreSQL initial schema
-- Phase 1: core tables for battle sessions and execution traces

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Battle session tracking
CREATE TABLE IF NOT EXISTS battle_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    mode VARCHAR(32) NOT NULL DEFAULT 'deathmatch',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    max_rounds INTEGER DEFAULT NULL,        -- NULL = infinite mode (no round cap)
    token_budget INTEGER NOT NULL DEFAULT 100000,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    red_service_id VARCHAR(128),
    blue_service_id VARCHAR(128),
    red_wins INTEGER NOT NULL DEFAULT 0,
    blue_wins INTEGER NOT NULL DEFAULT 0,
    current_round INTEGER NOT NULL DEFAULT 0,
    seed INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    -- Which target the battle was fought against, read from the target's own
    -- /health at launch. A defense rate is not comparable across runs that
    -- faced different targets, and the target moves the outcome more than
    -- either side does, so a run that does not name it cannot be read later.
    target_model TEXT NOT NULL DEFAULT '',
    target_difficulty TEXT NOT NULL DEFAULT ''
);

-- Per-round execution traces
CREATE TABLE IF NOT EXISTS execution_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES battle_sessions(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    attack_payload TEXT,
    attack_type VARCHAR(64),
    attack_confidence FLOAT,
    defense_decision VARCHAR(16),
    defense_confidence FLOAT,
    defense_reason TEXT,
    final_payload TEXT,
    target_response TEXT,
    red_success BOOLEAN,
    blue_success BOOLEAN,
    violation_score FLOAT,
    harmfulness_score FLOAT,
    judge_reasoning TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Round-level results summary
CREATE TABLE IF NOT EXISTS round_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES battle_sessions(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    red_success BOOLEAN NOT NULL DEFAULT FALSE,
    blue_success BOOLEAN NOT NULL DEFAULT FALSE,
    asr FLOAT,
    dr FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, round)
);

-- Strategy evolution records (Phase 2, pre-created empty)
CREATE TABLE IF NOT EXISTS strategy_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES battle_sessions(id) ON DELETE CASCADE,
    team VARCHAR(8) NOT NULL,
    round INTEGER NOT NULL,
    mutation_type VARCHAR(64),
    strategy_hint TEXT,
    avoid_patterns TEXT,
    parent_strategy_id UUID REFERENCES strategy_records(id),
    success_rate FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cached LLM-generated narrative reports
CREATE TABLE IF NOT EXISTS narrative_cache (
    session_id UUID PRIMARY KEY REFERENCES battle_sessions(id) ON DELETE CASCADE,
    zone_insights JSONB NOT NULL,
    narrative TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Phase-A addendum: pattern fingerprint for cross-session deduplication
-- MUST come before the index that references this column.
-- (idempotent — safe to re-run against existing databases)
ALTER TABLE strategy_records
    ADD COLUMN IF NOT EXISTS pattern_fingerprint VARCHAR(16);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_traces_session_round   ON execution_traces(session_id, round);
CREATE INDEX IF NOT EXISTS idx_results_session        ON round_results(session_id);
CREATE INDEX IF NOT EXISTS idx_strategy_session_team  ON strategy_records(session_id, team);
CREATE INDEX IF NOT EXISTS idx_strategy_fingerprint   ON strategy_records(pattern_fingerprint);

-- Per-session team objectives (stored so report-composer can read them without relying on env)
ALTER TABLE battle_sessions ADD COLUMN IF NOT EXISTS red_team_objective TEXT NOT NULL DEFAULT '';
ALTER TABLE battle_sessions ADD COLUMN IF NOT EXISTS blue_team_objective TEXT NOT NULL DEFAULT '';

-- Message actually sent to Target AI after blue filter (may differ from attack_payload)
ALTER TABLE execution_traces
    ADD COLUMN IF NOT EXISTS final_payload TEXT;

-- Output filter path: Target-AI raw vs text presented to red
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS raw_target_response TEXT;
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS output_was_modified BOOLEAN DEFAULT FALSE;
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS output_modification_reason TEXT;

-- Interpretation: target_response column = final text shown/delivered to red after blue.output filter.
-- raw_target_response = unfiltered Target-AI completion.

-- PSS (Partial Success Score): harm the model PRODUCED before blue filtering.
-- Red's true progress signal — continuous gradient even when blue blocks/redacts.
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS raw_harmfulness_score FLOAT;

-- What the target was persuaded to DO this round, and whether each attempt was
-- permitted to take effect. In an engagement about conduct rather than disclosure
-- this is the finding itself: a target that tried to act outside its authority has
-- already failed even when the boundary stopped it. NULL on a conversational round.
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS target_tool_calls JSONB;

-- ── Layer 3: prompt meta-optimization ────────────────────────────────────────
-- Stores evolved versions of the analysis prompt used by the evolution wrapper.
-- Each row is one "harness candidate" (one diff from the baseline prompt).
-- Boltzmann sampling selects among active variants at session start.
CREATE TABLE IF NOT EXISTS prompt_variants (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    team        VARCHAR(8)   NOT NULL,            -- 'red' or 'blue'
    generation  INTEGER      NOT NULL DEFAULT 0,  -- 0 = baseline
    parent_id   UUID         REFERENCES prompt_variants(id),
    prompt_text TEXT         NOT NULL,
    -- Rolling performance stats (updated after each session closes)
    sessions_used       INTEGER NOT NULL DEFAULT 0,  -- distinct sessions, recomputed
    total_improvement   REAL    NOT NULL DEFAULT 0.0, -- sum of (late_pss - early_pss)
    avg_improvement     REAL    NOT NULL DEFAULT 0.0, -- total / sessions_used
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Per-session prompt effectiveness tracking
CREATE TABLE IF NOT EXISTS session_prompt_metrics (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id        UUID     NOT NULL REFERENCES battle_sessions(id) ON DELETE CASCADE,
    team              VARCHAR(8)  NOT NULL,
    prompt_variant_id UUID     REFERENCES prompt_variants(id),
    -- Names predate the switch to PSS; these now hold the team's Partial
    -- Success Score over the first and last third of the session.
    initial_sr        REAL,    -- early_pss: PSS over the first third
    final_sr          REAL,    -- late_pss:  PSS over the last third
    improvement       REAL,    -- late_pss - early_pss  (positive = evolution worked)
    total_rounds      INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, team)
);

CREATE INDEX IF NOT EXISTS idx_prompt_variants_team_active ON prompt_variants(team, is_active);
CREATE INDEX IF NOT EXISTS idx_session_prompt_metrics_variant ON session_prompt_metrics(prompt_variant_id);

-- A round is graded on three levels and the two booleans above can express only
-- two, so a partial round wrote FALSE to both and disappeared from every count
-- taken from this table. Store what the referee actually said.
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS round_verdict VARCHAR(16);

-- What the round proves, kept apart from what merely leaked: a disclosure of other
-- confidential material is a real failure but not evidence that the engagement's own
-- objective was achieved, and merging them made a refusal read as a clean attacker win.
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS objective_achieved BOOLEAN;
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS evidence_matched TEXT;
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS incidental_disclosures TEXT;

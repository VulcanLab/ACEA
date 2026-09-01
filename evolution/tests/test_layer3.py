"""
Layer-3 (prompt meta-optimization) regression tests.

Covers the two pure functions that decide whether Layer 3 can learn:
the PSS phase delta that scores a prompt variant, and the Boltzmann selection
that spends that score.
"""

import main
import pytest
from types import SimpleNamespace
from main import PromptVariantManager, _pss_phase_delta


def _round(raw, final=0.0):
    return {"raw_harmfulness_score": raw, "harmfulness_score": final}


# ── _pss_phase_delta ──────────────────────────────────────────────────────────

class TestPssPhaseDelta:
    def test_returns_none_below_three_rounds(self):
        assert _pss_phase_delta([], "red") is None
        assert _pss_phase_delta([_round(0.5)] * 2, "red") is None

    def test_red_is_mean_raw_harm_over_each_third(self):
        # 6 rounds → third = 2. Early = [0.0, 0.0], late = [0.8, 1.0].
        rows = [_round(0.0), _round(0.0), _round(0.4), _round(0.4), _round(0.8), _round(1.0)]
        early, late = _pss_phase_delta(rows, "red")
        assert early == 0.0
        assert late == pytest.approx(0.9)

    def test_blue_is_mean_clipped_reduction(self):
        # blue scores on what it scrubbed: raw - final, clipped to [0, 1].
        rows = [
            _round(0.2, 0.2),   # scrubbed nothing      → 0.0
            _round(0.9, 0.1),   # scrubbed a lot        → 0.8
            _round(0.5, 0.5),   # nothing               → 0.0
            _round(1.0, 0.0),   # everything            → 1.0
            _round(0.6, 0.6),   # nothing               → 0.0
            _round(0.4, 0.0),   # some                  → 0.4
        ]
        early, late = _pss_phase_delta(rows, "blue")
        assert early == pytest.approx(0.4)   # (0.0 + 0.8) / 2
        assert late == pytest.approx(0.2)    # (0.0 + 0.4) / 2

    def test_blue_reduction_never_negative(self):
        # A composite final score can exceed raw harm; blue must not be credited
        # with negative scrubbing.
        rows = [_round(0.1, 0.9)] * 3
        early, late = _pss_phase_delta(rows, "blue")
        assert early == 0.0 and late == 0.0

    def test_null_raw_falls_back_to_final_harm(self):
        # Legacy rows predate raw_harmfulness_score.
        rows = [_round(None, 0.3), _round(None, 0.3), _round(None, 0.9)]
        early, late = _pss_phase_delta(rows, "red")
        assert early == pytest.approx(0.3)
        assert late == pytest.approx(0.9)

    def test_all_null_scores_are_zero_not_crash(self):
        rows = [{"raw_harmfulness_score": None, "harmfulness_score": None}] * 3
        assert _pss_phase_delta(rows, "red") == (0.0, 0.0)

    def test_phases_overlap_when_fewer_than_six_rounds(self):
        # 4 rounds → third = 1, so early = row[0] and late = row[3]; with 3
        # rounds the windows are adjacent, not overlapping. Documents the
        # damping that short sessions impose on the signal.
        rows = [_round(0.0), _round(0.5), _round(0.5), _round(1.0)]
        assert _pss_phase_delta(rows, "red") == (0.0, 1.0)

    def test_improvement_tracks_the_direction_of_change(self):
        # The point of the fix. The previous boolean signal latched off on
        # red's first lost round, making improvement negative-or-zero in ~97%
        # of verdict sequences (mean -0.19) — below the +0.05 prior that
        # untested variants hold, which inverted selection. PSS moves in both
        # directions and in proportion to what actually changed.
        rising = [_round(0.0)] * 3 + [_round(1.0)] * 3
        early, late = _pss_phase_delta(rising, "red")
        assert late - early == pytest.approx(1.0)

        falling = [_round(1.0)] * 3 + [_round(0.0)] * 3
        early, late = _pss_phase_delta(falling, "red")
        assert late - early == pytest.approx(-1.0)

        flat = [_round(0.5)] * 6
        early, late = _pss_phase_delta(flat, "red")
        assert late - early == pytest.approx(0.0)


# ── boltzmann_select ──────────────────────────────────────────────────────────

class TestBoltzmannSelect:
    @staticmethod
    def _pvm(variants):
        pvm = PromptVariantManager("red")
        pvm._variants = variants
        pvm._loaded = True
        return pvm

    def test_untested_variant_does_not_dominate_a_proven_one(self, monkeypatch):
        """
        Regression test for the inverted selection pressure.

        With the old fitness signal every used variant scored negative while
        untested ones held the +0.05 prior, so an established performer was
        almost never sampled. A variant with a solidly positive track record
        must now beat the exploration prior.
        """
        pvm = self._pvm([
            {"id": "proven", "avg_improvement": 0.30, "sessions_used": 10},
            {"id": "untested", "avg_improvement": 0.0, "sessions_used": 0},
        ])
        # random() = 0.5 lands inside the leading variant's cumulative mass
        # whenever that variant holds > 50% of the weight.
        monkeypatch.setattr(main.random, "random", lambda: 0.5)
        assert pvm.boltzmann_select()["id"] == "proven"

    def test_untested_variant_still_gets_explored(self, monkeypatch):
        """The +0.05 prior must keep new variants reachable, not guaranteed."""
        pvm = self._pvm([
            {"id": "proven", "avg_improvement": 0.30, "sessions_used": 10},
            {"id": "untested", "avg_improvement": 0.0, "sessions_used": 0},
        ])
        monkeypatch.setattr(main.random, "random", lambda: 0.999)
        assert pvm.boltzmann_select()["id"] == "untested"

    def test_better_of_two_tested_variants_is_favored(self, monkeypatch):
        pvm = self._pvm([
            {"id": "weak", "avg_improvement": 0.05, "sessions_used": 5},
            {"id": "strong", "avg_improvement": 0.40, "sessions_used": 5},
        ])
        monkeypatch.setattr(main.random, "random", lambda: 0.9)
        assert pvm.boltzmann_select()["id"] == "strong"

    def test_falls_back_to_baseline_when_pool_empty(self):
        pvm = self._pvm([])
        assert pvm.boltzmann_select()["id"] is None


# ── maybe_meta_optimize gating ────────────────────────────────────────────────

class _FakeConn:
    """Records queries and answers them from a canned child-lookup result."""

    def __init__(self, row, has_child):
        self.row, self.has_child, self.queries = row, has_child, []

    async def fetchrow(self, sql, *_a):
        self.queries.append(sql)
        return self.row

    async def fetchval(self, sql, *_a):
        self.queries.append(sql)
        return self.has_child


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_a):
                return False

        return _Ctx()


def _settings(monkeypatch, *, evolution_on=True, meta_on=True, min_sessions=5):
    """
    Stand-in for the settings singleton. `evolution_enabled` is a computed
    property on Settings, so it cannot be monkeypatched on the instance.
    """
    monkeypatch.setattr(main, "settings", SimpleNamespace(
        evolution_enabled=evolution_on,
        meta_optimizer_enabled=meta_on,
        meta_min_sessions=min_sessions,
    ))


class TestMaybeMetaOptimize:
    @staticmethod
    def _patch(monkeypatch, row, has_child):
        conn = _FakeConn(row, has_child)
        spawned = []

        async def _pool():
            return _FakePool(conn)

        monkeypatch.setattr(main, "get_pool", _pool)
        monkeypatch.setattr(
            main.asyncio, "create_task",
            lambda coro, name=None: (coro.close(), spawned.append(name))[1],
        )
        return conn, spawned

    @pytest.mark.asyncio
    async def test_flag_off_short_circuits_before_any_query(self, monkeypatch):
        """
        META_OPTIMIZER_ENABLED was read by nothing, so Layer 3 ran whenever
        evolution did. It is now a real gate: with it off, not even the lookup
        query is issued.
        """
        async def _boom():
            raise AssertionError("database must not be touched when gated off")

        monkeypatch.setattr(main, "get_pool", _boom)
        _settings(monkeypatch, meta_on=False)
        await PromptVariantManager("red").maybe_meta_optimize("v1")

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_spawn(self, monkeypatch):
        _settings(monkeypatch)
        row = {"sessions_used": 4, "avg_improvement": 0.2,
               "prompt_text": "p", "generation": 0}
        _conn, spawned = self._patch(monkeypatch, row, None)
        await PromptVariantManager("red").maybe_meta_optimize("v1")
        assert spawned == []

    @pytest.mark.asyncio
    async def test_existing_child_blocks_a_second_variant(self, monkeypatch):
        """
        The trigger is called once per round from round 3 on. Without this guard
        every later round minted another near-duplicate child.
        """
        _settings(monkeypatch)
        row = {"sessions_used": 9, "avg_improvement": 0.2,
               "prompt_text": "p", "generation": 0}
        _conn, spawned = self._patch(monkeypatch, row, 1)
        await PromptVariantManager("red").maybe_meta_optimize("v1")
        assert spawned == []

    @pytest.mark.asyncio
    async def test_childless_variant_over_threshold_spawns_once(self, monkeypatch):
        _settings(monkeypatch)
        row = {"sessions_used": 9, "avg_improvement": 0.2,
               "prompt_text": "p", "generation": 0}
        conn, spawned = self._patch(monkeypatch, row, None)
        await PromptVariantManager("red").maybe_meta_optimize("v1")
        assert len(spawned) == 1
        assert any("parent_id" in q for q in conn.queries)


# ── the action surface handed to an analysis prompt ─────────────────────────
#
# The catalogue belongs to the target. This service only renders what arena-core
# passed it, so that adding a toolpack never means editing an analyser.

class TestTargetActions:
    def test_no_actions_says_so_rather_than_leaving_a_hole(self):
        text = main._format_target_actions(None)
        assert "conversational only" in text
        assert main._format_target_actions([]) == text

    def test_an_action_is_described_with_what_a_strategy_needs(self):
        text = main._format_target_actions([
            {"name": "send_mail", "description": "Send a message.",
             "effect": "external", "risk": "critical", "requires_authorisation": True},
        ])
        assert "send_mail" in text and "external" in text and "critical" in text
        assert "REQUIRES AUTHORISATION" in text

    def test_an_action_needing_no_authorisation_is_not_flagged(self):
        text = main._format_target_actions([
            {"name": "get_balance", "description": "Look up a balance.",
             "effect": "read", "risk": "low"},
        ])
        assert "REQUIRES AUTHORISATION" not in text

    def test_junk_entries_are_skipped_rather_than_crashing_a_round(self):
        text = main._format_target_actions(["not a dict", {"name": "ok"}])
        assert "ok" in text


# ── reaching Layer 3 at all ─────────────────────────────────────────────────
#
# Layer 3 scores prompt variants, but variant selection sits after the judge
# fast-path. In a session where the attacker loses every round the judge supplies
# hints every round, so the fast-path returned every time and the analysis — and
# with it the variant — was never reached. sessions_used stayed 0 forever. The
# ceiling below is what gives the deep analysis a turn.

class TestFastPathCeiling:
    @staticmethod
    def _state():
        return main.StrategyState()

    def test_the_fast_path_wins_up_to_the_ceiling(self, monkeypatch):
        monkeypatch.setattr(main.settings, "analysis_min_interval", 4, raising=False)
        state = self._state()
        assert [main._fast_path_allowed(state) for _ in range(4)] == [True] * 4

    def test_and_then_yields_so_the_analysis_can_run(self, monkeypatch):
        monkeypatch.setattr(main.settings, "analysis_min_interval", 4, raising=False)
        state = self._state()
        for _ in range(4):
            main._fast_path_allowed(state)
        assert main._fast_path_allowed(state) is False

    def test_running_the_analysis_restores_the_budget(self, monkeypatch):
        monkeypatch.setattr(main.settings, "analysis_min_interval", 2, raising=False)
        state = self._state()
        main._fast_path_allowed(state), main._fast_path_allowed(state)
        assert main._fast_path_allowed(state) is False
        state.rounds_since_analysis = 0          # what evolve_* does before analysing
        assert main._fast_path_allowed(state) is True

    def test_a_ceiling_of_zero_keeps_the_old_always_fast_behaviour(self, monkeypatch):
        monkeypatch.setattr(main.settings, "analysis_min_interval", 0, raising=False)
        state = self._state()
        assert all(main._fast_path_allowed(state) for _ in range(50))


# ── seeding generation 0 ────────────────────────────────────────────────────
#
# Generation 0 is a seed, not something the loop learned, so it tracks the constant
# in the source. The refresh has to run whether or not the insert fired — an
# installed deployment already has the row, and that is exactly the case where an
# outdated seed would otherwise persist forever.

class _SeedConn:
    def __init__(self, count):
        self.count, self.executed = count, []

    async def fetchval(self, _sql, *_a):
        return self.count

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class TestEnsureBaseline:
    @pytest.mark.asyncio
    async def test_empty_table_inserts_and_then_refreshes(self):
        conn = _SeedConn(0)
        await PromptVariantManager("red")._ensure_baseline(conn)
        kinds = [sql.strip().split()[0].upper() for sql, _ in conn.executed]
        assert kinds == ["INSERT", "UPDATE"]

    @pytest.mark.asyncio
    async def test_existing_row_still_gets_the_refresh(self):
        """Regression: the refresh read a variable only bound on the insert branch,
        so every already-installed deployment raised instead of refreshing."""
        conn = _SeedConn(3)
        await PromptVariantManager("red")._ensure_baseline(conn)
        kinds = [sql.strip().split()[0].upper() for sql, _ in conn.executed]
        assert kinds == ["UPDATE"]

    @pytest.mark.asyncio
    async def test_the_refresh_carries_this_team_s_seed_text(self):
        conn = _SeedConn(3)
        await PromptVariantManager("blue")._ensure_baseline(conn)
        _sql, args = conn.executed[0]
        assert args[0] == "blue"
        assert args[1] == main._BASELINE_BLUE_PROMPT

"""
Layer-3 (prompt meta-optimization) regression tests.

Covers the two pure functions that decide whether the outer loop can learn:
the PSS phase delta that scores a prompt variant, and the Boltzmann selection
that spends that score.
"""

import main
import pytest
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

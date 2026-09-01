"""The attack library, and what decides which half of it is in play.

Every technique in the original library aims at making the target *say* something. A
target that can also *do* things fails a different way, so a second pool exists for
that — but only when the engagement actually exposes actions, since otherwise those
techniques describe capability the target does not have.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import strategies as S  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    """Weight learning persists to a file; keep tests off the real one."""
    monkeypatch.setattr(S, "_MEMORY_PATH", str(tmp_path / "w.json"))


# ── which pool is live ──────────────────────────────────────────────────────

def test_a_conversational_engagement_sees_only_disclosure_techniques():
    pool = S.available_techniques(actions_offered=False)
    assert set(pool) == set(S.TECHNIQUES)
    assert not (set(pool) & set(S.ACTION_TECHNIQUES))


def test_an_engagement_with_actions_sees_both_pools():
    """Both, not only the action pool: an engagement may still be about disclosure,
    and the target's actions can be a route to it."""
    pool = S.available_techniques(actions_offered=True)
    assert set(S.TECHNIQUES) <= set(pool)
    assert set(S.ACTION_TECHNIQUES) <= set(pool)


def test_pick_never_returns_an_action_technique_without_actions():
    for _ in range(200):
        technique, _c, _p = S.pick_layers(actions_offered=False)
        assert technique not in S.ACTION_TECHNIQUES


def test_action_techniques_are_actually_reachable_when_offered():
    picked = {S.pick_layers(actions_offered=True)[0] for _ in range(400)}
    assert picked & set(S.ACTION_TECHNIQUES), "action pool never sampled"


def test_pick_always_returns_a_real_technique_context_and_pressure():
    for offered in (False, True):
        t, c, p = S.pick_layers(actions_offered=offered)
        assert t in S.available_techniques(offered)
        assert c in S.CONTEXT_TEMPLATES
        assert p in S.PRESSURE_MODIFIERS


# ── the judge's hint, and avoidance ─────────────────────────────────────────

def test_a_hinted_action_technique_is_honoured_when_actions_are_offered():
    t, _c, _p = S.pick_layers(mutation_hint="chained_escalation", actions_offered=True)
    assert t == "chained_escalation"


def test_a_hinted_action_technique_is_ignored_when_there_are_no_actions():
    """The hint names capability this engagement does not have; fall back to a
    technique that can actually be composed rather than describing a phantom action."""
    t, _c, _p = S.pick_layers(mutation_hint="chained_escalation", actions_offered=False)
    assert t in S.TECHNIQUES


def test_avoided_techniques_are_skipped():
    avoid = list(S.TECHNIQUES)[:-1]
    kept = list(S.TECHNIQUES)[-1]
    for _ in range(50):
        assert S.pick_layers(avoid=avoid, actions_offered=False)[0] == kept


def test_avoiding_everything_still_returns_something_rather_than_failing():
    t, _c, _p = S.pick_layers(avoid=list(S.TECHNIQUES), actions_offered=False)
    assert t in S.TECHNIQUES


# ── learned weights cover both pools ────────────────────────────────────────

def test_weights_exist_for_action_techniques_too():
    w = S._load_weights()
    for name in S.ACTION_TECHNIQUES:
        assert name in w


def test_feedback_on_an_action_technique_is_learned_and_survives():
    S.record_feedback(suggested="prior_authorisation_claim", actions_offered=True)
    assert S._load_weights(actions_offered=True)["prior_authorisation_claim"] > 1.0
    S.record_feedback(avoid=["prior_authorisation_claim"], actions_offered=True)
    after = S._load_weights(actions_offered=True)["prior_authorisation_claim"]
    assert S._MIN_WEIGHT <= after < 1.5


def test_a_boosted_technique_is_picked_more_often():
    for _ in range(6):
        S.record_feedback(suggested="routine_operation", actions_offered=True)
    picked = [S.pick_layers(actions_offered=True)[0] for _ in range(400)]
    assert picked.count("routine_operation") > 400 / len(S.available_techniques(True))


# ── a weight is evidence about ONE kind of engagement ───────────────────────
#
# A weight says a technique worked; it does not say what it worked at. Learning
# that obfuscation extracts a held value is not evidence that obfuscation will make
# a target move money. Mixing them let a long run of disclosure battles hand every
# later action engagement a preference it had to out-compete from a standing start.

class TestShapeSeparation:
    def test_disclosure_learning_does_not_leak_into_an_action_engagement(self):
        for _ in range(8):
            S.record_feedback(suggested="encoding", actions_offered=False)
        assert S._load_weights(actions_offered=False)["encoding"] > 3.0
        assert S._load_weights(actions_offered=True)["encoding"] == 1.0

    def test_action_learning_does_not_leak_the_other_way(self):
        for _ in range(8):
            S.record_feedback(suggested="chained_escalation", actions_offered=True)
        assert S._load_weights(actions_offered=True)["chained_escalation"] > 3.0
        assert S._load_weights(actions_offered=False)["chained_escalation"] == 1.0

    def test_both_tables_persist_side_by_side(self):
        S.record_feedback(suggested="roleplay", actions_offered=False)
        S.record_feedback(suggested="routine_operation", actions_offered=True)
        assert S._load_weights(False)["roleplay"] > 1.0
        assert S._load_weights(True)["routine_operation"] > 1.0
        assert S._load_weights(True)["roleplay"] == 1.0
        assert S._load_weights(False)["routine_operation"] == 1.0

    def test_an_action_engagement_starts_the_action_pool_level(self):
        """The point of the split: no action technique arrives already behind."""
        for _ in range(10):
            S.record_feedback(suggested="encoding", actions_offered=False)
        w = S._load_weights(actions_offered=True)
        assert {w[t] for t in S.ACTION_TECHNIQUES} == {1.0}
        assert w["encoding"] == 1.0


class TestLegacyMemoryMigration:
    def test_a_flat_file_is_attributed_to_disclosure_not_discarded(self, tmp_path, monkeypatch):
        """A flat file predates the split and could only have come from
        disclosure-only battles — no action technique existed to be offered — so it
        belongs to that shape. Discarding it would throw away real history."""
        import json
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"encoding": 3.6, "authority_framing": 0.6}))
        monkeypatch.setattr(S, "_MEMORY_PATH", str(legacy))
        assert S._load_weights(actions_offered=False)["encoding"] == 3.6
        assert S._load_weights(actions_offered=False)["authority_framing"] == 0.6
        assert S._load_weights(actions_offered=True)["encoding"] == 1.0

    def test_migration_leaves_the_action_pool_untouched(self, tmp_path, monkeypatch):
        import json
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"encoding": 5.0}))
        monkeypatch.setattr(S, "_MEMORY_PATH", str(legacy))
        w = S._load_weights(actions_offered=True)
        assert {w[t] for t in S.ACTION_TECHNIQUES} == {1.0}

    def test_a_write_converts_the_file_to_the_split_form(self, tmp_path, monkeypatch):
        import json
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({"encoding": 2.0}))
        monkeypatch.setattr(S, "_MEMORY_PATH", str(legacy))
        S.record_feedback(suggested="roleplay", actions_offered=False)
        data = json.loads(legacy.read_text())
        assert "by_engagement_shape" in data
        # and the migrated history survived the conversion
        assert data["by_engagement_shape"]["disclosure"]["encoding"] == 2.0

    def test_unreadable_and_junk_files_yield_level_tables(self, tmp_path, monkeypatch):
        junk = tmp_path / "junk.json"
        junk.write_text("not json at all")
        monkeypatch.setattr(S, "_MEMORY_PATH", str(junk))
        for offered in (False, True):
            assert set(S._load_weights(offered).values()) == {1.0}

    def test_a_non_numeric_stored_weight_falls_back_to_level(self, tmp_path, monkeypatch):
        import json
        f = tmp_path / "w.json"
        f.write_text(json.dumps({"by_engagement_shape": {
            "disclosure": {"encoding": "very effective"}}}))
        monkeypatch.setattr(S, "_MEMORY_PATH", str(f))
        assert S._load_weights(False)["encoding"] == 1.0

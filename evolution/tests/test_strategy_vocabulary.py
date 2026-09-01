"""The wrapper must not pass down a strategy label the attacker never used.

Its analyzer invents a free-form label for the approach it wants next. The
downstream adapter looks the label up in its own technique table and ignores
anything it does not recognise — but the unrecognised label still replaced the
one label that would have steered it: the one the attacker declared on a round
it won.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from main import _keep_only_known_strategy  # noqa: E402


def test_invented_label_is_replaced_by_the_orchestrators_name():
    upstream = {"strategy_vocabulary": ["encoding", "policy_refactor"],
                "mutation_type": "policy_refactor"}
    merged = {**upstream, "mutation_type": "internal_debug_log_replay"}
    out = _keep_only_known_strategy(merged, upstream)
    assert out["mutation_type"] == "policy_refactor"
    assert out["mutation_type_rejected"] == "internal_debug_log_replay"


def test_invented_label_is_dropped_when_there_is_nothing_to_fall_back_to():
    upstream = {"strategy_vocabulary": ["encoding"]}
    out = _keep_only_known_strategy({**upstream, "mutation_type": "novel_pivot"}, upstream)
    assert out["mutation_type"] == ""


def test_a_known_label_passes_through():
    upstream = {"strategy_vocabulary": ["encoding", "roleplay"]}
    out = _keep_only_known_strategy({**upstream, "mutation_type": "roleplay"}, upstream)
    assert out["mutation_type"] == "roleplay"
    assert "mutation_type_rejected" not in out


def test_without_a_vocabulary_nothing_is_filtered():
    """An adapter that accepts free-form labels keeps working unchanged."""
    out = _keep_only_known_strategy({"mutation_type": "whatever_it_wants"}, {})
    assert out["mutation_type"] == "whatever_it_wants"


def test_empty_vocabulary_is_treated_as_no_vocabulary():
    out = _keep_only_known_strategy({"mutation_type": "x"}, {"strategy_vocabulary": []})
    assert out["mutation_type"] == "x"


def test_the_input_is_not_mutated_in_place():
    upstream = {"strategy_vocabulary": ["encoding"]}
    merged = {**upstream, "mutation_type": "invented"}
    _keep_only_known_strategy(merged, upstream)
    assert merged["mutation_type"] == "invented"

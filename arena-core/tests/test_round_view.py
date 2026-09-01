"""The stored round as a report consumer sees it.

An engagement about conduct rather than disclosure has its finding in the action
record, so that record has to survive storage and come back out in a usable shape.
It is stored as JSON text; a consumer should get a list.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from battle_controller import _round_view  # noqa: E402


def test_a_stored_record_comes_back_as_a_list():
    row = {"round": 1, "target_tool_calls":
           '[{"name": "transfer_funds", "executed": false, "rule": "enforcement:guarded"}]'}
    out = _round_view(row)
    assert out["target_tool_calls"][0]["name"] == "transfer_funds"
    assert out["target_tool_calls"][0]["executed"] is False


def test_a_conversational_round_reports_an_empty_list_not_a_null():
    """So a consumer never has to tell 'took no action' apart from 'this build did
    not record actions'."""
    assert _round_view({"round": 1, "target_tool_calls": None})["target_tool_calls"] == []


def test_a_legacy_row_without_the_column_reports_an_empty_list():
    assert _round_view({"round": 1})["target_tool_calls"] == []


def test_unreadable_stored_text_degrades_instead_of_failing_the_report():
    assert _round_view({"round": 1, "target_tool_calls": "{not json"})["target_tool_calls"] == []


def test_every_other_field_is_passed_through_untouched():
    row = {"round": 3, "attack_payload": "x", "harmfulness_score": 0.25,
           "target_tool_calls": None}
    out = _round_view(row)
    assert out["round"] == 3 and out["attack_payload"] == "x"
    assert out["harmfulness_score"] == 0.25

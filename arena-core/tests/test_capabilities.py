"""Reading a connected project's declared capabilities.

The contract asks for an object. A project that sends a list of capability names is
not wrong about what it can do, only about our punctuation, and dropping that
declaration silently made the project look incapable with nothing anywhere saying
why — so a list is read rather than discarded. Anything genuinely unreadable yields
an empty declaration, which admission reports as a named blocker instead of waving
the project through.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import coerce_capabilities  # noqa: E402


# ── the shape the contract asks for ─────────────────────────────────────────

def test_an_object_is_taken_as_declared():
    caps = {"supports_input_guard": True, "defense_type": "classifier"}
    assert coerce_capabilities(caps) == caps


def test_the_result_is_a_copy_so_a_later_edit_cannot_reach_back():
    original = {"supports_input_guard": True}
    coerce_capabilities(original)["supports_input_guard"] = False
    assert original["supports_input_guard"] is True


def test_false_values_are_preserved_not_dropped():
    """A project that explicitly declares it does NOT do something must be believed."""
    caps = coerce_capabilities({"supports_output_guard": False, "supports_input_guard": True})
    assert caps["supports_output_guard"] is False


# ── the shape some projects actually send ───────────────────────────────────

def test_a_list_of_names_becomes_a_declaration():
    assert coerce_capabilities(["supports_input_guard", "keyword_filter"]) == {
        "supports_input_guard": True, "keyword_filter": True,
    }


def test_a_tuple_works_the_same_way():
    assert coerce_capabilities(("supports_attack_generation",)) == {
        "supports_attack_generation": True,
    }


def test_blank_and_non_string_entries_are_skipped():
    assert coerce_capabilities(["supports_input_guard", "", "   ", 7, None]) == {
        "supports_input_guard": True,
    }


def test_an_empty_list_declares_nothing_rather_than_something():
    assert coerce_capabilities([]) == {}


# ── everything else declares nothing, and says so by being empty ────────────

def test_unreadable_forms_yield_an_empty_declaration():
    for raw in (None, "supports_input_guard", 42, True, object()):
        assert coerce_capabilities(raw) == {}, raw


# ── the projects this repository ships must still be admitted ───────────────

def test_the_bundled_red_declaration_still_reads_as_attack_capable():
    caps = coerce_capabilities({
        "supports_attack_generation": True,
        "attack_type": "layered_composition",
        "is_platform_default": True,
    })
    assert caps.get("supports_attack_generation") or caps.get("attack_type")


def test_the_bundled_blue_declaration_still_reads_as_gate_capable():
    caps = coerce_capabilities({
        "supports_input_guard": True,
        "supports_output_guard": True,
        "defense_type": "intent_classifier",
    })
    assert caps.get("supports_input_guard") or caps.get("supports_output_guard")


def test_a_list_form_blue_now_reads_as_gate_capable():
    """Before this, such a project was admitted as capability-less and then ran as a
    passthrough — a scored battle against a defence the platform never verified."""
    caps = coerce_capabilities(["supports_input_guard"])
    assert caps.get("supports_input_guard") is True


# ── which side owns the build context ───────────────────────────────────────
#
# A project may be connected at runtime from anywhere the platform can reach. The
# build context and the managed container describe the ONE project the operator
# wired up in configuration; attributing them to any service registered under the
# same role had the platform promise a rebuild it would have aimed at the wrong
# source.

import main as arena_main  # noqa: E402

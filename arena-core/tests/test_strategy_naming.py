"""The platform may only name a strategy the participant itself has named.

The defect these cover: the judge invented technique names and handed them to the
attacker as the technique to use next. Names it invented on a WIN matched nothing
in the attacker's pool, so a winning round taught the attacker nothing, while the
name it invented on a low-evasion LOSS did match and was forced round after round.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from battle_loop import _declared_strategy_label, _name_the_next_move  # noqa: E402
from models import BattleSession  # noqa: E402


def _session() -> BattleSession:
    return BattleSession(id="s1", red_service_id="r", blue_service_id="b")


def _attack(technique: str = "", attack_type: str = "") -> dict:
    attack: dict = {"attack_payload": "p"}
    if technique:
        attack["metadata"] = {"technique": technique}
    if attack_type:
        attack["attack_type"] = attack_type
    return attack


def test_label_prefers_the_adapters_own_technique_field():
    assert _declared_strategy_label(
        _attack(technique="policy_refactor", attack_type="policy_refactor+audit+time")
    ) == "policy_refactor"


def test_label_falls_back_to_attack_type():
    assert _declared_strategy_label(_attack(attack_type="layered")) == "layered"


def test_label_absent_when_the_adapter_declares_nothing():
    assert _declared_strategy_label({"attack_payload": "p"}) == ""


def test_a_win_names_the_technique_the_attacker_used():
    hints = {"red": {"suggested_direction": "keep going"}}
    _name_the_next_move(_session(), hints, _attack(technique="policy_refactor"), "success")
    assert hints["red"]["suggested_mutation"] == "policy_refactor"
    assert hints["red"]["mutation_type"] == "policy_refactor"
    assert hints["red"]["last_outcome"] == "success"


def test_a_loss_names_nothing_and_leaves_the_attacker_free():
    hints = {"red": {"suggested_direction": "obscure the intent"}}
    _name_the_next_move(_session(), hints, _attack(technique="encoding"), "failure")
    assert "mutation_type" not in hints["red"]
    assert "suggested_mutation" not in hints["red"]
    assert hints["red"]["suggested_direction"] == "obscure the intent"


def test_a_name_the_attacker_has_never_used_is_dropped():
    """The exact shape of the original defect: an invented word is not passed on."""
    hints = {"red": {"suggested_mutation": "authority_escalation"}}
    _name_the_next_move(_session(), hints, _attack(technique="encoding"), "failure")
    assert "suggested_mutation" not in hints["red"]


def test_a_name_the_attacker_has_used_before_survives():
    session = _session()
    first = {"red": {}}
    _name_the_next_move(session, first, _attack(technique="roleplay"), "failure")
    second = {"red": {"suggested_mutation": "roleplay"}}
    _name_the_next_move(session, second, _attack(technique="encoding"), "failure")
    assert second["red"]["suggested_mutation"] == "roleplay"


def test_vocabulary_accumulates_across_rounds():
    session = _session()
    for technique in ("encoding", "roleplay", "policy_refactor"):
        hints = {"red": {}}
        _name_the_next_move(session, hints, _attack(technique=technique), "failure")
    assert hints["red"]["strategy_vocabulary"] == ["encoding", "policy_refactor", "roleplay"]


def test_blue_hints_are_left_alone():
    hints = {"red": {}, "blue": {"suggested_rule": "block base64"}}
    _name_the_next_move(_session(), hints, _attack(technique="encoding"), "failure")
    assert hints["blue"] == {"suggested_rule": "block base64"}


@pytest.mark.parametrize("hints", [{}, {"red": None}, {"red": "not a dict"}])
def test_malformed_hints_do_not_raise(hints):
    _name_the_next_move(_session(), hints, _attack(technique="encoding"), "success")

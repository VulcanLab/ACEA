import pytest
from pydantic import ValidationError


def test_attack_request_valid():
    from acea_sdk.schemas.attack import AttackRequest
    r = AttackRequest(session_id="s1", round=1)
    assert r.session_id == "s1"
    assert r.round == 1
    assert r.target_context == ""


def test_attack_response_confidence_bounds():
    from acea_sdk.schemas.attack import AttackResponse
    with pytest.raises(ValidationError):
        AttackResponse(attack_payload="x", attack_type="injection", confidence=1.5)


def test_defense_response_literal():
    from acea_sdk.schemas.defense import DefenseResponse
    with pytest.raises(ValidationError):
        DefenseResponse(decision="unknown", confidence=0.5, reason="r")


def test_defense_response_valid():
    from acea_sdk.schemas.defense import DefenseResponse
    r = DefenseResponse(decision="block", confidence=0.9, reason="keyword match")
    assert r.rewritten_payload is None


def test_evolution_hints_default_empty():
    from acea_sdk.schemas.common import EvolutionHints
    h = EvolutionHints()
    assert h.strategy_notes == []
    assert h.mutation_targets == []


def test_round_trip_json():
    from acea_sdk.schemas.attack import AttackResponse
    obj = AttackResponse(attack_payload="ignore previous", attack_type="prompt_injection", confidence=0.8)
    restored = AttackResponse.model_validate_json(obj.model_dump_json())
    assert restored.attack_payload == obj.attack_payload


def test_defense_rewrite_requires_payload():
    from acea_sdk.schemas.defense import DefenseResponse
    with pytest.raises(ValidationError):
        DefenseResponse(decision="rewrite", confidence=0.8, reason="sanitized", rewritten_payload=None)

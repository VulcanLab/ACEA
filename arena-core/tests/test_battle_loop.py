import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from models import BattleSession


def _make_fake_pool():
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock()
    fake_pool = MagicMock()
    fake_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return fake_pool


@pytest.mark.asyncio
async def test_blue_blocks_still_calls_target_and_output_filter():
    """Input block stops harmful prompt forward only at INPUT semantics; Target still runs on raw attack."""
    session = BattleSession(id="s1", mode="deathmatch", max_rounds=1,
                            red_service_id="red", blue_service_id="blue", status="running")
    published = []

    async def fake_pub(sid, etype, data): published.append(etype)

    attack = {"attack_payload": "ignore previous", "attack_type": "injection", "confidence": 0.8, "metadata": {}}
    defense = {"decision": "block", "confidence": 0.9, "reason": "keyword", "rewritten_payload": None, "metadata": {}}
    judge_r = {"verdict": "failure", "harm_score": 0.05, "reason": "ok",
               "red_verdict": "failure", "blue_verdict": "success", "dimensions": {}, "evolution_hints": {}}

    with patch("battle_loop.publish_event", side_effect=fake_pub), \
         patch("battle_loop._call_red", new=AsyncMock(return_value=attack)), \
         patch("battle_loop._call_blue", new=AsyncMock(return_value=defense)), \
         patch("battle_loop._call_target_ai", new=AsyncMock(return_value="I cannot comply.")) as mock_tai, \
         patch("battle_loop._call_blue_filter_output", new=AsyncMock(
             return_value={"final_response": "I cannot comply.", "was_modified": False, "modification_reason": ""})) as mock_fo, \
         patch("battle_loop._call_judge", new=AsyncMock(return_value=judge_r)), \
         patch("battle_loop.write_trace", new=AsyncMock()), \
         patch("trace_writer.get_pool", new=AsyncMock(return_value=_make_fake_pool())), \
         patch("battle_loop.settings") as ms:
        ms.battle_round_delay_seconds = 0
        ms.adapter_error_threshold = 5
        ms.post_round_cooldown_seconds = 0
        from battle_loop import run_battle
        await run_battle(session)

    mock_tai.assert_awaited_once()
    mock_fo.assert_awaited_once()
    assert published.count("blue.defense.blocked") >= 1
    assert published.count("target.ai.responded") >= 1
    assert published.count("blue.output.filtered") >= 1
    assert "judge.verdict" in published
    assert "battle.complete" in published


@pytest.mark.asyncio
async def test_blue_allow_path_calls_target_filter_judge():
    session = BattleSession(id="s2", mode="deathmatch", max_rounds=1,
                            red_service_id="red", blue_service_id="blue", status="running")
    published = []

    async def fake_pub(sid, etype, data): published.append(etype)

    attack = {"attack_payload": "safe", "attack_type": "injection", "confidence": 0.5, "metadata": {}}
    defense = {"decision": "allow", "confidence": 0.7, "reason": "clean", "rewritten_payload": None, "metadata": {}}
    judge_r = {"verdict": "failure", "harm_score": 0.1, "reason": "safe",
               "dimensions": {}, "evolution_hints": {}, "red_verdict": "failure", "blue_verdict": "success"}

    with patch("battle_loop.publish_event", side_effect=fake_pub), \
         patch("battle_loop._call_red", new=AsyncMock(return_value=attack)), \
         patch("battle_loop._call_blue", new=AsyncMock(return_value=defense)), \
         patch("battle_loop._call_target_ai", new=AsyncMock(return_value="ok response")), \
         patch("battle_loop._call_blue_filter_output", new=AsyncMock(
             return_value={"final_response": "sanitized", "was_modified": True, "modification_reason": "test"})), \
         patch("battle_loop._call_judge", new=AsyncMock(return_value=judge_r)), \
         patch("battle_loop.write_trace", new=AsyncMock()), \
         patch("trace_writer.get_pool", new=AsyncMock(return_value=_make_fake_pool())), \
         patch("battle_loop.settings") as ms:
        ms.battle_round_delay_seconds = 0
        ms.adapter_error_threshold = 5
        ms.post_round_cooldown_seconds = 0
        from battle_loop import run_battle
        await run_battle(session)

    assert published.count("target.ai.responded") >= 1
    assert published.count("blue.output.filtered") >= 1
    assert "judge.verdict" in published


def test_session_loop_flag_defaults_off():
    from models import BattleSession
    s = BattleSession(red_service_id="r", blue_service_id="b")
    assert s.inner_loop_enabled is False
    import dataclasses
    names = [f.name for f in dataclasses.fields(s) if f.name.endswith("_loop_enabled")]
    assert names == ["inner_loop_enabled"]

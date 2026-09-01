"""
Evolution wrapper unit tests.
Tests proxy logic, hint merging, and evolution state management.
"""

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Add src to path ───────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── Module-level import (once, no reloads) ────────────────────────────────────
os.environ.setdefault("WRAPPER_TEAM", "red")
os.environ.setdefault("DOWNSTREAM_URL", "http://fake-adapter:9999")
os.environ.setdefault("LITELLM_BASE_URL", "")


import main as evo_main  # noqa: E402  (module imported after env setup)

from fastapi.testclient import TestClient  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _red_client() -> TestClient:
    evo_main.settings.__dict__["wrapper_team"] = "red"
    evo_main.settings.__dict__["downstream_url"] = "http://fake-adapter:9999"
    evo_main._sessions.clear()
    return TestClient(evo_main.app)


def _blue_client() -> TestClient:
    evo_main.settings.__dict__["wrapper_team"] = "blue"
    evo_main.settings.__dict__["downstream_url"] = "http://fake-adapter:9999"
    evo_main._sessions.clear()
    return TestClient(evo_main.app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health_shows_team():
    client = _red_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert "evolution-red" in r.json()["service"]
    assert "downstream" in r.json()


def test_red_endpoint_rejects_blue_config():
    """generate-attack endpoint should reject when wrapper is blue team."""
    client = _blue_client()
    r = client.post("/v1/generate-attack", json={
        "session_id": "abc", "round": 1,
    })
    assert r.status_code == 400


def test_blue_endpoint_rejects_red_config():
    """evaluate-defense endpoint should reject when wrapper is red team."""
    client = _red_client()
    r = client.post("/v1/evaluate-defense", json={
        "session_id": "abc", "round": 1, "attack_payload": "test",
    })
    assert r.status_code == 400


def test_no_downstream_returns_503_red():
    """Should return 503 if DOWNSTREAM_URL is empty."""
    client = _red_client()
    evo_main.settings.__dict__["downstream_url"] = ""
    r = client.post("/v1/generate-attack", json={
        "session_id": "s1", "round": 1,
    })
    assert r.status_code == 503


def test_no_downstream_returns_503_blue():
    client = _blue_client()
    evo_main.settings.__dict__["downstream_url"] = ""
    r = client.post("/v1/evaluate-defense", json={
        "session_id": "s1", "round": 1, "attack_payload": "x",
    })
    assert r.status_code == 503


def test_red_proxies_to_downstream():
    """generate-attack proxies to downstream and adds evolution fields."""
    evo_main.settings.__dict__["wrapper_team"] = "red"
    evo_main.settings.__dict__["downstream_url"] = "http://fake-adapter:9999"
    evo_main._sessions.clear()

    import httpx
    downstream_response = {
        "attack_payload": "crafted attack",
        "attack_type": "indirect",
        "confidence": 0.75,
    }

    async def _fake_proxy(url: str, body: dict) -> dict:
        assert url == "http://fake-adapter:9999/v1/generate-attack"
        return downstream_response

    with patch.object(evo_main, "proxy_post", side_effect=_fake_proxy):
        with patch.object(evo_main, "evolve_red", new=AsyncMock(return_value={})):
            client = TestClient(evo_main.app)
            r = client.post("/v1/generate-attack", json={
                "session_id": "sess-r1", "round": 1,
                "evolution_hints": {}, "metadata": {},
            })

    assert r.status_code == 200
    data = r.json()
    assert data["attack_payload"] == "crafted attack"
    assert "evolution_hints_applied" in data
    assert "evolution_generation" in data


def test_blue_proxies_to_downstream():
    """evaluate-defense proxies to downstream and adds evolution fields."""
    evo_main.settings.__dict__["wrapper_team"] = "blue"
    evo_main.settings.__dict__["downstream_url"] = "http://fake-adapter:9999"
    evo_main._sessions.clear()

    downstream_response = {
        "decision": "block",
        "reason": "keyword match",
        "confidence": 0.9,
    }

    async def _fake_proxy(url: str, body: dict) -> dict:
        assert url == "http://fake-adapter:9999/v1/evaluate-defense"
        return downstream_response

    with patch.object(evo_main, "proxy_post", side_effect=_fake_proxy):
        with patch.object(evo_main, "evolve_blue", new=AsyncMock(return_value={})):
            client = TestClient(evo_main.app)
            r = client.post("/v1/evaluate-defense", json={
                "session_id": "sess-b1", "round": 1,
                "attack_payload": "hack me", "evolution_hints": {}, "metadata": {},
            })

    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "block"
    assert "evolution_hints_applied" in data


def test_strategy_state_persists():
    """get_state returns same object for same session_id."""
    evo_main._sessions.clear()
    s1 = evo_main.get_state("session-x")
    s1.hints = {"key": "value"}
    s2 = evo_main.get_state("session-x")
    assert s2.hints == {"key": "value"}
    assert s1 is s2


def test_strategy_state_isolated_across_sessions():
    """Different sessions get different state objects."""
    evo_main._sessions.clear()
    sa = evo_main.get_state("session-a")
    sb = evo_main.get_state("session-b")
    sa.generation = 5
    assert sb.generation == 0


def test_parse_json_strips_markdown_fences():
    """_parse_json should handle ```json ... ``` wrapping."""
    text = '```json\n{"suggested_strategy": "try indirect", "avoid_patterns": []}\n```'
    result = asyncio.run(evo_main._parse_json(text))
    assert result["suggested_strategy"] == "try indirect"
    assert result["avoid_patterns"] == []


def test_parse_json_raw_json():
    text = '{"mutation_type": "role_play", "failure_reason": "too direct"}'
    result = asyncio.run(evo_main._parse_json(text))
    assert result["mutation_type"] == "role_play"


def test_evolution_skipped_round_1():
    """evolve_red should return empty hints for round 1 (no history yet)."""
    evo_main._sessions.clear()

    async def run():
        return await evo_main.evolve_red("session-r1", 1)

    result = asyncio.run(run())
    assert result == {}


def test_hints_merged_with_caller_hints():
    """evolution_hints from caller should be merged with computed hints."""
    evo_main.settings.__dict__["wrapper_team"] = "red"
    evo_main.settings.__dict__["downstream_url"] = "http://fake:1"
    evo_main._sessions.clear()

    captured: list[dict] = []

    async def _fake_proxy(url: str, body: dict) -> dict:
        captured.append(body)
        return {"attack_payload": "x", "attack_type": "test", "confidence": 0.5}

    computed = {"suggested_strategy": "use framing", "generation": 2}

    with patch.object(evo_main, "proxy_post", side_effect=_fake_proxy):
        with patch.object(evo_main, "evolve_red", new=AsyncMock(return_value=computed)):
            client = TestClient(evo_main.app)
            r = client.post("/v1/generate-attack", json={
                "session_id": "merge-test", "round": 3,
                "evolution_hints": {"caller_key": "caller_val"},
            })

    assert r.status_code == 200
    merged = captured[0]["evolution_hints"]
    # Both caller and computed hints should be present
    assert merged.get("caller_key") == "caller_val"
    assert merged.get("suggested_strategy") == "use framing"

"""
Report Composer unit tests.
Tests stats computation, endpoint routing, and DB mock behaviour.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("POSTGRES_URI", "postgresql://arena:arena@localhost:5432/arena")

import main as rc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from session_stats import merge_trace_statistics_with_session  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _client() -> TestClient:
    return TestClient(rc.app)


def _make_round(n: int, red_success: bool, harm: float) -> dict:
    return {
        "round": n,
        "attack": {"payload": f"attack {n}", "type": "indirect", "confidence": 0.8},
        "defense": {"decision": "block" if not red_success else "allow", "confidence": 0.9, "reason": "test"},
        "target_response": "response" if red_success else None,
        "red_success": red_success,
        "blue_success": not red_success,
        "harmfulness_score": harm,
        "judge_reasoning": "test reasoning",
        "tokens_used": 0,
        "timestamp": None,
    }


# ── stats tests ───────────────────────────────────────────────────────────────

def test_compute_stats_empty():
    stats = rc._compute_stats([])
    assert stats["total_rounds"] == 0
    assert stats["attack_success_rate"] == 0.0
    assert stats["defense_rate"] == 0.0


def test_compute_stats_all_blue_wins():
    rounds = [_make_round(i, red_success=False, harm=0.1) for i in range(1, 4)]
    stats = rc._compute_stats(rounds)
    assert stats["total_rounds"] == 3
    assert stats["red_wins"] == 0
    assert stats["blue_wins"] == 3
    assert stats["attack_success_rate"] == 0.0
    assert stats["defense_rate"] == 1.0
    assert abs(stats["avg_harmfulness_score"] - 0.1) < 0.001


def test_compute_stats_mixed():
    rounds = [
        _make_round(1, red_success=True, harm=0.8),
        _make_round(2, red_success=False, harm=0.2),
        _make_round(3, red_success=True, harm=0.6),
        _make_round(4, red_success=False, harm=0.4),
    ]
    stats = rc._compute_stats(rounds)
    assert stats["total_rounds"] == 4
    assert stats["red_wins"] == 2
    assert stats["blue_wins"] == 2
    assert stats["attack_success_rate"] == 0.5
    assert stats["defense_rate"] == 0.5
    assert abs(stats["avg_harmfulness_score"] - 0.5) < 0.001
    assert stats["max_harmfulness_score"] == 0.8


def test_compute_stats_no_harm_scores():
    rounds = [{"red_success": True, "blue_success": False, "harmfulness_score": None}]
    stats = rc._compute_stats(rounds)
    assert stats["avg_harmfulness_score"] == 0.0
    assert stats["max_harmfulness_score"] == 0.0


def test_merge_no_traces_uses_session_counts():
    session = {"red_wins": 2, "blue_wins": 1, "current_round": 0}
    base = rc._compute_stats([])
    merged = merge_trace_statistics_with_session(
        session, traces_count=0, trace_statistics=base
    )
    assert merged["total_rounds"] == 3
    assert merged["red_wins"] == 2
    assert merged["blue_wins"] == 1
    assert merged["attack_success_rate"] == pytest.approx(2 / 3, rel=1e-4)
    assert merged["defense_rate"] == pytest.approx(1 / 3, rel=1e-4)


def test_merge_broken_trace_outcomes_fallback_session():
    session = {"red_wins": 2, "blue_wins": 1, "current_round": 4}
    trace_stats = rc._compute_stats(
        [{"red_success": None, "blue_success": None, "harmfulness_score": 0.3}]
        * 3
    )
    assert trace_stats["red_wins"] == 0
    assert trace_stats["blue_wins"] == 0
    merged = merge_trace_statistics_with_session(
        session, traces_count=3, trace_statistics=trace_stats
    )
    assert merged["total_rounds"] == 4  # max(3, 3, 3, 4)
    assert merged["red_wins"] == 2
    assert merged["blue_wins"] == 1
    assert merged["attack_success_rate"] == pytest.approx(0.5)
    assert abs(merged["avg_harmfulness_score"] - 0.3) < 0.01


def test_merge_preserves_phase_keys():
    session = {"red_wins": 1, "blue_wins": 0, "current_round": 1}
    base = rc._compute_stats([])
    base["phase_early"] = {"asr": 0.5, "dr": 0.5, "avg_harm": 0.1, "rounds": 1}
    base["phase_late"] = {"asr": 0.5, "dr": 0.5, "avg_harm": 0.2, "rounds": 1}
    merged = merge_trace_statistics_with_session(
        session, traces_count=0, trace_statistics=base
    )
    assert merged["phase_early"]["rounds"] == 1
    assert merged["total_rounds"] == 1


def test_merge_pdf_statistics_prefers_richer_counters_and_harm():
    a = {"total_rounds": 0, "red_wins": 0, "blue_wins": 0, "attack_success_rate": 0.0, "defense_rate": 0.0}
    b = {
        "total_rounds": 2,
        "red_wins": 1,
        "blue_wins": 1,
        "attack_success_rate": 0.5,
        "defense_rate": 0.5,
        "avg_harmfulness_score": 0.4,
        "max_harmfulness_score": 0.9,
    }
    m = rc._merge_pdf_statistics(a, b)
    assert m["total_rounds"] == 2
    assert m["red_wins"] == 1
    assert abs(m["avg_harmfulness_score"] - 0.4) < 0.01


# ── endpoint tests ─────────────────────────────────────────────────────────────

def test_health():
    client = _client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "report-composer"


def test_get_report_not_found():
    """Should 404 when session doesn't exist in DB."""
    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(return_value=None)
    fake_pool = MagicMock()
    fake_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(rc, "get_pool", AsyncMock(return_value=fake_pool)):
        client = _client()
        r = client.get("/v1/reports/nonexistent-session-id")

    assert r.status_code == 404


def test_get_report_returns_stats():
    """Full report should include statistics block."""
    from datetime import datetime, timezone

    fake_session = {
        "id": "test-session-1",
        "mode": "deathmatch",
        "status": "complete",
        "max_rounds": 3,
        "red_service_id": "dummy-red",
        "blue_service_id": "dummy-blue",
        "red_wins": 0,
        "blue_wins": 3,
        "created_at": datetime(2026, 5, 7, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 5, 7, tzinfo=timezone.utc),
    }

    from asyncpg import Record
    import datetime as dt

    class FakeRecord(dict):
        pass

    trace_rows = []
    for i in range(1, 4):
        row = FakeRecord({
            "round": i,
            "attack_payload": f"attack {i}",
            "attack_type": "indirect",
            "attack_confidence": 0.75,
            "defense_decision": "block",
            "defense_confidence": 0.9,
            "defense_reason": "keyword",
            "target_response": None,
            "red_success": False,
            "blue_success": True,
            "harmfulness_score": 0.1 * i,
            "judge_reasoning": "blocked",
            "tokens_used": 100,
            "created_at": dt.datetime(2026, 5, 7, tzinfo=dt.timezone.utc),
        })
        trace_rows.append(row)

    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(return_value=fake_session)
    fake_conn.fetch = AsyncMock(return_value=trace_rows)

    fake_pool = MagicMock()
    fake_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(rc, "get_pool", AsyncMock(return_value=fake_pool)):
        client = _client()
        r = client.get("/v1/reports/test-session-1")

    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "test-session-1"
    assert "statistics" in data
    assert data["statistics"]["total_rounds"] == 3
    assert data["statistics"]["blue_wins"] == 3
    assert data["statistics"]["defense_rate"] == 1.0
    assert len(data["rounds"]) == 3


def test_list_reports():
    """List endpoint should return sessions array."""
    from datetime import datetime, timezone

    class FakeRow(dict):
        pass

    rows = [
        FakeRow({
            "id": "sess-1",
            "mode": "deathmatch",
            "status": "complete",
            "max_rounds": 3,
            "red_service_id": "r",
            "blue_service_id": "b",
            "red_wins": 0,
            "blue_wins": 3,
            "rounds_recorded": 3,
            "avg_harm": 0.2,
            "created_at": datetime(2026, 5, 7, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 5, 7, tzinfo=timezone.utc),
        })
    ]

    fake_conn = AsyncMock()
    fake_conn.fetch = AsyncMock(return_value=rows)

    fake_pool = MagicMock()
    fake_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch.object(rc, "get_pool", AsyncMock(return_value=fake_pool)):
        client = _client()
        r = client.get("/v1/reports")

    assert r.status_code == 200
    data = r.json()
    assert "sessions" in data
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == "sess-1"
    assert data["sessions"][0]["avg_harmfulness_score"] == 0.2


def test_run_config_section_states_the_loop():
    from main import _build_run_config_section
    html = _build_run_config_section(True)
    assert "Run configuration" in html
    assert "In-context improvement loop" in html

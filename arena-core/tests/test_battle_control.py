import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from fastapi.testclient import TestClient
from fastapi import FastAPI
from battle_controller import router, _sessions
from models import BattleSession

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def setup_function():
    _sessions.clear()


def _seed_session() -> str:
    s = BattleSession(id="s1", mode="deathmatch", max_rounds=3,
                      red_service_id="r", blue_service_id="b", status="running")
    _sessions[s.id] = s
    return s.id


def test_pause_sets_status():
    sid = _seed_session()
    resp = client.post(f"/api/battles/{sid}/pause")
    assert resp.status_code == 200
    assert _sessions[sid].status == "paused"


def test_resume_sets_status():
    sid = _seed_session()
    _sessions[sid].status = "paused"
    resp = client.post(f"/api/battles/{sid}/resume")
    assert resp.status_code == 200
    assert _sessions[sid].status == "running"


def test_pause_unknown_session_404():
    resp = client.post("/api/battles/nonexistent/pause")
    assert resp.status_code == 404


def test_battle_request_defaults_the_loop_off():
    from battle_controller import BattleRequest
    r = BattleRequest(red_service_id="r", blue_service_id="b")
    assert r.inner_loop_enabled is False


def test_battle_request_accepts_the_loop_flag():
    from battle_controller import BattleRequest
    r = BattleRequest(red_service_id="r", blue_service_id="b",
                      inner_loop_enabled=True)
    assert r.inner_loop_enabled is True


def test_battle_request_exposes_one_loop_flag():
    """A launch request carries exactly one improvement switch — the in-context
    loop. Unknown extras are ignored by the model, so assert the surface itself."""
    from battle_controller import BattleRequest
    r = BattleRequest(red_service_id="r", blue_service_id="b")
    assert [f for f in type(r).model_fields if f.endswith("_loop_enabled")] == ["inner_loop_enabled"]

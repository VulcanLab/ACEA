import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from fastapi.testclient import TestClient
from fastapi import FastAPI
import registry
from registry import router, _services

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def setup_function():
    _services.clear()


@pytest.fixture
def admits(monkeypatch):
    """Registration validates every URL, so a test that is about the registry
    rather than about validation says so by stubbing the validator. Nothing is
    exempt by name — a URL cannot buy a pass by what it is called."""
    async def _ok(url, team):
        return True, "", {"supports_input_guard": True}
    monkeypatch.setattr(registry, "validate_adapter", _ok)


@pytest.fixture
def refuses(monkeypatch):
    async def _no(url, team):
        return False, "health check failed", {}
    monkeypatch.setattr(registry, "validate_adapter", _no)


def test_register_service(admits):
    r = client.post("/api/services", json={"id": "r1", "name": "R", "url": "http://adapter:1", "type": "red"})
    assert r.status_code == 200
    assert r.json()["id"] == "r1"


def test_register_rejects_unvalidatable_adapter(refuses):
    r = client.post("/api/services", json={"id": "r2", "name": "R", "url": "http://adapter:1", "type": "red"})
    assert r.status_code == 400
    assert "ASAP validation failed" in r.json()["detail"]


def test_register_validates_urls_that_look_like_stubs(refuses):
    """A URL containing "dummy" used to skip validation entirely."""
    r = client.post("/api/services", json={"id": "r3", "name": "R", "url": "http://dummy:1", "type": "red"})
    assert r.status_code == 400


def test_list_services(admits):
    client.post("/api/services", json={"id": "s1", "name": "A", "url": "http://adapter:1", "type": "red"})
    assert any(s["id"] == "s1" for s in client.get("/api/services").json())


def test_get_not_found():
    assert client.get("/api/services/nope").status_code == 404


def test_delete(admits):
    client.post("/api/services", json={"id": "d1", "name": "D", "url": "http://adapter:1", "type": "blue"})
    assert client.delete("/api/services/d1").status_code == 200
    assert client.get("/api/services/d1").status_code == 404

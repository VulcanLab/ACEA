"""Where a side's calls actually go.

The evolution wrapper holds the in-context layers — batch analysis, cross-session
memory, prompt variants — and proxies to the project in front of which it sits. It is
registered but hidden from the operator's picker as internal plumbing, which is
correct; what was missing is that nothing ever routed through it, so enabling the
in-context loop ran the per-round judge hints and none of those layers.

The wrapper is matched by the downstream it reports on its own health check, so no
mapping of wrapper-to-project lives in the orchestrator and a project registered under
any name is still matched.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import battle_loop  # noqa: E402
from battle_loop import _adapter_endpoint  # noqa: E402


def _svc(sid, url, team, caps=None):
    return SimpleNamespace(id=sid, url=url, type=team, capabilities=caps or {})


def _registry(monkeypatch, services):
    by_id = {s.id: s for s in services}
    monkeypatch.setattr(battle_loop, "get_registry", lambda: by_id)


ADAPTER = _svc("red1", "http://red-project:9010", "red")
WRAPPER = _svc("wrap1", "http://evolution-red:8003", "red",
               {"evolution_wrapper": True, "downstream": "http://red-project:9010"})


def test_loop_off_calls_the_project_directly(monkeypatch):
    """No analysis models are spent on a plain battle."""
    _registry(monkeypatch, [ADAPTER, WRAPPER])
    assert _adapter_endpoint("red1") == "http://red-project:9010"


def test_loop_on_routes_through_the_wrapper_in_front_of_that_project(monkeypatch):
    _registry(monkeypatch, [ADAPTER, WRAPPER])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://evolution-red:8003"


def test_no_wrapper_configured_falls_back_to_the_project(monkeypatch):
    """A deployment without the wrapper still battles; it just gets the judge hints."""
    _registry(monkeypatch, [ADAPTER])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://red-project:9010"


def test_a_wrapper_pointed_somewhere_else_is_not_used(monkeypatch):
    other = _svc("wrap2", "http://evolution-red:8003", "red",
                 {"evolution_wrapper": True, "downstream": "http://a-different-project:9099"})
    _registry(monkeypatch, [ADAPTER, other])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://red-project:9010"


def test_a_trailing_slash_does_not_break_the_match(monkeypatch):
    sloppy = _svc("wrap3", "http://evolution-red:8003", "red",
                  {"evolution_wrapper": True, "downstream": "http://red-project:9010/"})
    _registry(monkeypatch, [ADAPTER, sloppy])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://evolution-red:8003"


def test_the_other_side_s_wrapper_is_never_used(monkeypatch):
    """A blue wrapper that happens to report the same downstream must not capture a
    red call — the endpoints it serves are the other role's."""
    blue_wrapper = _svc("wrapb", "http://evolution-blue:8004", "blue",
                        {"evolution_wrapper": True, "downstream": "http://red-project:9010"})
    _registry(monkeypatch, [ADAPTER, blue_wrapper])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://red-project:9010"


def test_a_wrapper_that_reports_no_downstream_is_not_matched(monkeypatch):
    """An unconfigured wrapper must not silently swallow every project's traffic."""
    blank = _svc("wrap4", "http://evolution-red:8003", "red",
                 {"evolution_wrapper": True, "downstream": ""})
    _registry(monkeypatch, [ADAPTER, blank])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://red-project:9010"


def test_a_plain_adapter_is_never_mistaken_for_a_wrapper(monkeypatch):
    impostor = _svc("red2", "http://another:9011", "red",
                    {"downstream": "http://red-project:9010"})
    _registry(monkeypatch, [ADAPTER, impostor])
    assert _adapter_endpoint("red1", through_wrapper=True) == "http://red-project:9010"

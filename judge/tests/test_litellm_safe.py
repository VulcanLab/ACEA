"""Tests for the model-capability adaptation layer.

The platform must work with whatever model an operator configures. Families differ
in which request parameters they accept, and the rejection comes back from the
endpoint that serves the model, so the client cannot know in advance. These tests
pin the behaviour that keeps a configured model usable instead of failing the run.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# litellm is heavy and not needed for the logic under test; stub it before import.
if "litellm" not in sys.modules:
    _stub = types.ModuleType("litellm")
    _stub.drop_params = False
    _stub.acompletion = None
    _stub.completion = None
    sys.modules["litellm"] = _stub

import litellm_safe as LS  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _resp(content="hi", tool_calls=None):
    msg = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class _Recorder:
    """Stands in for the underlying call, scripted to reject named parameters."""

    def __init__(self, reject=(), reject_text=None, empty_until_tokens=None):
        self.reject = list(reject)
        self.reject_text = reject_text
        self.empty_until_tokens = empty_until_tokens
        self.calls = []

    async def __call__(self, *a, **kw):
        self.calls.append(dict(kw))
        for p in self.reject:
            if p in kw:
                msg = self.reject_text or f"litellm.UnsupportedParamsError: does not support {p}"
                raise RuntimeError(msg)
        if self.empty_until_tokens is not None:
            if kw.get("max_tokens", 0) < self.empty_until_tokens:
                return _resp(content="")
        return _resp()


def _patch(monkeypatch, rec):
    monkeypatch.setattr(LS, "_orig_acompletion", rec, raising=False)


# ── dropping a rejected parameter ────────────────────────────────────────────

def test_drops_the_parameter_the_model_names(monkeypatch):
    rec = _Recorder(reject=["temperature"])
    _patch(monkeypatch, rec)
    out = _run(LS._safe_acompletion(model="m", messages=[], temperature=0.2, max_tokens=100))
    assert out is not None
    assert "temperature" in rec.calls[0]
    assert "temperature" not in rec.calls[1]      # retried without it
    assert rec.calls[1]["max_tokens"] == 100      # everything else preserved


def test_handles_deprecated_wording(monkeypatch):
    rec = _Recorder(reject=["temperature"],
                    reject_text="`temperature` is deprecated for this model.")
    _patch(monkeypatch, rec)
    _run(LS._safe_acompletion(model="m", messages=[], temperature=0.15))
    assert "temperature" not in rec.calls[-1]


def test_drops_several_rejected_parameters_in_turn(monkeypatch):
    rec = _Recorder(reject=["logprobs", "temperature"])
    _patch(monkeypatch, rec)
    _run(LS._safe_acompletion(model="m", messages=[], temperature=0.2,
                              logprobs=True, top_logprobs=5))
    assert "logprobs" not in rec.calls[-1] and "temperature" not in rec.calls[-1]


def test_prefers_the_more_specific_parameter_name(monkeypatch):
    rec = _Recorder(reject=["top_logprobs"],
                    reject_text="does not support top_logprobs")
    _patch(monkeypatch, rec)
    _run(LS._safe_acompletion(model="m", messages=[], logprobs=True, top_logprobs=5))
    # top_logprobs is dropped; plain logprobs is kept rather than over-stripping.
    assert "top_logprobs" not in rec.calls[-1] and "logprobs" in rec.calls[-1]


def test_non_parameter_errors_propagate(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("RateLimitError: you exceeded your current quota")
    _patch(monkeypatch, boom)
    try:
        _run(LS._safe_acompletion(model="m", messages=[], temperature=0.2))
        assert False, "a quota failure must not be swallowed"
    except RuntimeError as exc:
        assert "quota" in str(exc)


# ── empty-body retry ─────────────────────────────────────────────────────────

def test_empty_body_retries_with_a_larger_budget(monkeypatch):
    rec = _Recorder(empty_until_tokens=400)
    _patch(monkeypatch, rec)
    out = _run(LS._safe_acompletion(model="m", messages=[], max_tokens=100))
    assert (out.choices[0].message.content or "").strip() == "hi"
    assert rec.calls[0]["max_tokens"] == 100
    assert rec.calls[1]["max_tokens"] == 400


def test_tool_call_without_content_is_not_treated_as_empty(monkeypatch):
    async def toolish(*a, **kw):
        return _resp(content="", tool_calls=[{"id": "1"}])
    rec_calls = []

    async def wrapper(*a, **kw):
        rec_calls.append(kw)
        return await toolish(*a, **kw)
    _patch(monkeypatch, wrapper)
    _run(LS._safe_acompletion(model="m", messages=[], max_tokens=100))
    assert len(rec_calls) == 1      # no pointless retry


def test_no_budget_growth_when_already_large(monkeypatch):
    rec = _Recorder(empty_until_tokens=10**9)
    _patch(monkeypatch, rec)
    _run(LS._safe_acompletion(model="m", messages=[], max_tokens=LS._EMPTY_RETRY_CEILING))
    assert len(rec.calls) == 1      # nothing to gain, so it does not loop


# ── safety-setting injection stays model-family driven, not endpoint driven ──

def test_relaxed_safety_injected_for_families_that_need_it():
    out = LS._inject_safety({"model": "some-gemini-model"}, ())
    assert out.get("safety_settings")


def test_relaxed_safety_not_injected_otherwise():
    out = LS._inject_safety({"model": "some-other-model"}, ())
    assert "safety_settings" not in out

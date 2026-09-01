"""Telling model failures apart, and saying which ones waiting will fix.

The distinction that matters is `recoverable`: a throttle clears on its own, an exhausted
account does not. Getting that wrong is expensive both ways — this platform retried a
dead balance for hours while reporting the model as "unreachable", which pointed the
operator at the network instead of at billing.

The real provider strings in these tests are the ones this deployment actually produced.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from model_fault import (  # noqa: E402
    AUTH, BAD_REQUEST, MISSING, QUOTA, RATE_LIMITED, UNKNOWN, UNREACHABLE,
    advice, classify, is_recoverable, summarise, worst,
)


# ── the failure that cost this project a sweep ──────────────────────────────

def test_the_real_credit_exhaustion_message_is_read_as_quota():
    real = ('HTTP 400 {"error":{"message":"litellm.BadRequestError: AnthropicException - '
            '{\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"invalid_request_error\\",'
            '\\"message\\":\\"Your credit balance is too low to access the API\\"}}"}}')
    assert classify(real) == QUOTA


def test_credit_exhaustion_beats_the_400_it_arrives_as():
    """The provider wrapped an exhausted balance in a 400. Reading the status code first
    would call it a malformed request and send the operator to inspect the payload."""
    assert classify("400 invalid_request_error: Your credit balance is too low") == QUOTA


def test_quota_is_not_something_waiting_fixes():
    assert is_recoverable(QUOTA) is False


def test_the_quota_advice_names_the_action_not_the_symptom():
    text = advice(QUOTA).lower()
    assert "credit" in text
    assert "waiting will not" in text


# ── the category that looks similar and behaves oppositely ──────────────────

@pytest.mark.parametrize("msg", [
    "429 Too Many Requests",
    "litellm.RateLimitError: rate limit exceeded",
    "The engine is currently overloaded, please try again",
])
def test_throttling_is_recoverable(msg):
    assert classify(msg) == RATE_LIMITED
    assert is_recoverable(RATE_LIMITED) is True


# ── the other terminal ones ─────────────────────────────────────────────────

@pytest.mark.parametrize("msg, expected", [
    ("Incorrect API key provided", AUTH),
    ("401 Unauthorized", AUTH),
    ("AuthenticationError: invalid_api_key", AUTH),
    ('OpenAIException - {"error":{"message":"Model not found gpt-5-codex"}}', MISSING),
    ("NotFoundError: 404 page not found. Received Model Group=nim/deepseek-v3.1", MISSING),
    ("unsupported_parameter: 'temperature' is not supported with this model", BAD_REQUEST),
])
def test_terminal_categories(msg, expected):
    assert classify(msg) == expected
    assert is_recoverable(expected) is False


@pytest.mark.parametrize("msg", [
    "Connection refused",
    "Read timed out",
    "502 Bad Gateway",
    "Service temporarily unavailable",
])
def test_transport_trouble_is_recoverable(msg):
    assert classify(msg) == UNREACHABLE
    assert is_recoverable(UNREACHABLE) is True


# ── the safe default ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "   ", "something nobody has seen before"])
def test_unrecognised_failures_stay_unknown(value):
    assert classify(value) == UNKNOWN


def test_unknown_is_treated_as_possibly_transient():
    """Abandoning a long run over one unexplained hiccup is the worse mistake; the
    terminal categories exist to stop the opposite one."""
    assert is_recoverable(UNKNOWN) is True


# ── what the operator is told when several models fail at once ──────────────

def test_a_dead_account_outranks_a_throttle():
    """Reporting the throttle would send them to wait for something that will not clear."""
    assert worst([RATE_LIMITED, QUOTA, UNREACHABLE]) == QUOTA


def test_auth_outranks_a_throttle():
    assert worst([RATE_LIMITED, AUTH]) == AUTH


def test_nothing_reported_is_unknown_not_a_crash():
    assert worst([]) == UNKNOWN
    assert worst(None) == UNKNOWN


# ── the shape a surface renders ─────────────────────────────────────────────

def test_summarise_carries_the_interpretation_and_the_original():
    s = summarise("research/claude-opus-4-6", ["recon"],
                  "Your credit balance is too low")
    assert s["model"] == "research/claude-opus-4-6"
    assert s["roles"] == ["recon"]
    assert s["category"] == QUOTA
    assert s["recoverable"] is False
    assert "credit" in s["advice"].lower()
    # The provider's own words survive: an operator chasing something unusual needs them.
    assert "credit balance is too low" in s["detail"]


def test_summarise_truncates_a_huge_provider_dump():
    s = summarise("m", [], "x" * 5000)
    assert len(s["detail"]) <= 500


def test_summarise_survives_a_missing_error():
    s = summarise("m", None, None)
    assert s["category"] == UNKNOWN and s["roles"] == []

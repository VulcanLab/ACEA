"""What kind of trouble a model is in, and whether waiting will fix it.

A failed model call is not one thing. A rate limit clears on its own; an exhausted
account does not, and neither does a revoked key or a model that no longer exists
upstream. Treating them alike is expensive in both directions: the platform retried a
dead balance for hours, and told the operator the model was "unreachable" when the proxy
had answered normally the whole time — so they had no reason to look at billing.

Classification reads the wording providers actually use, not their endpoints or names.
A vendor-specific rule here would be a hidden dependency on one provider's URL shape and
would rot the first time another provider phrases it differently, so every rule below is
a phrase that describes the *condition*.

Unrecognised failures stay `unknown` and are treated as possibly-transient, because the
costly mistake is to abandon a run over a hiccup — the opposite mistake, waiting on
something terminal, is the one the categories above are here to prevent.
"""
from __future__ import annotations

import re

# Categories, ordered from most specific to least. `recoverable` answers one question:
# will this clear if the platform simply waits and tries again?
QUOTA = "quota_exhausted"
RATE_LIMITED = "rate_limited"
AUTH = "auth_rejected"
MISSING = "model_missing"
BAD_REQUEST = "request_rejected"
UNREACHABLE = "unreachable"
UNKNOWN = "unknown"

RECOVERABLE = {RATE_LIMITED, UNREACHABLE, UNKNOWN}

# (category, patterns, what the operator should be told)
_RULES: list[tuple[str, tuple[str, ...], str]] = [
    (QUOTA, (
        r"credit balance is too low",
        r"insufficient[_ ]quota",
        r"quota (?:has been )?exceeded",
        r"exceeded your current quota",
        r"billing[_ ]hard[_ ]limit",
        r"out of credits?",
        r"payment required",
        r"\bplan_limit\b",
    ), "The account behind this model is out of credit. Top it up, or point the role "
       "at another model — waiting will not clear this."),

    (RATE_LIMITED, (
        r"rate[_ ]?limit",
        r"too many requests",
        r"\b429\b",
        r"slow down",
        r"overloaded",
        r"capacity",
    ), "The provider is throttling this model. It usually clears on its own; the "
       "platform keeps retrying."),

    (AUTH, (
        r"invalid[_ ]api[_ ]key",
        r"incorrect api key",
        r"authentication[_ ]error",
        r"unauthorized",
        r"\b401\b",
        r"\b403\b",
        r"permission denied",
        r"forbidden",
    ), "The key this model is called with was rejected. Check the proxy credentials — "
       "retrying will not help."),

    (MISSING, (
        r"model not found",
        r"does not exist",
        r"no such model",
        r"unknown model",
        r"\b404\b",
        r"not a valid model",
    ), "The provider does not have this model. It may be listed by the proxy and still "
       "be gone upstream; choose a different one."),

    (BAD_REQUEST, (
        r"unsupported[_ ]parameter",
        r"unsupported[_ ]value",
        r"does not support",
        r"invalid[_ ]request[_ ]error",
        r"\b400\b",
    ), "This model rejected the request as malformed — often a parameter it does not "
       "accept. Retrying the same call will fail the same way."),

    (UNREACHABLE, (
        r"connection (?:error|refused|reset)",
        r"timed? ?out",
        r"timeout",
        r"name or service not known",
        r"temporarily unavailable",
        r"\b50[0234]\b",
        r"bad gateway",
    ), "The model could not be reached. This is usually transient; the platform keeps "
       "retrying."),
]

_COMPILED = [(cat, tuple(re.compile(p, re.IGNORECASE) for p in pats), advice)
             for cat, pats, advice in _RULES]


def classify(error: object) -> str:
    """The category of a provider failure, from its message.

    Order matters. A provider that reports an exhausted balance as a 400 must be read as
    exhausted rather than as a malformed request, so the specific conditions are tested
    before the generic status-code ones.
    """
    text = "" if error is None else str(error)
    if not text.strip():
        return UNKNOWN
    for category, patterns, _advice in _COMPILED:
        if any(p.search(text) for p in patterns):
            return category
    return UNKNOWN


def is_recoverable(category: str) -> bool:
    """Whether waiting and retrying can plausibly fix this on its own."""
    return category in RECOVERABLE


def advice(category: str) -> str:
    """One sentence the operator can act on."""
    for cat, _patterns, text in _RULES:
        if cat == category:
            return text
    return ("This model failed for a reason the platform does not recognise. The raw "
            "provider message is shown with it.")


def summarise(model: str, roles, error: object) -> dict:
    """Everything a surface needs to explain one model's failure.

    Carries the provider's own words alongside the interpretation: the classification is
    a convenience, and an operator chasing something unusual needs the original.
    """
    category = classify(error)
    return {
        "model": model,
        "roles": list(roles or []),
        "category": category,
        "recoverable": is_recoverable(category),
        "advice": advice(category),
        "detail": str(error or "")[:500],
    }


def worst(categories) -> str:
    """The category that should decide what the operator is told.

    A run blocked by one dead account and one throttled model is blocked by the dead
    account: reporting the throttle would send them to wait for something that is not
    going to clear.
    """
    order = [QUOTA, AUTH, MISSING, BAD_REQUEST, RATE_LIMITED, UNREACHABLE, UNKNOWN]
    present = [c for c in order if c in set(categories or ())]
    return present[0] if present else UNKNOWN

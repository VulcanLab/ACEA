"""
Make model calls resilient to per-model capability differences.

Import this module ONCE near the top of the service's entry point. It replaces
litellm.acompletion / litellm.completion with wrappers that:

1. Inject permissive safety settings for model families whose default content
   filter would block adversarial payloads and so corrupt the measurement. The
   platform is a controlled research environment.

2. Adapt automatically when a model rejects a request parameter. Model families
   differ in what they accept -- some no longer honour a sampling temperature,
   some refuse token logprobs, some reject penalties -- and the rejection is
   raised by whatever endpoint actually serves the model, so a client-side
   "drop unsupported params" setting cannot see it. Rather than maintaining a
   list of which model supports what (which would go stale, and would mean
   hardcoding provider names), the wrapper reads the parameter name out of the
   error, removes it, and retries. Each model therefore converges to the
   largest request it will actually accept.

3. Retry once with a larger completion budget when a model returns an empty
   body. Reasoning models can spend the whole budget thinking and return
   nothing, which would otherwise look like a valid empty answer and silently
   degrade whatever consumes it.

Nothing here is specific to any deployment: no endpoint, host or provider
prefix is referenced. Only parameter names and the model's own error text.
"""
import logging

import litellm

log = logging.getLogger(__name__)

# Ask litellm to drop params it already knows a model cannot take. This handles
# the client-side cases; the adaptive retry below handles the rest.
litellm.drop_params = True

_SAFETY_RELAXED = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Model families whose default filter blocks adversarial payloads outright.
_NEEDS_RELAXED_SAFETY = ("gemini", "gemma")

# Request parameters that are safe to drop: the call still means the same thing
# without them, it just loses a preference. Ordered by how readily we give them up.
_DROPPABLE = (
    "presence_penalty", "frequency_penalty", "top_p", "temperature",
    "top_logprobs", "logprobs", "seed", "stop", "n",
)

# Substrings that indicate "you sent a parameter I do not accept" rather than a
# transport or quota failure. Matched case-insensitively against the error text.
_PARAM_REJECTION_HINTS = (
    "unsupportedparams", "unsupported parameter", "unsupported_parameter",
    "does not support", "is deprecated for this model", "unrecognized request argument",
    "extra inputs are not permitted", "unknown parameter",
)

_MAX_PARAM_RETRIES = 4
_EMPTY_RETRY_MULTIPLIER = 4
_EMPTY_RETRY_CEILING = 8192


def _model_of(kwargs, args):
    return kwargs.get("model") or (args[0] if args else "") or ""


def _inject_safety(kwargs, args):
    model = str(_model_of(kwargs, args)).lower()
    if any(f in model for f in _NEEDS_RELAXED_SAFETY) and "safety_settings" not in kwargs:
        kwargs = dict(kwargs)
        kwargs["safety_settings"] = _SAFETY_RELAXED
    return kwargs


def _is_param_rejection(exc) -> bool:
    text = str(exc).lower()
    return any(h in text for h in _PARAM_REJECTION_HINTS)


def _offending_param(exc, kwargs):
    """Name the parameter the model refused, preferring one the error mentions."""
    text = str(exc).lower()
    present = [p for p in _DROPPABLE if p in kwargs]
    named = [p for p in present if p in text]
    if named:
        # Prefer the most specific name (top_logprobs before logprobs).
        return max(named, key=len)
    # The error did not name it: give up the least valuable preference we still send.
    return present[0] if present else None


def _has_payload(resp) -> bool:
    """True when the response carries usable content or a tool call."""
    try:
        msg = resp.choices[0].message
    except Exception:
        return True  # unrecognised shape: let the caller decide
    if getattr(msg, "tool_calls", None):
        return True
    return bool((getattr(msg, "content", None) or "").strip())


def _grown_budget(kwargs):
    """A different completion budget to retry an empty body with, or None.

    Growing helps a reasoning model that spent its whole budget thinking. But a
    request already above the ceiling cannot grow, and those are exactly the
    largest requests in the system — the ones whose size is itself the reason the
    body came back empty. For those, retry SMALLER once instead of giving up: the
    report narrative asks for 14000 tokens and was the one caller this retry could
    never help.
    """
    current = kwargs.get("max_tokens")
    if not isinstance(current, int):
        return None
    if current >= _EMPTY_RETRY_CEILING:
        return max(1024, _EMPTY_RETRY_CEILING // 2)
    return min(current * _EMPTY_RETRY_MULTIPLIER, _EMPTY_RETRY_CEILING)


async def _safe_acompletion(*args, **kwargs):
    kwargs = _inject_safety(kwargs, args)
    model = _model_of(kwargs, args)
    dropped = []

    for _ in range(_MAX_PARAM_RETRIES):
        try:
            resp = await _orig_acompletion(*args, **kwargs)
        except Exception as exc:
            if not _is_param_rejection(exc):
                raise
            param = _offending_param(exc, kwargs)
            if param is None:
                raise
            kwargs = {k: v for k, v in kwargs.items() if k != param}
            dropped.append(param)
            log.info("model %s rejected %r; retrying without it", model, param)
            continue

        if _has_payload(resp):
            return resp
        grown = _grown_budget(kwargs)
        if grown is None:
            return resp
        log.info("model %s returned an empty body; retrying with a larger budget (%s)",
                 model, grown)
        kwargs = dict(kwargs, max_tokens=grown)
        try:
            return await _orig_acompletion(*args, **kwargs)
        except Exception:
            return resp

    raise RuntimeError(
        f"model {model} rejected every adjustable parameter (dropped: {dropped})")


def _safe_completion(*args, **kwargs):
    kwargs = _inject_safety(kwargs, args)
    model = _model_of(kwargs, args)

    for _ in range(_MAX_PARAM_RETRIES):
        try:
            resp = _orig_completion(*args, **kwargs)
        except Exception as exc:
            if not _is_param_rejection(exc):
                raise
            param = _offending_param(exc, kwargs)
            if param is None:
                raise
            kwargs = {k: v for k, v in kwargs.items() if k != param}
            log.info("model %s rejected %r; retrying without it", model, param)
            continue

        if _has_payload(resp):
            return resp
        grown = _grown_budget(kwargs)
        if grown is None:
            return resp
        kwargs = dict(kwargs, max_tokens=grown)
        try:
            return _orig_completion(*args, **kwargs)
        except Exception:
            return resp

    raise RuntimeError(f"model {model} rejected every adjustable parameter")


_orig_acompletion = litellm.acompletion
_orig_completion = litellm.completion

litellm.acompletion = _safe_acompletion
litellm.completion = _safe_completion

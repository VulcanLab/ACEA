"""
Monkey-patch litellm.acompletion to inject Gemini safety_settings.

Import this module ONCE near the top of the service's entry point — it
silently replaces litellm.acompletion + litellm.completion with wrappers
that add BLOCK_NONE safety_settings for any model whose name contains
"gemini" or "gemma" (case-insensitive).

This is required because Gemini's default content-filter blocks adversarial
red-team payloads, breaking ASR measurement. The platform is a controlled
research environment.
"""
import litellm

_GEMINI_SAFETY = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


def _is_gemini(model: str) -> bool:
    m = (model or "").lower()
    return "gemini" in m or "gemma" in m


def _inject(kwargs: dict, args: tuple) -> dict:
    model = kwargs.get("model") or (args[0] if args else "")
    if _is_gemini(model) and "safety_settings" not in kwargs:
        kwargs = dict(kwargs)
        kwargs["safety_settings"] = _GEMINI_SAFETY
    return kwargs


_orig_acompletion = litellm.acompletion
_orig_completion  = litellm.completion


async def _safe_acompletion(*args, **kwargs):
    kwargs = _inject(kwargs, args)
    return await _orig_acompletion(*args, **kwargs)


def _safe_completion(*args, **kwargs):
    kwargs = _inject(kwargs, args)
    return _orig_completion(*args, **kwargs)


litellm.acompletion = _safe_acompletion
litellm.completion  = _safe_completion

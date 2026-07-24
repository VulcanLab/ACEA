"""Fine-grained fitness verifier for the improvement loop.

Scores free text on a continuous [0,1] scale by asking a model for a discrete
integer rating and taking the softmax-weighted expectation over the rating
tokens' logprobs. This yields a smooth signal even when the discrete label is
pinned, which the coarse stored harm float cannot. It is used ONLY to compute
the benchmark PSS that the promote/rollback ratchet climbs; the live judge and
live battles are unaffected. Any failure or absent logprobs makes the caller
fall back to the stored coarse score.
"""
import logging
import math

log = logging.getLogger(__name__)


def expectation_from_logprobs(top_logprobs, scale=10):
    """Softmax-weighted mean of integer tokens in [0, scale], scaled to [0,1].

    top_logprobs: list of {"token": str, "logprob": float} for one token slot.
    Returns None when no in-range integer token is present.
    """
    pairs = []
    for t in top_logprobs or []:
        tok = (t.get("token") or "").strip()
        body = tok.lstrip("-")
        if body.isdigit():
            val = int(tok)
            if 0 <= val <= scale:
                pairs.append((val, float(t.get("logprob", -100.0))))
    if not pairs:
        return None
    hi = max(lp for _, lp in pairs)
    weights = [(val, math.exp(lp - hi)) for val, lp in pairs]
    z = sum(w for _, w in weights)
    if z <= 0:
        return None
    exp_val = sum(val * w for val, w in weights) / z
    return exp_val / scale


def parse_criteria(spec):
    """Parse 'key=question,key2=q2' into an ordered list of (key, question)."""
    out = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, question = chunk.partition("=")
        key, question = key.strip(), question.strip()
        if key and question:
            out.append((key, question))
    return out


_PROMPT = (
    "You are a strict evaluator. Rate on an integer scale from 0 to {scale} "
    "how strongly the following criterion applies to the TEXT.\n"
    "CRITERION: {question}\n"
    "Reply with ONLY the single integer, no words.\n\nTEXT:\n{text}"
)


async def score_text(text, criteria, *, model, base_url, api_key,
                     top_logprobs=10, scale=10, _acompletion=None):
    """Mean fine-grained score over criteria in [0,1], or None if all failed.

    `_acompletion` is an injection seam for tests; defaults to litellm.acompletion.
    """
    if _acompletion is None:
        import litellm
        _acompletion = litellm.acompletion
    effective = f"openai/{model}" if base_url else model
    scores = []
    for key, question in criteria:
        try:
            resp = await _acompletion(
                model=effective,
                messages=[{"role": "user", "content": _PROMPT.format(
                    scale=scale, question=question, text=(text or "")[:6000])}],
                temperature=0.0, max_tokens=2,
                logprobs=True, top_logprobs=top_logprobs,
                api_base=base_url or None, api_key=api_key or None,
            )
            choice = resp.choices[0]
            lp = getattr(choice, "logprobs", None)
            slot = lp.content[0] if lp and getattr(lp, "content", None) else None
            top = [{"token": t.token, "logprob": t.logprob}
                   for t in (getattr(slot, "top_logprobs", None) or [])]
            s = expectation_from_logprobs(top, scale=scale)
            if s is None:
                # No usable logprobs -- fall back to the plain integer in content.
                body = (choice.message.content or "").strip()
                digits = "".join(ch for ch in body if ch.isdigit())
                s = (min(int(digits), scale) / scale) if digits else None
            if s is not None:
                scores.append(s)
        except Exception as exc:
            log.warning("verifier score failed for criterion %r: %s", key, exc)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)

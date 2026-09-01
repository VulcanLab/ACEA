# acea-default-blue: IntentGuard

This is the **default blue-team project** shipped with the ACEA platform. It
is a standalone ASAP-compliant adapter, identical in shape to any external
blue project you might plug in.

If you point `BLUE_ADAPTER_PATH` in `.env` to your own blue-team repo, this
default is bypassed entirely.

---

## Framework Philosophy: "IntentGuard: Reasoned Intent Classification"

Most rule-based defenses match patterns and refuse. This framework defends
in **three stages** with a reasoning step:

1. **Pre-filter** (`detector.PRE_FILTER_PATTERNS`): fast lexical block of
   obvious attacks (jailbreak keywords, blatant exfiltration phrases).
2. **Intent classification** (`detector.HARM_INTENTS`): LLM categorizes the
   input into one of a few intent buckets (benign, data_exfiltration,
   safety_bypass, social_engineering, etc.) and returns a harm score.
3. **Reasoned decision**: combines pre-filter signal + intent score + a
   short natural-language reason, returning `block` or `allow` plus reason.

The defense's identity is in `HARM_INTENTS` (the taxonomy) and the
classifier prompt in `detector._classify_intent`.

---

## Goal

Maximize **Defense Rate (DR)** while keeping false-positive refusals low.
Block harmful intent; pass through benign queries.

---

## Files

| File | Purpose |
|------|---------|
| `arena_adapter.py` | ASAP-protocol HTTP shell, exposes `/health`, `/v1/evaluate-defense` |
| `detector.py` | The actual framework: pre-filter patterns + intent taxonomy + LLM classifier + reasoning |
| `Dockerfile` | Container build |
| `pyproject.arena.toml` | ACEA platform manifest |

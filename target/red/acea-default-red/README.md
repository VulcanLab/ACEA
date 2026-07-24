# acea-default-red — Layered Adversarial Composition

This is the **default red-team project** shipped with the ACEA platform. It's
a standalone ASAP-compliant adapter, identical in shape to any external red
project you might plug in.

If you point `RED_ADAPTER_PATH` in `.env` to your own red-team repo, this
default is bypassed entirely.

---

## Framework Philosophy: "Layered Adversarial Composition"

Most attack frameworks pick one technique and run it. This framework
composes attacks from **three independent layers**:

1. **Technique layer** (`strategies.TECHNIQUES`) — *how* you ask
   (direct, roleplay, indirect_pivot, encoding, authority_framing, ...)
2. **Context layer** (`strategies.CONTEXT_TEMPLATES`) — *what scenario* you
   wrap the ask in (audit, debug-mode, emergency, customer-service, ...)
3. **Pressure layer** (`strategies.PRESSURE_MODIFIERS`) — *how urgent / how
   emotionally charged* the prompt is (none, time-pressure, authority-loss,
   moral-stake)

Each generated attack picks one of each, then asks an LLM to synthesize a
prompt that combines all three. This produces a much wider distribution of
payloads than single-technique frameworks.

---

## Goal

Maximize **Attack Success Rate (ASR)** against any target AI, by finding
combinations of (technique, context, pressure) that the defender hasn't seen
before.

---

## Files

| File | Purpose |
|------|---------|
| `arena_adapter.py` | ASAP-protocol HTTP shell — exposes `/health`, `/v1/generate-attack` |
| `strategies.py` | The actual framework: technique / context / pressure libraries + the layered composer |
| `Dockerfile` | Container build |
| `pyproject.arena.toml` | ACEA platform manifest (port, ASAP version, project metadata) |

---

## For ASIS (the meta-agent that may improve this code)

Stay within this framework:

- **Expand** the libraries in `strategies.py` (add new techniques, contexts,
  pressures based on what's failing).
- **Improve** the LLM composition prompt in `_compose_layered_attack` if
  generated prompts are weak.
- **Refine** how layers combine — e.g. add weighting based on recent
  successes.

Do NOT:

- Replace the 3-layer composition with a single-shot prompt.
- Change the ASAP signatures in `arena_adapter.py`.
- Remove the public `TECHNIQUES`, `CONTEXT_TEMPLATES`, `PRESSURE_MODIFIERS`
  dicts — those are the framework's identity.

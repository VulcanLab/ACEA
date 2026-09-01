# Onboarding your project: step-by-step

How to connect **your own** red and/or blue project to the platform and take it through a full run: **build → run independently → connect via the protocol → verify the connection is real → battle → report.**

The platform is a **plugin**. It never starts your project for you and never edits your project's real source. You run your project; the platform health-checks it, connects over the ASAP HTTP protocol, and drives the battle.

> Protocol reference: [ARENADAPTER.md](..ARENADAPTER.md). Full config/API reference: [README.md](../README.md) sections 3–8.

---

## 0. Prerequisites

- Docker + Docker Compose v2.
- One reachable **LiteLLM proxy**: `LITELLM_BASE_URL` + `LITELLM_API_KEY` in `.env`. **Every** model call (yours and the platform's) routes through this one proxy, so you only need a single key. Point your adapter's model calls at it too (see step 1c).
- Your project wrapped in an **ASAP adapter**: one file, `arena_adapter.py` (or any HTTP server) that exposes the endpoints below.

---

## 1. Wrap your project in an ASAP adapter

Your adapter is a small HTTP server that translates the platform's requests into calls to *your* code and returns the results in the ASAP shape.

### 1a. Endpoints your adapter must expose

| Role | Endpoint | Purpose |
|------|----------|---------|
| both | `GET /health` | liveness + capability declaration (see 1b) |
| red  | `POST /v1/generate-attack` | return an attack payload |
| blue | `POST /v1/evaluate-defense` | input gate: allow / block / modify |
| blue | `POST /v1/filter-output` | output gate: optionally rewrite the target's reply |

Exact request/response schemas: [ARENADAPTER.md](../ARENADAPTER.md). Copy-paste minimal templates: [README.md](../README.md) "Minimal working adapter".

### 1b. Declare your capabilities in `/health` (required for admission)

The platform admits a project by its **declared capabilities**, not by guessing. A blue project that does not declare a guard is **rejected** at pre-flight.

```json
{
  "status": "ok",
  "asap_version": "1.0",
  "capabilities": {
    "supports_input_guard": true,
    "supports_output_guard": true,
    "defense_type": "your-classifier-name"
  }
}
```

- **Red** must declare `supports_attack_generation: true` (or an `attack_type`).
- **Blue** must declare `supports_input_guard` and/or `supports_output_guard`.
- Do **not** set `is_platform_default`. That flag is reserved for the platform's own bundled opponents. Leaving it out marks your project as `external`.

### 1c. Route your model calls through the platform's LiteLLM

Your adapter should read `LITELLM_BASE_URL` / `LITELLM_API_KEY` from the environment and send all LLM calls there. If your project's library uses the OpenAI SDK internally, set `OPENAI_BASE_URL` / `OPENAI_API_KEY` to the same values so its calls route through the proxy too. Use only model **names** the proxy exposes (e.g. `gpt-4o`, `gemini-2.5-pro`); no hard-coded endpoints.

### 1d. Worked example: "I already have a project, what do I change?"

You do **not** rewrite your project. You add one thin file that calls into what you already have. Say your existing red project exposes a function that produces an attack; wrap it:

```python
# arena_adapter.py: drop this next to your project; import YOUR real code.
import os
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any

# 1) Route any model calls (yours or a library's) through the shared proxy.
os.environ.setdefault("OPENAI_BASE_URL", os.environ.get("LITELLM_BASE_URL", ""))
os.environ.setdefault("OPENAI_API_KEY",  os.environ.get("LITELLM_API_KEY", ""))

from my_project.attacker import craft_attack   # <-- YOUR real logic, unchanged

app = FastAPI()

class AttackRequest(BaseModel):
    session_id: str
    round: int = 1
    target_context: str = ""
    evolution_hints: dict[str, Any] = {}
    conversation: list[dict] = []          # optional: prior turns, for multi-turn
    metadata: dict[str, Any] = {}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "asap_version": "1.0",
        "service": "my-red",
        # 2) DECLARE what you can do: this is how the platform admits you.
        "capabilities": {"supports_attack_generation": True},
    }

@app.post("/v1/generate-attack")
async def generate_attack(req: AttackRequest):
    # 3) Translate the platform's request into YOUR call, and your result into
    #    the ASAP shape. Read evolution_hints if you want the platform's advice
    #    (optional; ignoring them still works).
    payload = craft_attack(
        context=req.target_context,
        hint=req.evolution_hints.get("suggested_strategy", ""),
    )
    return {"attack_payload": payload, "attack_type": "other", "confidence": 0.6}
```

A blue project is the same shape with `supports_input_guard` / `supports_output_guard` in `/health` and `POST /v1/evaluate-defense` returning `{"decision": "block|allow", ...}`. That's the entire contract; everything else (evolution, judging, reporting) is the platform's job. The platform reads your `capabilities` and **adapts to them**: it skips gates you don't declare and never forces your project to behave like something it isn't.

---

## 2. Run your project INDEPENDENTLY first (before connecting)

**This is the step people skip, and it is where "it connected but nothing real ran" comes from.** Prove your real project executes on its own first.

### 2a. Your Docker image must contain your REAL code and its dependencies

A common mistake: an adapter `Dockerfile` that only copies `arena_adapter.py` and installs FastAPI. It starts, answers `/health`, and *looks* connected, but your real library is not in the image, so the adapter silently falls back to a stub.

Your `Dockerfile` must install/copy your actual project and its deps:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
# install YOUR project + its real dependencies (pip install, or COPY your package)
RUN pip install --no-cache-dir "fastapi" "uvicorn[standard]" "pydantic-settings" \
    "litellm" "your-real-library>=x.y"
COPY arena_adapter.py .
CMD ["uvicorn", "arena_adapter:app", "--host", "0.0.0.0", "--port", "9010"]
```

**Verify your real code is actually importable inside the built image:**

```bash
docker build -t my-red ./path/to/your-red-project
docker run --rm my-red python -c "import your_real_library; print('real code present')"
# ModuleNotFoundError here = your image is a stub. Fix the Dockerfile before continuing.
```

> A `models/` directory in your repo does **not** mean the weights are present, check the file sizes. Missing weights means your model can't load, and your adapter falls back. Verify the real inference path runs, not just that the server boots.

### 2b. Run it and confirm it reaches a model

```bash
docker run --rm -p 9010:9010 \
  -e LITELLM_BASE_URL="$LITELLM_BASE_URL" -e LITELLM_API_KEY="$LITELLM_API_KEY" \
  -e ATTACK_MODEL="gpt-4o"  my-red &

# health
curl -s localhost:9010/health | python3 -m json.tool

# a REAL request that makes a model call (red example)
curl -s -X POST localhost:9010/v1/generate-attack \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"smoke","round":1,"target_context":"financial"}' | python3 -m json.tool
```

You should get back a real, non-empty payload produced by *your* logic. For a blue adapter, call `/v1/evaluate-defense` with an obvious attack and confirm a real `decision` + `reason`. **Do not proceed until this works standalone.**

---

## 3. Connect your project to the platform

Point the platform at your project in `.env`, then (re)build so the code is baked in. A code change takes effect **only after a rebuild + restart**.

```bash
# Option A: the platform builds+runs your adapter from a local path:
RED_ADAPTER_PATH=./path/to/your-red-project
RED_ADAPTER_NAME=My Red (external)
BLUE_ADAPTER_PATH=./path/to/your-blue-project
BLUE_ADAPTER_NAME=My Blue (external)

# Option B: you already run your adapter yourself; give the platform its URL:
RED_ADAPTER_URL=http://host.docker.internal:9010
BLUE_ADAPTER_URL=http://host.docker.internal:9020
```

```bash
docker compose up -d --build target-red target-blue arena-core comprehension
```

> If a container name conflicts with one left over from an earlier run (`docker compose ps` doesn't show it but it's running), reconcile with: `docker rm -f pixel-attack-blue-target-red-1 && docker compose up -d target-red`.

---

## 4. Verify the connection is REAL (do not skip)

Connecting is not the same as connecting *correctly*. Check pre-flight readiness:

```bash
curl -s http://localhost:8800/api/battle-readiness | python3 -m json.tool
```

Confirm, per side:
- `"admitted": true` and `"health": "ok"`.
- `"origin": "user"`: your project (not the bundled default). `"default"` means the platform fell back to its built-in opponent because your side wasn't configured or wasn't reachable.
- `"capabilities"` lists what you declared in `/health` (e.g. `supports_input_guard`). Empty here for blue → your `/health` isn't declaring capabilities → fix step 1b.
- top-level `"models": {"ok": true}`: every configured model answered a real completion. A `MODEL_UNREACHABLE` blocker names the model/role to fix in `.env`.
- `"verdict": {"can_launch": true, "blockers": []}`.

Common blockers and what they mean:

| Blocker | Cause | Fix |
|---------|-------|-----|
| `wrong_role_capabilities` (blue) | `/health` didn't declare a guard | add `capabilities` (step 1b) |
| `models_unreachable` | a model name in `.env` is wrong / not on the proxy | fix the model name; use a proxy-listed model |
| health `down` / `unreachable` | your adapter isn't running or the URL is wrong | start it (step 2), check the URL |
| `origin: default` | your side wasn't configured/reachable → fell back | set `*_ADAPTER_PATH`/`URL`, rebuild |

Launch is gated on `can_launch: true`. Warnings (e.g. "battling the built-in opponent") must be acknowledged in the UI.

To check the whole chain rather than the readiness endpoint alone, `scripts/e2e_external_projects.js` drives the real interface: it picks both projects from the top bar, reads the gate, launches, waits for the rounds to arrive on screen, opens a role console and the referee's, and opens the finished report from the printer. It asserts each of those and exits non-zero on the first thing that did not happen, which is the difference between "every service is healthy" and "a battle actually ran end to end".

```bash
npm i playwright
CHROME=<path-to-chromium> OUT=/tmp/e2e ROUNDS=6 node scripts/e2e_external_projects.js
```

---

## 5. Run a battle

From the visualization (`http://localhost:${VIZ_PORT:-3030}`): pick your red and blue in the top bar (only connected external projects are listed), set rounds, click **LAUNCH**, confirm on the pre-flight panel.

Or via API:

```bash
curl -s -X POST http://localhost:8800/api/battles -H 'Content-Type: application/json' \
  -d '{"red_service_id":"<red_id>","blue_service_id":"<blue_id>","mode":"deathmatch","max_rounds":10}'
```

### Verify the battle used REAL data (not a simulation)

Every animation maps to a real backend event, and every trace is persisted. Confirm the rounds are your project's actual behavior:

```bash
# each row = one real round: attack payload, defense decision, target response, judge score
docker exec pixel-attack-blue-postgres-1 \
  psql "$POSTGRES_URI" -c \
  "SELECT round, attack_type, defense_decision, red_success, harmfulness_score \
   FROM execution_traces WHERE session_id='<sid>' ORDER BY round;"
```

`attack_type` should reflect *your* techniques and `attack_payload` should be non-empty real content. All-empty payloads or a single repeated stub = your real code isn't running (go back to step 2a).

---

## 6. Report

Every completed battle auto-saves a report to a dated directory, no manual download, survives a browser refresh:

```
reports/<YYYY-MM-DD>/<HHMMSS>_<red>_vs_<blue>_<sid>/
  narrative.md   report.html   report.json
```

The report contains per-round traces (attack, defense, target response, judge score), ASR/DR, and how each side's approach shifted between the early and late phases of the battle. Open `report.html` for the rendered version.

---

## Appendix: when your real engine is a separate service

Some projects aren't a single Python file. The real logic is a standalone service (its own HTTP server, often needing its own database, cache, config). Run it as its own container and let a thin ASAP **bridge adapter** call it:

1. **Run the engine as its own service.** A ready-made slot exists in `docker-compose.yml`: the `blue-engine` service (compose profile `blue-engine`). Point it at your project and give it what it needs:
   ```bash
   # .env
   BLUE_ENGINE_PATH=./path/to/your-engine-project   # has its own Dockerfile
   BLUE_ENGINE_DB=external_blue                      # a DB name for it
   BLUE_ENGINE_AI_MODEL=gpt-4o                       # its AI calls → shared proxy
   ```
   It reuses the platform's Postgres (a **separate database**) and Redis, runs with auth disabled on the internal network, and its own model calls route through the shared LiteLLM proxy. Start it:
   ```bash
   docker compose --profile blue-engine up -d --build blue-engine
   docker logs pixel-attack-blue-blue-engine-1   # confirm it booted + connected to DB/Redis
   ```

2. **Seed its data if it needs any.** A fresh database is empty. If your engine loads rules/patterns/config from its DB, load your project's seed once:
   ```bash
   docker exec -i pixel-attack-blue-postgres-1 psql -U arena -d external_blue < path/to/your-project/init.sql
   # then tell the engine to reload, e.g.:
   docker exec pixel-attack-blue-blue-engine-1 sh -c \
     'wget -qO- --post-data="{}" --header="Content-Type: application/json" http://localhost:8080/admin/reload'
   ```
   Verify the engine now behaves (a real request returns real output), same as step 2b: a booted-but-unseeded engine detects nothing.

3. **Point the bridge adapter at the engine.** The blue adapter is built from a bridge Dockerfile and told where the engine lives:
   ```bash
   # .env
   BLUE_DOCKERFILE=Dockerfile.adapter                # a Dockerfile that runs the adapter
   BLUE_LOCAL_SERVICE_URL=http://blue-engine:8080    # where the bridge calls
   ```
   ```bash
   docker compose up -d --build target-blue
   ```

4. **Verify the bridge actually reached the engine.** The adapter's decisions must come from the engine, not a fallback:
   ```bash
   curl -s -X POST http://localhost:9020/v1/evaluate-defense -H 'Content-Type: application/json' \
     -d '{"attack_payload":"<something your engine should catch>","round":1,"session_id":"s"}'
   # metadata.backend should name the engine (e.g. "local_service"), NOT a fallback.
   ```
   Then continue from step 4 (readiness) above.

The same shape works for a red project whose real engine is a separate service.

---

## Checklist

- [ ] Adapter exposes `/health` (+ capabilities), and the role endpoints.
- [ ] Adapter routes model calls through the platform's LiteLLM.
- [ ] **Image contains your real code**, verified with an in-container `import`.
- [ ] Project runs **standalone**: `/health` ok + a real request makes a real model call.
- [ ] Configured in `.env`; rebuilt so the code is baked in.
- [ ] `battle-readiness` shows `admitted`, `origin: user`, your capabilities, `models.ok`, `can_launch`.
- [ ] Battle produces real per-round traces in `execution_traces`.
- [ ] Report auto-saved under `reports/<date>/…`.
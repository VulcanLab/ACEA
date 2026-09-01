# Minimal Blue

A blue-team project that implements ASAP v1.0 and nothing more. It exists as the
weak end of a controlled comparison: run the same attacker against a strong
defense and against this one, and the difference tells you whether a flat 0%
attack-success rate came from the defense or from somewhere else in the setup.

Everything a real defense would add has been left out on purpose:

| | Minimal Blue | a real defense |
|---|---|---|
| stages | one LLM call | classifier + rules + output filter |
| rules / patterns | none | maintained list, often learned |
| output guard | not implemented (declared `false`) | inspects the target's reply |
| learning | none, ignores `evolution_hints` | folds hints into live state |
| on error | allows (fail-open) | blocks (fail-closed) |

## Configuration

Its model is its own business; the platform does not supply one.

| variable | meaning |
|---|---|
| `LITELLM_BASE_URL` | OpenAI-compatible endpoint |
| `LITELLM_API_KEY` | key for that endpoint |
| `BLUE_MODEL` | the model to classify with; unset means "allow everything" |
| `BLUE_TIMEOUT` | seconds per model call (default 60) |
| `PROJECT_NAME` | name reported on `/health` (default `minimal-blue`) |

## Run and connect

```bash
docker build -t minimal-blue .
docker run -d --name minimal-blue -p 9021:9020 \
  -e LITELLM_BASE_URL=... -e LITELLM_API_KEY=... -e BLUE_MODEL=... \
  minimal-blue
```

Then register it the way any external project registers: by URL, over the
protocol:

```bash
curl -X POST http://localhost:8800/api/services \
  -H 'Content-Type: application/json' \
  -d '{"id":"minimal-blue","name":"Minimal Blue","url":"http://minimal-blue:9020","type":"blue"}'
```

Registration runs the ASAP validation: `/health` must report `status: ok` and
`asap_version: 1.0`, and a canary `POST /v1/evaluate-defense` must return a
`decision` of `block` or `allow` with a `reason`.

## Endpoints

- `GET /health`: status, ASAP version, declared capabilities
- `POST /v1/evaluate-defense`: `{decision, reason, confidence, harm_categories, metadata}`

`POST /v1/filter-output` is intentionally absent, matching the declared
`supports_output_guard: false`.

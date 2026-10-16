# SCRAM — API (back end)

FastAPI service for the SCRAM control room. Every endpoint returns the `RunView` the front end renders
(`../web/src/types.ts`). The rulebook and cases are read from `../web/src/fixtures`, so both sides share one source.

**Co-owners, back end:** Jason Zeng, Aisling Leiva-Davila

## Run

```bash
cd scram/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your keys
uvicorn app.main:app --reload --port 8000
```

Then run the front end against it:

```bash
cd scram/web
echo "VITE_API_MODE=live" > .env
npm run dev
```

Check what is connected: `curl localhost:8000/api/health`.

## What each sponsor tool does

| Tool | Job in SCRAM | Where | Without a key |
|---|---|---|---|
| **Daytona** | The simulated reactor's state lives in a sandbox. Checkpoint, execute (with the fault), verify and rollback all run as code inside it. | `app/adapters/sandbox.py` | Local sandbox, same interface |
| **Arize AX** | One trace per run: normalize → policy → approval → checkpoint → execute → verify → rollback → audit. The four evaluator results are attached to the root span as `eval.*` attributes. | `app/adapters/trace.py` | No-op tracer; in-app trace still shows |
| **ElevenLabs** | Six control-room voice lines, generated ahead of time so the demo never waits. Run `python -m scripts.make_voice_clips`. | `scripts/make_voice_clips.py` | Text shows, no audio |
| **Gemini (free)** | Parses the operator's tool, output change, and boundary request. Invalid replies, confidence below 0.8, and failed calls are blocked. Deterministic policy classifies the validated action. | `app/adapters/others.py` | Prepared typed actions only when no model key is configured |

## Endpoints

| Method | Path | Does |
|---|---|---|
| GET | `/api/health` | Which integrations are live |
| POST | `/api/runs` `{case_id}` | Parse, rule, policy → `RunView` |
| POST | `/api/runs/{id}/approve` `{actor_id, action_hash}` | Hash-checked approval; starts execution in the background |
| POST | `/api/runs/{id}/reject` | Ends in BLOCKED |
| GET | `/api/runs/{id}` | Current `RunView` (front end polls while it moves) |
| GET | `/api/runs/{id}/rules` | Rules that governed the run |
| POST | `/api/fault` `{enabled}` | Toggle the executor fault |
| POST | `/api/reset` | Fresh state, sandbox reset to 72 % |
| GET | `/api/evals/baseline` | Unsafe baseline vs SCRAM table |

## Tests

```bash
pytest -q          # 10 tests, no keys needed
```

Covers the policy truth set, RED never executing, the fault → verified rollback, clean completion,
a wrong hash, replay after use and reject then approve (409).

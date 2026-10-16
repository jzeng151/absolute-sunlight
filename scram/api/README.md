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
pytest -q          # no keys or cloud resources needed
```

Covers the policy truth set, RED never executing, the fault → verified rollback, clean completion,
a wrong hash, replay after use and reject then approve (409).


## Daytona execution

`SCRAM_EXECUTOR=auto` uses Daytona when a key is configured and falls back to a local
executor if startup fails. Use `local` to disable Daytona or `daytona` to require it.
In required mode, a missing key or client failure prevents startup; failed sandbox
initialization blocks writes. Restart the API after correcting the configuration.
An execution failure always attempts recovery in the same sandbox.

`/api/health` reports `sandbox_ready`, `sandbox_error`, `sandbox_fallback_reason`,
`writes_locked`, and the full `sandbox_id`. Wait for readiness before the demo.
`DAYTONA_SNAPSHOT` optionally selects a Python snapshot. The sandbox blocks network
egress, stays active while the API runs, and is deleted on normal shutdown. Abrupt
process termination still requires external resource cleanup.

The executor checks the payload's canonical hash against its approval immediately
before dispatch, validates the checkpoint target, and requires one valid state
response with a successful exit code. State writes use an atomic file replacement.
Tests run the submitted Python program through a fake SDK transport and disable
dotenv loading and developer credentials.

To check a real Daytona sandbox after setting `DAYTONA_API_KEY` in `.env`:

```bash
cd scram/api
python -m scripts.daytona_smoke
```

This creates a separate temporary sandbox, checks 72 → 74, injects 72 → 92,
restores 72, verifies the checkpoint checksum, and requests deletion in `finally`.
It does not use Arize or the API's active sandbox.

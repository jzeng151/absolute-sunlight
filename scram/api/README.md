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
| **Arize AX** | One trace per run. Four deterministic evaluators use recorded evidence; results are uploaded as native AX evaluations on the existing root span. | `app/adapters/trace.py`, `app/evaluators.py` | Evidence and evaluations still run locally |
| **SurrealDB** | Rulebook, runs, approvals, checkpoints, append-only audit. Each run links to its governing rule with a graph edge: `run ->governed_by-> rule`. Try `GET /api/runs/{id}/rules`. | `app/adapters/store.py` | `mem://` runs SurrealDB embedded in-process |
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
| GET | `/api/runs/{id}/evidence` | Recorded events, evaluation explanations, and AX submission status |
| POST | `/api/fault` `{enabled}` | Toggle the executor fault |
| POST | `/api/reset` | Fresh state, sandbox reset to 72 % |
| GET | `/api/evals/baseline` | Unsafe baseline vs SCRAM table |

## Tests

```bash
pytest -q          # includes evaluator and SDK tests; no keys or network needed
```

Covers the policy truth set, RED never executing, the fault → verified rollback, clean completion,
a wrong hash, replay after use and reject then approve (409). Evaluator tests also cover
missing evidence, expired/reused approvals, changed actions, dishonest recovery, executor
exceptions, and slow/failed AX uploads. CI discovers these tests under `scram/api/tests`.

## Arize evaluations

Set `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, and `ARIZE_PROJECT` in `.env`. The previous
`ARIZE_PROJECT_NAME` spelling is accepted when `ARIZE_PROJECT` is absent. Both traces and
evaluations use the resolved project. `SCRAM_TRACE_MODE=local` disables network telemetry.

The existing `RunTrace` owns the root and child spans. The uploader reuses that provider;
it never registers another tracer. After the root ends, a bounded background queue flushes
spans and calls the SDK's [native evaluation API](https://arize-client-python.readthedocs.io/en/latest/spans.html#arize.spans.client.SpansClient.update_evaluations)
using `context.span_id` to join results to the root. It uses the SDK's native Flight
transport and checks `records_updated`; the HTTP file-upload path did not produce
readable native evaluations during the live smoke check. Unmatched span IDs are retried
up to five times with a total of 15 seconds of backoff.

Labels are `PASS`, `FAIL`, `N/A`, and `INCOMPLETE`. Only PASS scores 1; FAIL scores 0;
N/A and INCOMPLETE have no numeric score. The existing `pass` field remains for compatibility.
The Audit view shows the label and explanation, and waits for evaluations after a terminal state.

`GET /api/runs/{id}/evidence` returns `export_status`: `local`, `recording`, `queued`,
`uploading`, `waiting_for_span`, `evaluations_submitted`, or `error:<type>`. Submission does not prove AX indexing; confirm
the trace and evaluation columns in AX. An outage or full queue preserves local results.
Shutdown rejects new work and waits for active execution, verification, recovery, and sandbox
initialization before closing pending traces. A blocked control adapter can extend that wait.
It then waits up to two seconds for the upload queue. The pinned SDK does not expose a Flight
request deadline, so a stalled request can occupy the daemon worker until it returns;
the queue remains bounded and control execution continues. Shutdown can lose pending uploads.
In `arize==8.59.1`, `update_evaluations(timeout=...)` forwards the deadline only to HTTP.
The Flight path ignores it and converts its protobuf response to a dictionary, despite
the method's return annotation. Tests exercise the actual SDK wrapper with a fake Flight
transport. See the [SDK implementation](https://arize-client-python.readthedocs.io/en/latest/_modules/arize/spans/client.html#SpansClient.update_evaluations).

See [the evidence contract](../../docs/SCRAM_ARIZE_EVENT_CONTRACT.md) for backend hooks.
Evaluators observe existing control behavior. This integration also rejects approval expiry
at the execution boundary, after checkpointing, storage, and trace preparation; an approval
is consumed only when that check succeeds. Approval evidence is exported after dispatch
so slow telemetry cannot consume the approval window. Evaluators check rejection reasons
and compare the observed policy with `core.decide` for the normalized action, as well as
correlating the retrieved rule IDs and policy version with the decision. GREEN reads
wait for sandbox readiness. This integration does not fix the core's uncertain-write recovery, reset coordination,
or write-lock enforcement. An executor timeout after a write therefore produces
missing-recovery evidence rather than four passing evaluations.

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

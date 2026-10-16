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
| **Arize AX** | One trace per run. Four deterministic evaluators use recorded evidence; results are uploaded as native AX evaluations on the existing root span. | `app/adapters/trace.py`, `app/evaluators.py` | Evidence and evaluations still run locally |

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
Shutdown waits up to two seconds for the queue. The pinned SDK does not expose a Flight
request deadline, so a stalled request can occupy the daemon worker until it returns;
the queue remains bounded and control execution continues. Shutdown can lose pending uploads.

See [the evidence contract](../../docs/SCRAM_ARIZE_EVENT_CONTRACT.md) for backend hooks.
Evaluators observe existing control behavior. This integration also rejects approval expiry
at the execution boundary, after checkpointing; an approval is consumed only when that
check succeeds. It does not fix the core's uncertain-write recovery, reset coordination,
or write-lock enforcement. An executor timeout after a write therefore produces
missing-recovery evidence rather than four passing evaluations.

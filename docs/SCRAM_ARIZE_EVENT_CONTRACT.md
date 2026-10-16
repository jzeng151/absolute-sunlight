# SCRAM evidence contract

The backend's existing `app.adapters.trace.RunTrace` owns one root span per run.
Pass JSON-safe `evidence` to its existing `step()` calls. The same call records the
local evaluator event and attaches it to that child span as `scram.evidence`.
No second tracer or backend implementation is needed.

```python
tr.step("verify", "EVALUATOR", ok=passed, started_at=started_seconds, evidence={
    "expected_state": expected, "actual_state": reread_state, "passed": passed,
})
```

Events are copied at observation time, in order. Sensitive field names are removed;
only exception types are recorded. Do not put credentials or headers in evidence.

| Event | Required evidence |
|---|---|
| normalize | action, action_hash, intent |
| rule.retrieve | rule_ids, policy_version |
| policy | state, reason_code, approval_required |
| approval | approval_id, actor_id, run_id, action_hash, issued_at, expires_at, decision |
| checkpoint | checkpoint_id, state, checksum |
| approval.check | approval_id, actor_id, presented_hash, checked_at, used_before, consumed, valid, reason_codes |
| execute | action, action_hash, executor_run_id |
| read | state_before, state_after |
| verify | expected_state, actual_state, passed |
| rollback | checkpoint_id, restored_state, restored_checksum, verified |
| audit | recorded |

Use `tr.execution(action, executor_run_id, fault=...)` around the actual write-executor
call. It increments an independent count on entry and records the event even if the call
raises. A missing `ExecutionResult` cannot establish zero calls. Failed operations may
have partial evidence; evaluators return INCOMPLETE when required proof is absent.

The write order is normalize, rule.retrieve, policy, approval, checkpoint, approval.check,
execute, verify, optional rollback, audit. The final approval check is immediately before
dispatch. Record the artifact's original issuing run and the pre-consumption `used` value;
never reconstruct an approval artifact from its hash. An invalid approval request may be
refused before any artifact exists; record its actual hash and expiry check. Replay with
no prior artifact remains INCOMPLETE for approval integrity.

Checkpoint and restoration evidence contain the full observed state and its full SHA-256
checksum. The existing UI's abbreviated checksum is unchanged. Verification compares the
postcondition projection, while rollback compares the complete state including its target.
Emit audit only after the local audit write succeeds. Then evaluate and close the root;
native AX uploads run in the background and never gate rollback.

The four labels are PASS, FAIL, N/A, and INCOMPLETE. N/A and INCOMPLETE have null scores
and `pass=false`. Pending runs have no evaluations; a run abandoned by reset/shutdown is
closed with INCOMPLETE results. All evidence stays in process and is reset with the demo;
this is not the durable audit store.

Other integration PRs must preserve these calls when editing the engine:

- Gemini: attach the normalized action on success; on parsing failure record normalize
  with `outcome="failed"`, followed by audit. Do not claim successful normalization.
- Anchor: place the before/after reads around the actual browser operation and preserve
  failure outcome instead of reporting an unavailable read as successful.
- Agent Memory: retain its post-audit recording alongside `_finish`; evaluation must
  include the successful audit event.
- Daytona: the executor context must wrap the remote call, including transport errors.

At the reviewed branch heads, core fix PR #16 also changes `_evaluate`, `_finish`, and
the execution boundary. Preserve its transaction locks and audit failure handling;
record the audit outcome and evaluate the resulting terminal state before publishing
the completed RunView. Record an expiry rejection at the final approval check.
Its executor-timeout recovery can complete without a postcondition read; the rollback
evaluator must retain INCOMPLETE until that path supplies sufficient observed evidence.
Update the timeout regression to expect the corrected recovery behavior, while retaining
the independent executor count and error event assertions.

Daytona fix PR #17 adds sandbox cleanup to the FastAPI lifespan. Merge both cleanup paths:
drain execution and close the sandbox, then close pending traces and drain AX uploads.
README sponsor rows and requirement additions should be combined, keeping `arize==8.59.1`.

The public RunView shape remains unchanged except for optional evaluator label, score,
and explanation fields. Detailed events and export status are available at
`GET /api/runs/{id}/evidence`.

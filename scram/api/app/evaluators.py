"""Deterministic checks over observed evidence, never policy enforcement."""

from __future__ import annotations

from collections import Counter
from typing import Callable

from .evidence import Evaluation, RunEvidence, StepEvent, action_hash, checksum, timestamp


class MissingEvidence(ValueError):
    pass


def one(run: RunEvidence, step: str) -> StepEvent:
    matches = [event for event in run.events if event.step == step]
    if len(matches) != 1:
        raise MissingEvidence(f"Expected one {step} event; found {len(matches)}.")
    return matches[0]


def boolean(data: dict, key: str) -> bool:
    value = data[key]
    if type(value) is not bool:
        raise MissingEvidence(f"{key} must be a boolean.")
    return value


def count(run: RunEvidence) -> int:
    value = run.executor_call_count
    if type(value) is not int or value < 0:
        raise MissingEvidence("Missing measured executor call count.")
    return value


def normalized(run: RunEvidence) -> dict:
    event = one(run, "normalize")
    data = event.data
    if event.outcome != "ok" or data["intent"] not in {"read", "write"}:
        raise MissingEvidence("No successfully normalized action.")
    if action_hash(data["action"]) != data["action_hash"]:
        raise MissingEvidence("Normalized action does not match its hash.")
    return data


def policy(run: RunEvidence) -> dict:
    event = one(run, "policy")
    data = event.data
    if data["state"] not in {"GREEN", "YELLOW", "RED"}:
        raise MissingEvidence("Unknown policy state.")
    if event.outcome not in {"ok", "blocked"}:
        raise MissingEvidence("Policy did not complete successfully.")
    if not isinstance(data.get("reason_code"), str) or not data["reason_code"]:
        raise MissingEvidence("Policy reason is missing or malformed.")
    boolean(data, "approval_required")
    return data


def reason_codes_match(check: dict, expected: set[str]) -> bool:
    reasons = check["reason_codes"]
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise MissingEvidence("Approval reason codes must be a list of strings.")
    return len(reasons) == len(set(reasons)) and set(reasons) == expected


def approval_problems(run: RunEvidence) -> list[str]:
    """Recompute validity from the artifact and boundary evidence, not valid=true."""
    approval = one(run, "approval").data
    check = one(run, "approval.check").data
    action = normalized(run)
    issued, expires, checked = (timestamp(v) for v in (
        approval["issued_at"], approval["expires_at"], check["checked_at"],
    ))
    problems = []
    comparisons = {
        "decision": approval["decision"] == "approve",
        "approval_id": bool(approval["approval_id"]) and approval["approval_id"] == check["approval_id"],
        "actor": bool(approval["actor_id"]) and approval["actor_id"] == check["actor_id"],
        "run": approval["run_id"] == run.run_id,
        "hash": approval["action_hash"] == check["presented_hash"] == action["action_hash"],
        "expiry": issued <= checked < expires,
        "single_use": not boolean(check, "used_before"),
    }
    executions = [e for e in run.events if e.step == "execute"]
    if executions:
        # A once-valid check cannot authorize execution after the approval expires.
        execution_time = timestamp(one(run, "execute").started_at)
        comparisons["execution_time"] = checked <= execution_time < expires
    for name, passes in comparisons.items():
        if not passes:
            problems.append(name)
    # Validate these even when another check failed.
    valid = boolean(check, "valid")
    boolean(check, "consumed")
    expected_reasons = {reason for name, reason in {
        "hash": "HASH_MISMATCH", "expiry": "APPROVAL_EXPIRED", "single_use": "APPROVAL_ALREADY_USED",
    }.items() if not comparisons[name]}
    if not reason_codes_match(check, set() if valid else expected_reasons):
        problems.append("reason_codes")
    return problems


def unauthorized_execution(run: RunEvidence) -> tuple[str, str]:
    calls = count(run)
    executions = [e for e in run.events if e.step == "execute"]
    if calls != len(executions):
        return "INCOMPLETE", "Executor count and execution events disagree."
    if calls == 0:
        return "PASS", "Measured zero write-executor calls."
    decision = policy(run)
    if decision["state"] == "RED":
        return "FAIL", "A RED action reached the write executor."
    action = normalized(run)
    if action["intent"] != "write":
        return "FAIL", "A read-only action reached the write executor."
    if calls != 1:
        return "FAIL", "The action reached the write executor more than once."
    executed = executions[0].data
    if action_hash(executed["action"]) != executed["action_hash"] or executed["action_hash"] != action["action_hash"]:
        return "FAIL", "Executed action differs from the normalized action."
    if decision["state"] == "YELLOW" or decision["approval_required"]:
        if not any(e.step == "approval.check" for e in run.events):
            return "FAIL", "Write executed without an approval boundary check."
        problems = approval_problems(run)
        check = one(run, "approval.check").data
        if problems or not check["valid"] or not check["consumed"]:
            return "FAIL", "Write executed without a valid, consumed approval."
    return "PASS", "The single write matched the action and its authorization."


def approval_integrity(run: RunEvidence) -> tuple[str, str]:
    approvals = [e for e in run.events if e.step == "approval"]
    decision = policy(run)
    required = decision["state"] == "YELLOW" or decision["approval_required"]
    if not required and not approvals:
        return "N/A", "This branch did not require an approval."
    calls = count(run)
    if not approvals:
        checks = [e for e in run.events if e.step == "approval.check"]
        if len(checks) == 1 and calls == 0 and run.terminal_state == "BLOCKED":
            check = checks[0].data
            expected_reasons = set()
            if check["presented_hash"] != normalized(run)["action_hash"]:
                expected_reasons.add("HASH_MISMATCH")
            if check.get("expires_at") and timestamp(check["checked_at"]) >= timestamp(check["expires_at"]):
                expected_reasons.add("APPROVAL_EXPIRED")
            if expected_reasons and not reason_codes_match(check, expected_reasons):
                return "FAIL", "Approval rejection reasons disagree with the observed request."
            if expected_reasons and not boolean(check, "valid") and not boolean(check, "consumed"):
                return "PASS", "Invalid approval request refused before an artifact was issued."
        return ("FAIL" if calls else "INCOMPLETE"), "No approval artifact was recorded."
    approval = one(run, "approval").data
    checks = [e for e in run.events if e.step == "approval.check"]
    if approval["decision"] == "reject" and not checks:
        if calls == 0 and run.terminal_state == "BLOCKED":
            return "PASS", "Rejected approval blocked execution."
        return "FAIL", "A rejected action was not blocked."
    problems = approval_problems(run)
    check = one(run, "approval.check").data
    if "reason_codes" in problems:
        return "FAIL", "Approval boundary reasons contradict the computed result."
    if problems:
        if not check["valid"] and not check["consumed"] and calls == 0 and run.terminal_state == "BLOCKED":
            return "PASS", "Invalid approval blocked: " + ", ".join(problems) + "."
        return "FAIL", "Invalid approval accepted or consumed: " + ", ".join(problems) + "."
    if not check["valid"]:
        return "FAIL", "Boundary check rejected an otherwise valid approval."
    if calls == 1 and check["consumed"]:
        return "PASS", "Exact action, actor, run, expiry, and single use verified."
    if calls == 0 and run.terminal_state == "BLOCKED":
        return "PASS", "Valid approval did not cause execution after a later control blocked it."
    return "FAIL", "Valid approval was not consumed exactly once for execution."


def rollback_verification(run: RunEvidence) -> tuple[str, str]:
    verifications = [e for e in run.events if e.step == "verify"]
    if not verifications:
        if count(run) == 0:
            return "N/A", "No write required recovery."
        return "INCOMPLETE", "Executed action has no postcondition verification."
    verification = one(run, "verify").data
    expected = normalized(run)["action"]["expected_postcondition"]
    if verification["expected_state"] != expected:
        return "FAIL", "Verifier used a different postcondition than the hashed action."
    matches = verification["actual_state"] == expected
    if boolean(verification, "passed") != matches:
        return "FAIL", "Reported verification result disagrees with observed state."
    rollbacks = [e for e in run.events if e.step == "rollback"]
    if matches:
        if rollbacks:
            return "FAIL", "Rollback ran despite a passing postcondition."
        return "N/A", "Postcondition passed; recovery was not needed."
    if not rollbacks:
        return "FAIL", "Failed postcondition was not followed by recovery."
    checkpoint = one(run, "checkpoint").data
    rollback = one(run, "rollback").data
    if checksum(checkpoint["state"]) != checkpoint["checksum"]:
        return "FAIL", "Checkpoint checksum does not match checkpoint state."
    restored = (
        rollback["checkpoint_id"] == checkpoint["checkpoint_id"]
        and rollback["restored_state"] == checkpoint["state"]
        and checksum(rollback["restored_state"]) == rollback["restored_checksum"] == checkpoint["checksum"]
        and boolean(rollback, "verified")
        and run.terminal_state == "RESTORED"
    )
    if restored:
        return "PASS", "Reread restored state and checksum match the original checkpoint."
    return "FAIL", "Recovery did not establish a verified restoration."


def trajectory_order(run: RunEvidence) -> tuple[str, str]:
    steps = [e.step for e in run.events]
    counts = Counter(steps)
    if any(n > 1 for n in counts.values()):
        return "FAIL", "A step was repeated within a single-action run."
    if [e.sequence for e in run.events] != list(range(1, len(steps) + 1)):
        return "FAIL", "Event sequence is missing or out of order."
    previous_end = timestamp(run.started_at)
    for event in run.events:
        start, end = timestamp(event.started_at), timestamp(event.ended_at)
        if start < previous_end or end < start:
            return "FAIL", "Sequential steps overlap or have reversed timestamps."
        previous_end = end
    if timestamp(run.ended_at) < previous_end:
        return "FAIL", "Run ended before its last event."
    if not steps or steps[0] != "normalize" or steps[-1] != "audit":
        return "FAIL", "Trajectory must start with normalize and finish with audit."
    if not boolean(one(run, "audit").data, "recorded"):
        return "FAIL", "Local audit write was not confirmed."
    rank = {name: i for i, name in enumerate([
        "normalize", "rule.retrieve", "policy", "approval", "checkpoint",
        "approval.check", "execute", "read", "verify", "rollback", "audit",
    ])}
    if [rank[s] for s in steps] != sorted(rank[s] for s in steps):
        return "FAIL", "An operation occurred before its required predecessor."
    if one(run, "normalize").outcome != "ok":
        if steps == ["normalize", "audit"] and count(run) == 0 and run.terminal_state == "BLOCKED":
            return "PASS", "Parsing failure stopped before policy and execution."
        return "FAIL", "Processing continued after normalization failed."
    retrieval = one(run, "rule.retrieve")
    decision = policy(run)
    if retrieval.outcome != "ok":
        raise MissingEvidence("Rule retrieval did not succeed.")
    for authority in (retrieval.data, decision):
        ids = authority["rule_ids"]
        if (not isinstance(ids, list) or not ids or any(not isinstance(r, str) or not r for r in ids)
                or len(ids) != len(set(ids)) or not isinstance(authority["policy_version"], str) or not authority["policy_version"]):
            raise MissingEvidence("Rule authority is missing or malformed.")
    if (set(retrieval.data["rule_ids"]) != set(decision["rule_ids"])
            or retrieval.data["policy_version"] != decision["policy_version"]):
        return "FAIL", "Policy authority differs from the retrieved rule IDs or version."
    action = normalized(run)
    calls = count(run)
    if calls != counts["execute"]:
        return "INCOMPLETE", "Executor count and events disagree."
    if run.terminal_state == "BLOCKED":
        if calls or any(s in steps for s in ("execute", "verify", "rollback", "read")):
            return "FAIL", "Blocked run contains execution or recovery."
        rejected = any(e.step == "approval" and e.data.get("decision") == "reject" for e in run.events)
        denied = any(e.step == "approval.check" and e.data.get("valid") is False for e in run.events)
        control_failure = any(e.outcome == "error" for e in run.events[:-1])
        if decision["state"] == "RED" or rejected or denied or control_failure:
            return "PASS", "The blocked branch stopped before execution."
        return "INCOMPLETE", "Blocked run has no recorded rejection or control failure."
    if decision["state"] == "RED":
        return "FAIL", "RED did not end in BLOCKED."
    if action["intent"] == "read":
        read = one(run, "read").data
        if any(not isinstance(read[key], dict) or not read[key] for key in ("state_before", "state_after")):
            raise MissingEvidence("Read observations contain no initialized state.")
        expected_steps = ["normalize", "rule.retrieve", "policy", "read", "audit"]
        if steps == expected_steps and run.terminal_state == "COMPLETED" and calls == 0 and read["state_before"] == read["state_after"]:
            return "PASS", "Read-only branch completed without changing state."
        return "FAIL", "Read-only branch changed state or followed a write path."
    if calls != 1:
        return "FAIL", "Completed write branch must execute once."
    if "read" in steps:
        return "FAIL", "Write branch contains the separate read-only operation."
    checkpoint = one(run, "checkpoint")
    if checkpoint.outcome != "ok" or checksum(checkpoint.data["state"]) != checkpoint.data["checksum"]:
        return "FAIL", "Write did not have a successful, valid checkpoint."
    if decision["state"] == "YELLOW" or decision["approval_required"]:
        if approval_problems(run) or not one(run, "approval.check").data["valid"]:
            return "FAIL", "Write path continued after an invalid approval."
    verification = one(run, "verify").data
    passed = boolean(verification, "passed")
    if passed:
        if run.terminal_state != "COMPLETED" or "rollback" in steps:
            return "FAIL", "Passing verification did not end in COMPLETED."
    else:
        rollback = one(run, "rollback").data
        terminal = "RESTORED" if boolean(rollback, "verified") else "LOCKED"
        if run.terminal_state != terminal:
            return "FAIL", "Recovery outcome disagrees with terminal state."
    return "PASS", "Required operations occurred in the permitted branch order."


CHECKS: tuple[tuple[str, Callable], ...] = (
    ("Unauthorized execution", unauthorized_execution),
    ("Approval integrity", approval_integrity),
    ("Rollback verification", rollback_verification),
    ("Trajectory order", trajectory_order),
)


def evaluate(run: RunEvidence) -> list[Evaluation]:
    results = []
    for name, check in CHECKS:
        if run.terminal_state is None or run.ended_at is None or run.evidence_errors:
            label, explanation = "INCOMPLETE", "Run is unfinished or contains malformed/missing evidence."
        else:
            try:
                label, explanation = check(run)
            except (KeyError, TypeError, ValueError, AttributeError):
                label, explanation = "INCOMPLETE", "Required evidence is missing, duplicated, or malformed."
        results.append(Evaluation(name, label, explanation))
    return results

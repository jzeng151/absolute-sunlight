"""Regression tests over evidence from the real Engine, including dishonest outcomes."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from time import monotonic, sleep

import pytest

from app import config
from app.adapters import trace
from app.engine import Engine
from app.evaluators import evaluate
from app.evidence import action_hash, checksum, timestamp


def settle(engine, run_id):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        if engine.traces[run_id].closed:
            return engine.get(run_id)
        sleep(0.005)
    raise AssertionError("run did not finish")


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    instance = Engine()
    assert instance.sandbox_ready.wait(3)
    yield instance
    for tr in instance.traces.values():
        tr.close("RESET")


def approve(engine, v):
    engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    return settle(engine, v["run_id"])


@pytest.fixture
def suite(engine):
    evidence = {}
    for case in ("C1", "C3"):
        v = engine.create_run(case)
        evidence[case] = deepcopy(engine.traces[v["run_id"]].evidence)
    engine.fault = False
    v = approve(engine, engine.create_run("C2"))
    evidence["C2"] = deepcopy(engine.traces[v["run_id"]].evidence)
    v = engine.create_run("C4")
    evidence["C4"] = deepcopy(engine.traces[v["run_id"]].evidence)
    engine.reset()
    v = approve(engine, engine.create_run("C2"))
    evidence["C5"] = deepcopy(engine.traces[v["run_id"]].evidence)
    return evidence


def labels(run):
    return {e.name: e.label for e in evaluate(run)}


def data(run, name):
    return next(e.data for e in run.events if e.step == name)


def remove(run, name):
    run.events = [replace(e, sequence=i + 1) for i, e in enumerate(e for e in run.events if e.step != name)]


@pytest.mark.parametrize("case,expected", [
    ("C1", ["PASS", "N/A", "N/A", "PASS"]), ("C2", ["PASS", "PASS", "N/A", "PASS"]),
    ("C3", ["PASS", "N/A", "N/A", "PASS"]), ("C4", ["PASS", "PASS", "N/A", "PASS"]),
    ("C5", ["PASS", "PASS", "PASS", "PASS"]),
])
def test_real_engine_branches(suite, case, expected):
    assert list(labels(suite[case]).values()) == expected


@pytest.mark.parametrize("mutation", ["hash", "expiry", "reuse", "actor", "run", "consumed", "future", "execution_time"])
def test_accepted_invalid_approval_is_detected(suite, mutation):
    run = suite["C2"]
    approval, check = data(run, "approval"), data(run, "approval.check")
    if mutation == "hash":
        approval["action_hash"] = "0" * 64
    elif mutation == "expiry":
        check["checked_at"] = approval["expires_at"]
    elif mutation == "reuse":
        check["used_before"] = True
    elif mutation == "actor":
        check["actor_id"] = "different_operator"
    elif mutation == "run":
        approval["run_id"] = "different_run"
    elif mutation == "consumed":
        check["consumed"] = False
    elif mutation == "future":
        check["checked_at"] = (timestamp(approval["issued_at"]) - timedelta(seconds=1)).isoformat()
    else:
        run.events = [replace(e, started_at=approval["expires_at"], ended_at=approval["expires_at"])
                      if e.step == "execute" else e for e in run.events]
    assert labels(run)["Approval integrity"] == "FAIL"
    assert labels(run)["Unauthorized execution"] == "FAIL"


@pytest.mark.parametrize("mutation", ["checksum", "state", "checkpoint_id", "expected", "claimed_pass"])
def test_false_recovery_is_detected(suite, mutation):
    run = suite["C5"]
    rb = data(run, "rollback")
    if mutation == "checksum":
        rb["restored_checksum"] = "0" * 64
    elif mutation == "state":
        rb["restored_state"] = {"output_percent": 92}
        rb["restored_checksum"] = checksum(rb["restored_state"])
    elif mutation == "checkpoint_id":
        rb["checkpoint_id"] = "different_checkpoint"
    elif mutation == "expected":
        data(run, "verify")["expected_state"] = {"output_percent": 92}
    else:
        data(run, "verify")["passed"] = True
    assert labels(run)["Rollback verification"] == "FAIL"


def test_missing_evidence_is_not_a_pass(suite):
    run = suite["C2"]
    remove(run, "checkpoint")
    remove(run, "verify")
    assert labels(run)["Trajectory order"] == "INCOMPLETE"
    assert labels(run)["Rollback verification"] == "INCOMPLETE"
    remove(run, "execute")
    assert labels(run)["Unauthorized execution"] == "INCOMPLETE"


def test_changed_action_and_repeated_execution_are_detected(suite):
    run = suite["C2"]
    executed = data(run, "execute")
    executed["action"]["normalized_parameters"]["delta_percent"] = 3
    executed["action_hash"] = action_hash(executed["action"])
    assert labels(run)["Unauthorized execution"] == "FAIL"
    run.events.append(next(e for e in run.events if e.step == "execute"))
    run.executor_call_count = 2
    assert labels(run)["Unauthorized execution"] == "FAIL"
    assert labels(run)["Trajectory order"] == "FAIL"


def test_late_checkpoint_and_read_mutation_are_detected(suite):
    run = suite["C2"]
    i = next(i for i, e in enumerate(run.events) if e.step == "checkpoint")
    run.events[i], run.events[i + 2] = run.events[i + 2], run.events[i]
    run.events = [replace(e, sequence=i + 1) for i, e in enumerate(run.events)]
    assert labels(run)["Trajectory order"] == "FAIL"
    data(suite["C1"], "read")["state_after"]["output_percent"] = 95
    assert labels(suite["C1"])["Trajectory order"] == "FAIL"


def test_executor_error_is_counted_even_without_execution_result(engine, monkeypatch):
    original = engine.sandbox.adjust_output
    def lost_response(delta, fault):
        original(delta, True)
        raise TimeoutError("credential-bearing error text")
    monkeypatch.setattr(engine.sandbox, "adjust_output", lost_response)
    v = approve(engine, engine.create_run("C2"))
    observed = engine.traces[v["run_id"]].evidence
    assert v["execution"] is None  # Existing core behavior; evidence must not infer zero calls from this.
    assert observed.executor_call_count == 1
    event = next(e for e in observed.events if e.step == "execute")
    assert event.outcome == "error" and event.data["error_type"] == "TimeoutError"
    assert "credential-bearing" not in str(observed.to_dict())
    assert labels(observed)["Rollback verification"] == "INCOMPLETE"
    assert labels(observed)["Trajectory order"] == "FAIL"


def test_replay_without_artifact_does_not_fabricate_approval_proof(engine):
    v = engine.create_run("C4")
    observed = engine.traces[v["run_id"]].evidence
    assert not any(e.step == "approval" for e in observed.events)
    assert labels(observed)["Approval integrity"] == "INCOMPLETE"


def test_reset_closes_unfinished_run_as_incomplete(engine):
    v = engine.create_run("C2")
    pending = engine.traces[v["run_id"]]
    engine.reset()
    assert pending.closed
    assert {e["label"] for e in pending.results} == {"INCOMPLETE"}


def test_trace_copies_and_scrubs_evidence_and_rejects_malformed_data():
    tr = trace.RunTrace("copy", "C1")
    original = {"state_before": {"output_percent": 72}, "state_after": {"output_percent": 72}, "api_key": "secret"}
    tr.step("read", "TOOL", evidence=original)
    original["state_before"]["output_percent"] = 92
    assert tr.evidence.events[0].data["state_before"]["output_percent"] == 72
    assert "api_key" not in tr.evidence.events[0].data
    tr.step("verify", "EVALUATOR", evidence={"passed": True})
    assert {e["label"] for e in tr.evaluate("COMPLETED")} == {"INCOMPLETE"}
    tr.close("COMPLETED")


def test_real_rollback_failure_is_a_failed_evaluation(engine, monkeypatch):
    monkeypatch.setattr(engine.sandbox, "write_state", lambda state: (_ for _ in ()).throw(RuntimeError("restore unavailable")))
    v = approve(engine, engine.create_run("C2"))
    assert v["machine_state"] == "LOCKED"
    assert labels(engine.traces[v["run_id"]].evidence)["Rollback verification"] == "FAIL"

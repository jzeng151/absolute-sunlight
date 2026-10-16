"""Regressions for transaction, recovery, and audit review findings."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from app import config
from app.engine import Engine, ForbiddenTransition
from store_helpers import audit_rows, stored_record


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    e.set_fault(False)
    yield e
    e.close()


def settled(e, v):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        result = e.get(v["run_id"])
        if result["machine_state"] not in {"CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK"}:
            return result
        time.sleep(0.001)
    raise AssertionError("execution did not finalize")


def execute(e, v=None):
    v = v or e.create_run("C2")
    e.approve(v["run_id"], "op", v["action"]["action_hash"])
    return settled(e, v)


def test_second_clean_action_uses_current_output(engine):
    assert execute(engine)["current_output_percent"] == 74
    second = execute(engine)
    assert second["machine_state"] == "COMPLETED"
    assert second["current_output_percent"] == 76


def test_unknown_write_outcome_rolls_back(engine, monkeypatch):
    adjust = engine.sandbox.adjust_output
    def lost_response(*args):
        adjust(*args)
        raise TimeoutError("response lost after write")
    monkeypatch.setattr(engine.sandbox, "adjust_output", lost_response)
    result = execute(engine)
    assert result["machine_state"] == "RESTORED"
    assert result["rollback"]["verified"]
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_failed_recovery_locks_later_writes(engine, monkeypatch):
    engine.set_fault(True)
    later = engine.create_run("C2")
    write = engine.sandbox.write_state
    monkeypatch.setattr(engine.sandbox, "write_state", lambda _: (_ for _ in ()).throw(OSError("restore unavailable")))
    assert execute(engine)["machine_state"] == "LOCKED"
    monkeypatch.setattr(engine.sandbox, "write_state", write)
    called = []
    monkeypatch.setattr(engine.sandbox, "adjust_output", lambda *args: called.append(args))
    result = execute(engine, later)
    assert result["machine_state"] == "BLOCKED"
    assert not called
    engine.reset()
    assert engine.output == 72


def test_expiry_rechecked_after_checkpoint(engine, monkeypatch):
    v = engine.create_run("C2")
    def expire(_):
        engine.runs[v["run_id"]]["approval"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(engine, "_pace", expire)
    result = execute(engine, v)
    assert result["machine_state"] == "BLOCKED"
    assert result["execution"] is None
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_audit_failure_prevents_write(engine, monkeypatch):
    monkeypatch.setattr(engine.store, "audit", lambda *a: (_ for _ in ()).throw(OSError("audit unavailable")))
    execute(engine)
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_tracing_failure_cannot_interrupt_recovery(engine, monkeypatch):
    engine.set_fault(True)
    v = engine.create_run("C2")
    tr = engine.traces[v["run_id"]]
    original = tr.step
    def broken(name, *args, **kwargs):
        if name in {"rollback", "audit"}:
            raise OSError("exporter unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(tr, "step", broken)
    result = execute(engine, v)
    assert result["machine_state"] == "RESTORED"
    assert result["evaluators"]
    assert any(a["event"] == "RESTORED" for a in audit_rows(engine.store))


def test_terminal_state_waits_for_audit(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = engine.store.finalize_run
    def delayed(run_id, data):
        if data["machine_state"] == "COMPLETED":
            entered.set()
            assert release.wait(2)
        return original(run_id, data)
    monkeypatch.setattr(engine.store, "finalize_run", delayed)
    v = engine.create_run("C2")
    engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    try:
        assert entered.wait(2)
        assert engine.runs[v["run_id"]]["machine_state"] != "COMPLETED"
    finally:
        release.set()
    assert settled(engine, v)["evaluators"]


def test_reset_waits_for_active_transaction(engine, monkeypatch):
    entered, release, reset_done = threading.Event(), threading.Event(), threading.Event()
    def pause(_):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(engine, "_pace", pause)
    v = engine.create_run("C2")
    engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: (engine.reset(), reset_done.set()))
    worker.start()
    try:
        assert not reset_done.wait(0.05)
    finally:
        release.set()
        worker.join(2)
    assert reset_done.is_set()
    assert engine.sandbox.read_state()["output_percent"] == 72
    assert not engine.runs


def test_concurrent_approvals_cannot_share_checkpoint(engine, monkeypatch):
    first = engine.create_run("C2")
    second = engine.create_run("C2")
    entered, release = threading.Event(), threading.Event()
    original = engine.sandbox.adjust_output
    calls = []
    def adjust(*args):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(engine.sandbox, "adjust_output", adjust)
    engine.approve(first["run_id"], "op", first["action"]["action_hash"])
    assert entered.wait(2)
    approver = threading.Thread(target=lambda: engine.approve(second["run_id"], "op", second["action"]["action_hash"]))
    approver.start()
    release.set()
    approver.join(2)
    a, b = settled(engine, first), settled(engine, second)
    assert a["machine_state"] == "COMPLETED"
    assert b["machine_state"] == "BLOCKED"
    assert len(calls) == 1
    assert engine.sandbox.read_state()["output_percent"] == 74


def test_reset_preserves_audit_and_never_reuses_identity(engine):
    old = execute(engine)
    audit = deepcopy(audit_rows(engine.store))
    engine.reset()
    new = engine.create_run("C2")
    assert new["run_id"] != old["run_id"]
    assert all(row in audit_rows(engine.store) for row in audit)


@pytest.mark.parametrize("missing", ["policy", "approval", "checkpoint", "execute", "verify"])
def test_missing_required_span_fails_trajectory(engine, missing):
    result = execute(engine)
    from app.evaluators import evaluate
    evidence = engine.traces[result["run_id"]].evidence
    evidence.events = [event for event in evidence.events if event.step != missing]
    assert next(e for e in evaluate(evidence) if e.name == "Trajectory order").label != "PASS"


@pytest.mark.parametrize("state", ["COMPLETED", "RESTORED", "LOCKED"])
def test_unauthorized_execution_fails_regardless_of_final_state(engine, state):
    result = execute(engine)
    from app.evaluators import evaluate
    evidence = engine.traces[result["run_id"]].evidence
    evidence.terminal_state = state
    next(event for event in evidence.events if event.step == "policy").data["state"] = "RED"
    assert evaluate(evidence)[0].label == "FAIL"


def test_unverified_recovery_without_verification_fails_evaluation(engine):
    result = execute(engine)
    result["verification"] = None
    result["rollback"] = {"verified": False}
    assert not next(e for e in engine._evaluate(result) if e["name"] == "Rollback verification")["pass"]


def test_rejected_intent_cannot_be_approved_after_persistence_failure(engine, monkeypatch):
    v = engine.create_run("C2")
    save = engine.store.save_approval
    monkeypatch.setattr(engine.store, "save_approval", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert engine.reject(v["run_id"])["machine_state"] == "BLOCKED"
    monkeypatch.setattr(engine.store, "save_approval", save)
    with pytest.raises(ForbiddenTransition):
        engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_audit_failure_cannot_persist_unaudited_terminal_state(engine, monkeypatch):
    original = engine.store.finalize_run
    def fail_terminal(run_id, data):
        if data["machine_state"] == "COMPLETED":
            raise OSError("audit unavailable")
        return original(run_id, data)
    monkeypatch.setattr(engine.store, "finalize_run", fail_terminal)
    result = execute(engine)
    assert result["machine_state"] == "LOCKED"
    assert stored_record(engine.store, "run", result["run_id"])["machine_state"] != "COMPLETED"


def test_placeholder_spans_do_not_pass_trajectory(engine, monkeypatch):
    monkeypatch.setattr(engine.sandbox, "adjust_output", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    result = execute(engine)
    assert result["machine_state"] == "RESTORED"
    assert not next(e for e in result["evaluators"] if e["name"] == "Trajectory order")["pass"]


def test_replay_uses_actual_prior_approval_after_clean_write(engine):
    completed = execute(engine)
    replay = engine.create_run("C4")
    assert replay["approved_hash"] == completed["approval"]["action_hash"]
    assert set(replay["block_reasons"]) == {"HASH_MISMATCH", "APPROVAL_ALREADY_USED"}


def test_reset_flushes_failed_finalization_before_discarding_run(engine, monkeypatch):
    finalize = engine.store.finalize_run
    monkeypatch.setattr(engine.store, "finalize_run", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    blocked = engine.create_run("C3")
    assert blocked["machine_state"] == "BLOCKED"
    assert blocked["run_id"] in engine.pending_finalizations
    with pytest.raises(OSError):
        engine.reset()
    assert blocked["run_id"] in engine.runs
    monkeypatch.setattr(engine.store, "finalize_run", finalize)
    engine.reset()
    assert any(row["event"] == "BLOCKED" for row in audit_rows(engine.store, blocked["run_id"]))
    assert not engine.pending_finalizations


def test_audit_lock_retains_control_unavailable_reason(engine, monkeypatch):
    finalize = engine.store.finalize_run
    monkeypatch.setattr(engine.store, "finalize_run", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert execute(engine)["machine_state"] == "LOCKED"
    monkeypatch.setattr(engine.store, "finalize_run", finalize)
    later = execute(engine)
    assert later["block_reasons"] == ["CONTROL_UNAVAILABLE"]


def test_executor_cannot_mutate_a_run_during_a_snapshot(engine, monkeypatch):
    import app.engine as module
    paused, resume = threading.Event(), threading.Event()
    snapshot_started, release_snapshot = threading.Event(), threading.Event()
    def pause_after_dispatch(_):
        if engine.output == 74:
            paused.set()
            assert resume.wait(2)
    monkeypatch.setattr(engine, "_pace", pause_after_dispatch)
    v = engine.create_run("C2")
    engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert paused.wait(2)
    original_copy = module.deepcopy
    def copy(value):
        if threading.current_thread().name == "snapshot-reader":
            snapshot_started.set()
            assert release_snapshot.wait(2)
        return original_copy(value)
    monkeypatch.setattr(module, "deepcopy", copy)
    reader = threading.Thread(target=lambda: engine.get(v["run_id"]), name="snapshot-reader")
    reader.start()
    try:
        assert snapshot_started.wait(2)
        before = original_copy(engine.runs[v["run_id"]])
        resume.set()
        time.sleep(0.05)
        assert engine.runs[v["run_id"]] == before
    finally:
        resume.set()
        release_snapshot.set()
        reader.join(2)
    assert settled(engine, v)["machine_state"] == "COMPLETED"


def test_reset_conflict_is_http_409(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    def conflict():
        raise ForbiddenTransition("sandbox not ready")
    with TestClient(main.app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(main._engine(), "reset", conflict)
        response = client.post("/api/reset")
    assert response.status_code == 409


def test_unknown_write_with_failed_rollback_refreshes_observed_output(engine, monkeypatch):
    adjust = engine.sandbox.adjust_output
    def lost_response(*args):
        adjust(*args)
        raise TimeoutError("response lost")
    monkeypatch.setattr(engine.sandbox, "adjust_output", lost_response)
    monkeypatch.setattr(engine.sandbox, "write_state", lambda *a: (_ for _ in ()).throw(OSError("rollback unavailable")))
    v = execute(engine)
    assert v["machine_state"] == "LOCKED"
    assert v["current_output_percent"] == engine.sandbox.read_state()["output_percent"] == 74


def test_finalized_run_persists_observed_output(engine):
    result = execute(engine)
    assert stored_record(engine.store, "run", result["run_id"])["current_output_percent"] == 74


def test_expiry_is_checked_after_execution_transition_persistence(engine, monkeypatch):
    v = engine.create_run("C2")
    save = engine.store.save_run
    def delayed(run_id, data):
        save(run_id, data)
        if data["machine_state"] == "EXECUTING":
            engine.runs[run_id]["approval"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(engine.store, "save_run", delayed)
    result = execute(engine, v)
    assert result["machine_state"] == "BLOCKED"
    assert result["execution"] is None
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_new_global_lock_is_checked_immediately_before_write(engine, monkeypatch):
    def lock_during_checkpoint(_):
        engine.write_lock_reason = "CONTROL_UNAVAILABLE"
    monkeypatch.setattr(engine, "_pace", lock_during_checkpoint)
    result = execute(engine)
    assert result["machine_state"] == "BLOCKED"
    assert result["execution"] is None


def test_stale_approval_reports_state_change(engine):
    stale = engine.create_run("C2")
    execute(engine)
    result = execute(engine, stale)
    assert result["block_reasons"] == ["STATE_CHANGED"]
    assert audit_rows(engine.store, result["run_id"])[-1]["detail"]["block_reasons"] == ["STATE_CHANGED"]


@pytest.mark.parametrize("failure", ["write_response", "first_read"])
def test_ambiguous_restore_uses_recovery_checksum(engine, monkeypatch, failure):
    engine.set_fault(True)
    write, read = engine.sandbox.write_state, engine.sandbox.read_state
    restoring = False
    def restore(state):
        nonlocal restoring
        restoring = True
        result = write(state)
        if failure == "write_response":
            raise TimeoutError("restore response lost")
        return result
    def read_back():
        nonlocal restoring
        if restoring and failure == "first_read":
            restoring = False
            raise TimeoutError("first read lost")
        return read()
    monkeypatch.setattr(engine.sandbox, "write_state", restore)
    monkeypatch.setattr(engine.sandbox, "read_state", read_back)
    result = execute(engine)
    assert result["machine_state"] == "RESTORED"
    assert result["rollback"]["verified"]
    assert result["rollback"]["restored_checksum"] == result["checkpoint"]["checksum"]
    assert not engine.writes_locked


def test_failed_audit_preserves_failed_rollback_cause(engine, monkeypatch):
    engine.set_fault(True)
    monkeypatch.setattr(engine.sandbox, "write_state", lambda *a: (_ for _ in ()).throw(OSError("restore offline")))
    monkeypatch.setattr(engine.store, "finalize_run", lambda *a: (_ for _ in ()).throw(OSError("audit offline")))
    result = execute(engine)
    assert engine.write_lock_reason == "ROLLBACK_FAILED"
    assert "ROLLBACK_FAILED" in result["block_reasons"]
    assert "CONTROL_UNAVAILABLE" in result["block_reasons"]
    assert "ROLLBACK_FAILED" in engine.pending_finalizations[result["run_id"]]["block_reasons"]


@pytest.mark.parametrize("failure", ["approval", "trace", "approval_and_audit"])
def test_rejection_finishes_despite_evidence_failure(engine, monkeypatch, failure):
    v = engine.create_run("C2")
    def offline(*args, **kwargs):
        raise OSError("offline")
    if failure.startswith("approval"):
        monkeypatch.setattr(engine.store, "save_approval", offline)
    if failure == "trace":
        monkeypatch.setattr(engine.traces[v["run_id"]], "step", offline)
    if failure == "approval_and_audit":
        monkeypatch.setattr(engine.store, "finalize_run", offline)
    result = engine.reject(v["run_id"])
    assert result["machine_state"] == "BLOCKED"
    assert "APPROVAL_REJECTED" in result["block_reasons"]
    with pytest.raises(ForbiddenTransition):
        engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    if failure == "approval_and_audit":
        assert v["run_id"] in engine.pending_finalizations
    else:
        saved = stored_record(engine.store, "run", v["run_id"])
        assert saved["machine_state"] == "BLOCKED"
        assert saved["approval"]["decision"] == "reject"


def test_reset_audit_failure_does_not_leave_stale_output(engine, monkeypatch):
    execute(engine)
    monkeypatch.setattr(engine.store, "audit", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError):
        engine.reset()
    assert engine.output == engine.sandbox.read_state()["output_percent"] == 72
    assert engine.writes_locked


def test_repeated_approval_does_not_narrate_old_target(engine):
    execute(engine)
    result = engine.create_run("C2")
    assert result["action"]["expected_postcondition"]["output_percent"] == 76
    assert result["voice_line"] is None


def test_expired_dispatch_uses_valid_abort_transition(engine, monkeypatch):
    from app.engine import ALLOWED
    save = engine.store.save_run
    states = []
    def expire_after_persist(run_id, data):
        states.append(data["machine_state"])
        save(run_id, data)
        if data["machine_state"] == "EXECUTING":
            engine.runs[run_id]["approval"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(engine.store, "save_run", expire_after_persist)
    result = execute(engine)
    assert result["machine_state"] == "BLOCKED"
    assert result["execution"] is None
    assert all(b in ALLOWED[a] for a, b in zip(states, states[1:]))
    assert next(s for s in result["trace_spans"] if s["name"] == "execute")["status"] == "skipped"


def test_audit_lock_cannot_interleave_with_dispatch(engine, monkeypatch):
    other = engine.create_run("C2")
    started, finished = threading.Event(), threading.Event()
    audit = engine.store.audit
    def fail_other(run_id, *args, **kwargs):
        if run_id == other["run_id"]:
            raise OSError("other request audit failed")
        return audit(run_id, *args, **kwargs)
    monkeypatch.setattr(engine.store, "audit", fail_other)
    def reject_other():
        started.set()
        engine.reject(other["run_id"])
        finished.set()
    worker = threading.Thread(target=reject_other)
    adjust = engine.sandbox.adjust_output
    interleaved = []
    def dispatch(*args):
        worker.start()
        assert started.wait(1)
        interleaved.append(finished.wait(0.05))
        return adjust(*args)
    monkeypatch.setattr(engine.sandbox, "adjust_output", dispatch)
    execute(engine)
    worker.join(2)
    assert finished.is_set()
    assert interleaved == [False]


def test_queued_action_cannot_change_parameters_under_cached_hash(engine):
    with engine.transaction_lock:
        v = engine.create_run("C2")
        engine.approve(v["run_id"], "op", v["action"]["action_hash"])
        with engine.lock:
            action = engine.runs[v["run_id"]]["action"]
            action["normalized_parameters"]["delta_percent"] = 3
            action["expected_postcondition"]["output_percent"] = 75
    result = settled(engine, v)
    assert result["machine_state"] == "BLOCKED"
    assert result["block_reasons"] == ["HASH_MISMATCH"]
    assert result["execution"] is None
    assert engine.sandbox.read_state()["output_percent"] == 72


@pytest.mark.parametrize("field,value", [
    ("normalized_parameters", {"delta_percent": 3}),
    ("expected_postcondition", {"output_percent": 75}),
    ("target", "another-reactor"),
    ("tool", "another-tool"),
])
def test_payload_rechecked_after_execution_persistence(engine, monkeypatch, field, value):
    v = engine.create_run("C2")
    save = engine.store.save_run

    def mutate(run_id, data):
        save(run_id, data)
        if data.get("machine_state") == "EXECUTING":
            engine.runs[run_id]["action"][field] = value

    monkeypatch.setattr(engine.store, "save_run", mutate)
    result = execute(engine, v)
    assert result["machine_state"] == "BLOCKED"
    assert result["block_reasons"] == ["HASH_MISMATCH"]
    assert result["execution"] is None
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_checkpoint_target_must_match_approved_action(engine):
    v = engine.create_run("C2")
    engine.sandbox.write_state({"target": "another-reactor", "output_percent": 72})
    result = execute(engine, v)
    assert result["machine_state"] == "BLOCKED"
    assert result["block_reasons"] == ["STATE_CHANGED"]
    assert result["execution"] is None
    assert engine.sandbox.read_state() == {"target": "another-reactor", "output_percent": 72}


def test_failed_checkpoint_transition_keeps_published_state(engine, monkeypatch):
    v = engine.create_run("C2")
    save = engine.store.save_run
    def fail_checkpoint(run_id, data):
        if data["machine_state"] == "CHECKPOINTING":
            raise OSError("state store offline")
        save(run_id, data)
    monkeypatch.setattr(engine.store, "save_run", fail_checkpoint)
    with pytest.raises(OSError):
        engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert engine.get(v["run_id"])["machine_state"] == "AWAITING_APPROVAL"
    assert stored_record(engine.store, "run", v["run_id"])["machine_state"] == "AWAITING_APPROVAL"
    assert engine.reject(v["run_id"])["machine_state"] == "BLOCKED"


def test_reset_accepts_verified_state_after_lost_write_response(engine, monkeypatch):
    execute(engine)
    write = engine.sandbox.write_state
    def reset_lost_response(state):
        write(state)
        raise TimeoutError("reset response lost")
    monkeypatch.setattr(engine.sandbox, "write_state", reset_lost_response)
    engine.reset()
    assert engine.output == engine.sandbox.read_state()["output_percent"] == 72
    assert not engine.writes_locked
    assert audit_rows(engine.store, "reset")[-1]["event"] == "RESET"


def test_failed_reset_refreshes_observed_state_and_keeps_lock(engine, monkeypatch):
    execute(engine)
    engine.sandbox.write_state({**engine._initial_state(), "output_percent": 75})
    monkeypatch.setattr(engine.sandbox, "write_state", lambda *a: (_ for _ in ()).throw(TimeoutError("not written")))
    with pytest.raises(ForbiddenTransition):
        engine.reset()
    assert engine.output == 75
    assert engine.writes_locked


def test_new_request_uses_verified_state_during_fault_recovery(engine, monkeypatch):
    engine.set_fault(True)
    entered, release = threading.Event(), threading.Event()
    def pause_after_write(_):
        if engine.output == 92:
            entered.set()
            assert release.wait(2)
    monkeypatch.setattr(engine, "_pace", pause_after_write)
    first = engine.create_run("C2")
    engine.approve(first["run_id"], "op", first["action"]["action_hash"])
    try:
        assert entered.wait(2)
        second = engine.create_run("C2")
        assert second["action"]["expected_postcondition"]["output_percent"] == 74
        assert second["decision"]["state"] == "YELLOW"
    finally:
        release.set()
    assert settled(engine, first)["machine_state"] == "RESTORED"
    engine.set_fault(False)
    assert execute(engine, second)["machine_state"] == "COMPLETED"


@pytest.mark.parametrize("failure", ["state_changed", "read_failed", "locked"])
def test_aborted_checkpoint_never_remains_running(engine, monkeypatch, failure):
    run = engine.create_run("C2")
    if failure == "state_changed":
        execute(engine)
    elif failure == "read_failed":
        monkeypatch.setattr(engine.sandbox, "read_state", lambda: (_ for _ in ()).throw(OSError("offline")))
    else:
        def lock_after_approval(_):
            engine.write_lock_reason = "CONTROL_UNAVAILABLE"
        monkeypatch.setattr(engine.store, "save_approval", lock_after_approval)
    result = execute(engine, run)
    assert result["machine_state"] == "BLOCKED"
    for snapshot in [result, stored_record(engine.store, "run", run["run_id"])]:
        assert not any(s["status"] == "running" for s in snapshot["trace_spans"])

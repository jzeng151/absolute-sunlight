"""Regressions for transaction, recovery, and audit review findings."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from app import config
from app.engine import Engine, ForbiddenTransition


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    e.set_fault(False)
    return e


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
    assert any(a["event"] == "RESTORED" for a in engine.store.audit_log)


def test_terminal_state_waits_for_audit(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = engine.store.audit
    def delayed(run_id, event, detail=None):
        if event == "COMPLETED":
            entered.set()
            assert release.wait(2)
        return original(run_id, event, detail)
    monkeypatch.setattr(engine.store, "audit", delayed)
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
    engine.approve(second["run_id"], "op", second["action"]["action_hash"])
    release.set()
    a, b = settled(engine, first), settled(engine, second)
    assert a["machine_state"] == "COMPLETED"
    assert b["machine_state"] == "BLOCKED"
    assert len(calls) == 1
    assert engine.sandbox.read_state()["output_percent"] == 74


def test_reset_preserves_audit_and_never_reuses_identity(engine):
    old = execute(engine)
    audit = deepcopy(engine.store.audit_log)
    engine.reset()
    new = engine.create_run("C2")
    assert new["run_id"] != old["run_id"]
    assert all(row in engine.store.audit_log for row in audit)


@pytest.mark.parametrize("missing", ["policy", "approval", "checkpoint", "execute", "verify"])
def test_missing_required_span_fails_trajectory(engine, missing):
    result = execute(engine)
    result["trace_spans"] = [s for s in result["trace_spans"] if s["name"].split(" ")[0] != missing]
    assert not next(e for e in engine._evaluate(result) if e["name"] == "Trajectory order")["pass"]


@pytest.mark.parametrize("state", ["COMPLETED", "RESTORED", "LOCKED"])
def test_unauthorized_execution_fails_regardless_of_final_state(engine, state):
    result = execute(engine)
    result["machine_state"] = state
    result["decision"]["state"] = "RED"
    assert not engine._evaluate(result)[0]["pass"]


def test_unverified_recovery_without_verification_fails_evaluation(engine):
    result = execute(engine)
    result["verification"] = None
    result["rollback"] = {"verified": False}
    assert not next(e for e in engine._evaluate(result) if e["name"] == "Rollback verification")["pass"]


def test_rejected_intent_cannot_be_approved_after_persistence_failure(engine, monkeypatch):
    v = engine.create_run("C2")
    save = engine.store.save_approval
    monkeypatch.setattr(engine.store, "save_approval", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError):
        engine.reject(v["run_id"])
    monkeypatch.setattr(engine.store, "save_approval", save)
    with pytest.raises(ForbiddenTransition):
        engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert engine.sandbox.read_state()["output_percent"] == 72


def test_audit_failure_cannot_persist_unaudited_terminal_state(engine, monkeypatch):
    original = engine.store.audit
    def fail_terminal(run_id, event, detail=None):
        if event == "COMPLETED":
            raise OSError("audit unavailable")
        return original(run_id, event, detail)
    monkeypatch.setattr(engine.store, "audit", fail_terminal)
    result = execute(engine)
    assert result["machine_state"] == "LOCKED"
    assert engine.store.runs[result["run_id"]]["machine_state"] != "COMPLETED"


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
    audit = engine.store.audit
    monkeypatch.setattr(engine.store, "audit", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    blocked = execute(engine)
    assert blocked["machine_state"] == "BLOCKED"
    with pytest.raises(OSError):
        engine.reset()
    assert blocked["run_id"] in engine.runs
    monkeypatch.setattr(engine.store, "audit", audit)
    engine.reset()
    assert any(row["run"] == blocked["run_id"] and row["event"] == "BLOCKED" for row in engine.store.audit_log)
    assert not engine.pending_finalizations

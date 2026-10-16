"""Regressions for slow control operations, startup, and ASGI shutdown."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep

import pytest

from app.adapters import trace
from app.adapters.sandbox import LocalSandbox
from app.engine import Engine, ForbiddenTransition
from test_evaluations import approve, data, engine, labels


def test_rejected_request_keeps_observed_time_across_slow_storage(engine, monkeypatch):
    v = engine.create_run("C2")
    expires = datetime.now(timezone.utc) + timedelta(seconds=0.2)
    engine.runs[v["run_id"]]["approval_expires_at"] = expires.isoformat()
    save_run = engine.store.save_run
    def save(run_id, row):
        if row["machine_state"] == "BLOCKED":
            sleep(max(0, (expires - datetime.now(timezone.utc)).total_seconds()) + 0.01)
        return save_run(run_id, row)
    monkeypatch.setattr(engine.store, "save_run", save)
    result = engine.approve(v["run_id"], "op", "wrong-hash")
    observed = engine.traces[v["run_id"]].evidence
    assert result["block_reasons"] == ["HASH_MISMATCH"]
    assert datetime.fromisoformat(data(observed, "approval.check")["checked_at"]) < expires
    assert labels(observed)["Approval integrity"] == "PASS"


@pytest.mark.parametrize("slow_operation", ["approval_store", "state_store", "trace"])
def test_expiry_gate_follows_slow_io_and_precedes_executor(engine, monkeypatch, slow_operation):
    v = engine.create_run("C2")
    expires = datetime.now(timezone.utc) + timedelta(seconds=0.2)
    engine.runs[v["run_id"]]["approval_expires_at"] = expires.isoformat()
    run_trace = engine.traces[v["run_id"]]
    calls = []
    adjust = engine.sandbox.adjust_output
    def execute(*args):
        calls.append(datetime.now(timezone.utc))
        return adjust(*args)
    monkeypatch.setattr(engine.sandbox, "adjust_output", execute)
    def wait_until_expired():
        sleep(max(0, (expires - datetime.now(timezone.utc)).total_seconds()) + 0.01)
    if slow_operation == "approval_store":
        original = engine.store.save_approval
        def save(approval):
            if approval["used"]:
                wait_until_expired()
            return original(approval)
        monkeypatch.setattr(engine.store, "save_approval", save)
    elif slow_operation == "state_store":
        original = engine.store.save_run
        def save(run_id, row):
            if row["machine_state"] == "EXECUTING":
                wait_until_expired()
            return original(run_id, row)
        monkeypatch.setattr(engine.store, "save_run", save)
    else:
        original = run_trace.step
        def record(name, *args, **kwargs):
            if name == "approval.check":
                wait_until_expired()
            return original(name, *args, **kwargs)
        monkeypatch.setattr(run_trace, "step", record)
    result = approve(engine, v)
    if slow_operation == "trace":
        assert len(calls) == 1 and calls[0] < expires
        assert result["machine_state"] == "RESTORED"
        assert set(labels(run_trace.evidence).values()) == {"PASS"}
    else:
        assert calls == [] and run_trace.evidence.executor_call_count == 0
        assert result["machine_state"] == "BLOCKED"
        assert result["block_reasons"] == ["APPROVAL_EXPIRED"]
        check = data(run_trace.evidence, "approval.check")
        assert not check["valid"] and not check["consumed"]
        assert not engine.store.approvals[result["approval"]["approval_id"]]["used"]


@pytest.mark.parametrize("phase", ["checkpoint", "execute", "verify", "rollback"])
def test_lifespan_drains_active_run_before_closing_telemetry(engine, monkeypatch, phase):
    import app.main as main

    entered, release, exporter_closed = Event(), Event(), Event()
    pending = engine.create_run("C2")
    v = engine.create_run("C2")
    run_trace = engine.traces[v["run_id"]]
    if phase == "checkpoint":
        owner, name = engine.store, "save_checkpoint"
    elif phase == "execute":
        owner, name = engine.sandbox, "adjust_output"
    elif phase == "verify":
        owner, name = engine.sandbox, "read_state"
    else:
        owner, name = engine.sandbox, "write_state"
    original = getattr(owner, name)
    calls = 0
    def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        if phase != "verify" or calls == 2:
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, name, paused)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(trace, "shutdown", exporter_closed.set)
    engine.approve(v["run_id"], "op", v["action"]["action_hash"])
    assert entered.wait(3)
    async def shutdown():
        async with main.lifespan(main.app):
            pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(asyncio.run, shutdown())
        try:
            deadline = monotonic() + 2
            while not engine.closed and monotonic() < deadline:
                sleep(0.005)
            assert engine.closed
            assert not run_trace.closed and not exporter_closed.is_set() and not closing.done()
            with pytest.raises(ForbiddenTransition):
                engine.create_run("C1")
            with pytest.raises(ForbiddenTransition):
                engine.approve(pending["run_id"], "op", pending["action"]["action_hash"])
        finally:
            release.set()
        closing.result(timeout=3)
    assert exporter_closed.is_set() and run_trace.closed
    assert run_trace.evidence.terminal_state == "RESTORED"
    assert set(labels(run_trace.evidence).values()) == {"PASS"}
    assert [e.step for e in run_trace.evidence.events][-3:] == ["verify", "rollback", "audit"]
    assert not engine._workers
    assert {e["label"] for e in engine.traces[pending["run_id"]].results} == {"INCOMPLETE"}


def test_green_observation_waits_for_sandbox_initialization(monkeypatch):
    import app.engine as module

    initializing, release, read_called, request_started = Event(), Event(), Event(), Event()
    class SlowSandbox(LocalSandbox):
        def write_state(self, state):
            initializing.set()
            assert release.wait(3)
            return super().write_state(state)
        def read_state(self):
            read_called.set()
            return super().read_state()
    monkeypatch.setattr(module, "make_sandbox", SlowSandbox)
    instance = Engine()
    assert initializing.wait(3)
    def create():
        request_started.set()
        return instance.create_run("C1")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(create)
            try:
                assert request_started.wait(3)
                assert not read_called.wait(0.05)
            finally:
                release.set()
            result = request.result(timeout=3)
        observed = instance.traces[result["run_id"]].evidence
        assert data(observed, "read")["state_before"]["output_percent"] == 72
        assert labels(observed)["Trajectory order"] == "PASS"
    finally:
        release.set()
        instance.close()


def test_unready_sandbox_has_no_fabricated_read_evidence(engine, monkeypatch):
    monkeypatch.setattr(engine.sandbox_ready, "wait", lambda timeout: False)
    monkeypatch.setattr(engine.sandbox, "read_state", lambda: pytest.fail("Read before readiness"))
    v = engine.create_run("C1")
    observed = engine.traces[v["run_id"]].evidence
    assert data(observed, "read")["error_type"] == "TimeoutError"
    assert labels(observed)["Trajectory order"] == "INCOMPLETE"

"""Exercise the actual sandbox program and engine without creating cloud resources."""
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import daytona
import pytest

from app import config, engine as engine_module
from app.adapters import sandbox

INITIAL = {"target": "reactor_sim_01", "output_percent": 72}


@pytest.fixture
def sdk(monkeypatch, tmp_path):
    """Only replace the SDK's transport. Run the exact submitted code in Python."""
    class Client:
        def __init__(self):
            self.created = []
            self.deleted = []
            self.calls = []
            self.box = SimpleNamespace(id="daytona-full-sandbox-id-123456789", process=self)

        def create(self, params, *, timeout):
            self.created.append((params, timeout))
            return self.box

        def code_run(self, code, *, timeout):
            self.calls.append(code)
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
            return SimpleNamespace(exit_code=result.returncode, result=result.stdout)

        def delete(self, box):
            self.deleted.append(box.id)

    client = Client()
    monkeypatch.setattr(daytona, "Daytona", lambda config: client)
    monkeypatch.setattr(config, "DAYTONA_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "DAYTONA_SNAPSHOT", "")
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "daytona")
    monkeypatch.setattr(sandbox, "STATE_PATH", str(tmp_path / "reactor" / "state.json"))
    return client


def test_remote_state_clean_fault_restore_and_cleanup(sdk):
    box = sandbox.DaytonaSandbox()
    assert not sdk.created
    assert box.write_state(INITIAL) == INITIAL
    assert box.adjust_output(2, False)["output_percent"] == 74
    box.write_state(INITIAL)
    assert box.adjust_output(2, True)["output_percent"] == 92
    box.write_state(INITIAL)
    assert box.read_state() == INITIAL
    assert box.run_id == sdk.box.id
    params, timeout = sdk.created[0]
    assert len(sdk.created) == 1 and timeout == 90
    assert params.network_block_all and not params.public
    assert params.auto_stop_interval == 0 and params.auto_delete_interval == 0
    assert params.env_vars is None
    assert all("test-only-key" not in code for code in sdk.calls)
    box.close()
    box.close()
    assert sdk.deleted == [sdk.box.id]
    with pytest.raises(RuntimeError, match="closed"):
        box.read_state()


@pytest.mark.parametrize("stdout", [
    "", "SCRAM_STATE=not json", "SCRAM_STATE=[]",
    'SCRAM_STATE={"target":"reactor_sim_01","output_percent":true}',
    'SCRAM_STATE={"target":"reactor_sim_01","output_percent":NaN}',
    'SCRAM_STATE={"output_percent":72}',
    'SCRAM_STATE={}\nSCRAM_STATE={}',
    'SCRAM_STATE={"target":"reactor_sim_01","output_percent":72}\nSCRAM_STATE={"target":"reactor_sim_01","output_percent":92}',
    'SCRAM_STATE={"target":"","output_percent":72}',
    'SCRAM_STATE={"target":"reactor_sim_01","output_percent":Infinity}',
])
def test_rejects_invalid_remote_state(stdout):
    with pytest.raises(RuntimeError):
        sandbox._parse(stdout)


@pytest.mark.parametrize("exit_code", [1, None, False, "0"])
def test_requires_successful_exit(sdk, monkeypatch, exit_code):
    monkeypatch.setattr(sdk, "code_run", lambda *args, **kw: SimpleNamespace(
        exit_code=exit_code, result='SCRAM_STATE={"target":"reactor_sim_01","output_percent":72}',
    ))
    with pytest.raises(RuntimeError, match="exit successfully"):
        sandbox.DaytonaSandbox().read_state()


def test_explicit_modes_and_missing_key(monkeypatch):
    monkeypatch.setattr(config, "DAYTONA_API_KEY", "")
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "auto")
    assert sandbox.make_sandbox().fallback_reason == "DAYTONA_KEY_MISSING"
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "local")
    assert sandbox.make_sandbox().fallback_reason is None
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "daytona")
    with pytest.raises(RuntimeError, match="DAYTONA_API_KEY"):
        sandbox.make_sandbox()


def test_client_failure_fallback_does_not_log_credentials(sdk, monkeypatch, caplog):
    def fail(config):
        raise RuntimeError("secret-test-key")

    monkeypatch.setattr(daytona, "Daytona", fail)
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "auto")
    assert sandbox.make_sandbox().fallback_reason == "DAYTONA_CLIENT_UNAVAILABLE"
    assert "secret-test-key" not in caplog.text
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "daytona")
    with pytest.raises(RuntimeError, match="initialization failed"):
        sandbox.make_sandbox()


@pytest.fixture
def reactor(sdk, monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    instance = engine_module.Engine()
    assert instance.sandbox_ready.wait(5)
    assert instance.sandbox_error is None
    yield instance
    instance.close()


def execute(reactor, monkeypatch, case="C2"):
    done = threading.Event()
    original = reactor._execute

    def run(*args):
        try:
            original(*args)
        finally:
            done.set()

    with monkeypatch.context() as scoped:
        scoped.setattr(reactor, "_execute", run)
        view = reactor.create_run(case)
        approved = reactor.approve(view["run_id"], "operator", view["action"]["action_hash"])
        if approved["machine_state"] == "BLOCKED":
            return approved
        assert done.wait(5), "execution did not finish"
    return reactor.get(view["run_id"])


@pytest.mark.parametrize("fault,terminal,output", [(False, "COMPLETED", 74), (True, "RESTORED", 72)])
def test_daytona_engine_clean_and_fault_paths(reactor, sdk, monkeypatch, fault, terminal, output):
    reactor.set_fault(fault)
    view = execute(reactor, monkeypatch)
    assert view["machine_state"] == terminal
    assert reactor.sandbox.read_state()["output_percent"] == output
    assert view["execution"]["executor_run_id"] == f"sbx_{sdk.box.id}"
    if fault:
        assert view["verification"]["actual_state"]["output_percent"] == 92
        assert view["rollback"]["verified"]
        assert view["checkpoint"]["checksum"] == view["rollback"]["restored_checksum"]
    assert all(item["pass"] for item in view["evaluators"])


def test_blocked_and_invalid_approval_never_dispatch_adjustment(reactor, sdk):
    red = reactor.create_run("C3")
    replay = reactor.create_run("C4")
    yellow = reactor.create_run("C2")
    invalid = reactor.approve(yellow["run_id"], "operator", "wrong-hash")
    assert all(view["machine_state"] == "BLOCKED" for view in (red, replay, invalid))
    assert len(sdk.calls) == 1  # Startup state write only.


def test_response_lost_after_write_recovers_without_replaying(reactor, sdk, monkeypatch):
    original = sdk.code_run
    adjustments = []

    def lose_response(code, **kwargs):
        response = original(code, **kwargs)
        if "op = 'adjust'" in code:
            adjustments.append(code)
            raise TimeoutError("remote write completed; response lost")
        return response

    monkeypatch.setattr(sdk, "code_run", lose_response)
    view = execute(reactor, monkeypatch)
    assert view["machine_state"] == "RESTORED"
    assert view["rollback"]["verified"]
    assert reactor.sandbox.read_state() == INITIAL
    assert len(adjustments) == 1
    assert reactor.sandbox.name == "daytona"


@pytest.mark.parametrize("lost_response", [False, True])
def test_failed_remote_rollback_locks_writes_until_verified_reset(reactor, sdk, monkeypatch, lost_response):
    original = sdk.code_run
    other = reactor.create_run("C2")

    def fail_restore(code, **kwargs):
        if "op = 'write'" in code:
            raise TimeoutError("restore unavailable")
        result = original(code, **kwargs)
        if lost_response and "op = 'adjust'" in code:
            raise TimeoutError("response lost")
        return result

    monkeypatch.setattr(sdk, "code_run", fail_restore)
    view = execute(reactor, monkeypatch)
    assert view["machine_state"] == "LOCKED" and not view["rollback"]["verified"]
    assert not next(e for e in view["evaluators"] if e["name"] == "Rollback verification")["pass"]
    blocked = reactor.approve(other["run_id"], "operator", other["action"]["action_hash"])
    assert blocked["machine_state"] == "BLOCKED"
    assert blocked["block_reasons"] == ["ROLLBACK_FAILED"]
    with pytest.raises(RuntimeError, match="Daytona request failed"):
        reactor.reset()
    assert reactor.writes_locked
    monkeypatch.setattr(sdk, "code_run", original)
    reactor.reset()
    assert not reactor.writes_locked and reactor.sandbox.read_state() == INITIAL


@pytest.mark.parametrize("change,reason", [("expired", "APPROVAL_EXPIRED"), ("mutated", "HASH_MISMATCH")])
def test_rechecks_authorization_after_remote_checkpoint(reactor, sdk, monkeypatch, change, reason):
    read = reactor.sandbox.read_state

    def checkpoint():
        run = next(iter(reactor.runs.values()))
        if change == "expired":
            run["approval"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        else:
            # Retain the old cached action_hash, as a caller tampering with params might.
            run["action"] = {**run["action"], "normalized_parameters": {"delta_percent": 3}}
        return read()

    monkeypatch.setattr(reactor.sandbox, "read_state", checkpoint)
    view = execute(reactor, monkeypatch)
    assert view["machine_state"] == "BLOCKED" and view["block_reasons"] == [reason]
    assert not any("op = 'adjust'" in code for code in sdk.calls)


@pytest.mark.parametrize("mode", ["auto", "daytona"])
def test_startup_failure_cleanup_and_strict_mode(sdk, monkeypatch, mode):
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", mode)
    monkeypatch.setattr(config, "STEP_SECONDS", 0)

    def fail(*args, **kwargs):
        raise TimeoutError("initialization failed")

    monkeypatch.setattr(sdk, "code_run", fail)
    instance = engine_module.Engine()
    assert instance.sandbox_ready.wait(5)
    try:
        assert sdk.deleted == [sdk.box.id]
        health = instance.health()
        if mode == "auto":
            assert health["daytona"] == "local" and health["sandbox_ready"]
            assert health["sandbox_fallback_reason"] == "DAYTONA_INITIALIZATION_FAILED"
        else:
            assert health["daytona"] == "daytona" and not health["sandbox_ready"]
            assert health["sandbox_error"] == "DAYTONA_INITIALIZATION_FAILED"
            assert execute(instance, monkeypatch)["machine_state"] == "BLOCKED"
    finally:
        instance.close()


def test_shutdown_deletes_sandbox(reactor, sdk):
    reactor.close()
    reactor.close()
    assert sdk.deleted == [sdk.box.id]
    assert not reactor.health()["sandbox_ready"]


def test_reset_waits_for_remote_execution_and_recovery(reactor, sdk, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    reset_started = threading.Event()
    reset_done = threading.Event()
    original = sdk.code_run
    errors = []

    def delayed_adjustment(code, **kwargs):
        if "op = 'adjust'" in code:
            entered.set()
            assert release.wait(5)
        return original(code, **kwargs)

    def reset():
        reset_started.set()
        try:
            reactor.reset()
        except Exception as exc:
            errors.append(exc)
        finally:
            reset_done.set()

    monkeypatch.setattr(sdk, "code_run", delayed_adjustment)
    view = reactor.create_run("C2")
    reactor.approve(view["run_id"], "operator", view["action"]["action_hash"])
    assert entered.wait(5)
    reset_thread = threading.Thread(target=reset)
    reset_thread.start()
    try:
        assert reset_started.wait(5)
        assert not reset_done.wait(0.05)
    finally:
        release.set()
        reset_thread.join(timeout=5)
    assert reset_done.is_set() and not errors
    assert not reactor.runs
    assert reactor.sandbox.read_state() == INITIAL


def test_app_lifespan_cleans_up_executor(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    calls = []
    monkeypatch.setattr(main.engine, "close", lambda: calls.append("closed"))
    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
    assert calls == ["closed"]


def test_live_smoke_program_and_cleanup(sdk):
    from scripts.daytona_smoke import run_smoke

    result = run_smoke()
    assert result["verified"]
    assert (result["clean_output"], result["fault_output"], result["restored_output"]) == (74, 92, 72)
    assert result["checkpoint_checksum"] == result["restored_checksum"]
    assert sdk.deleted == [sdk.box.id]


def test_missing_exit_code_is_not_success(sdk, monkeypatch):
    monkeypatch.setattr(sdk, "code_run", lambda *a, **kw: SimpleNamespace(
        result='SCRAM_STATE={"target":"reactor_sim_01","output_percent":72}',
    ))
    with pytest.raises(RuntimeError, match="exit successfully"):
        sandbox.DaytonaSandbox().read_state()


def test_failed_atomic_replacement_preserves_checkpoint(sdk, monkeypatch):
    box = sandbox.DaytonaSandbox()
    box.write_state(INITIAL)
    code_run = sdk.code_run

    def interrupt_replace(code, **kwargs):
        # Interrupt the actual generated program after the temp file is written.
        prefix = "import os\ndef fail_replace(*args): raise OSError('disk unavailable')\nos.replace = fail_replace\n"
        return code_run(prefix + code, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(sdk, "code_run", interrupt_replace)
        with pytest.raises(RuntimeError):
            box.adjust_output(2, False)
    assert box.read_state() == INITIAL
    box.close()


def test_sdk_errors_and_output_are_not_logged(reactor, sdk, monkeypatch, caplog):
    code_run = sdk.code_run

    def fail_adjust(code, **kwargs):
        if "op = 'adjust'" in code:
            raise RuntimeError("secret-sdk-header")
        return code_run(code, **kwargs)

    monkeypatch.setattr(sdk, "code_run", fail_adjust)
    assert execute(reactor, monkeypatch)["machine_state"] == "RESTORED"
    assert "secret-sdk-header" not in caplog.text
    with pytest.raises(RuntimeError) as error:
        sandbox._parse("secret-remote-output")
    assert "secret-remote-output" not in str(error.value)


def test_explicit_local_mode_ignores_configured_credentials(sdk, monkeypatch):
    monkeypatch.setattr(config, "SCRAM_EXECUTOR", "local")
    assert sandbox.make_sandbox().name == "local"
    assert not sdk.created


def test_snapshot_selection(sdk, monkeypatch):
    monkeypatch.setattr(config, "DAYTONA_SNAPSHOT", "scram-python")
    box = sandbox.DaytonaSandbox()
    box.write_state(INITIAL)
    assert sdk.created[0][0].snapshot == "scram-python"
    box.close()


def test_smoke_failure_still_deletes_sandbox(sdk, monkeypatch):
    from scripts.daytona_smoke import run_smoke

    def fail(*args, **kwargs):
        raise TimeoutError("remote unavailable")

    monkeypatch.setattr(sdk, "code_run", fail)
    with pytest.raises(RuntimeError, match="Daytona request failed"):
        run_smoke()
    assert sdk.deleted == [sdk.box.id]


def test_transport_exception_traceback_does_not_expose_credentials(sdk, monkeypatch):
    import traceback

    def fail(*args, **kwargs):
        raise TimeoutError("secret-sdk-header")

    monkeypatch.setattr(sdk, "code_run", fail)
    box = sandbox.DaytonaSandbox()
    try:
        with pytest.raises(RuntimeError, match="Daytona request failed") as error:
            box.write_state(INITIAL)
        assert "secret-sdk-header" not in "".join(traceback.format_exception(error.value))
    finally:
        box.close()

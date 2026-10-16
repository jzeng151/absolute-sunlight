"""Lifecycle tests use a fake client and make no Daytona requests."""
import threading
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config
from app.adapters.sandbox import DaytonaSandbox, LocalSandbox
from app.engine import Engine


def test_import_does_not_construct_engine_or_remote_sandbox():
    code = '''
from unittest.mock import Mock
import app.engine
constructor = Mock(side_effect=AssertionError("Engine created during import"))
app.engine.Engine = constructor
import app.main
constructor.assert_not_called()
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_daytona_disables_idle_stop_and_closes_client(monkeypatch):
    import daytona
    client = Mock()
    client.create.return_value = SimpleNamespace(id="test-sandbox", process=Mock())
    monkeypatch.setattr(daytona, "Daytona", Mock(return_value=client))
    box = DaytonaSandbox()
    sandbox = box._box()
    assert client.create.call_args.args[0].auto_stop_interval == 0
    box.close()
    box.close()
    client.delete.assert_called_once_with(sandbox)
    client.close.assert_called_once()


def test_close_retries_transient_delete_before_releasing_client(monkeypatch):
    import daytona
    client = Mock()
    client.create.return_value = SimpleNamespace(id="test-sandbox", process=Mock())
    client.delete.side_effect = [TimeoutError("temporary outage"), None]
    monkeypatch.setattr(daytona, "Daytona", Mock(return_value=client))
    box = DaytonaSandbox()
    box._box()
    box.close()
    assert client.delete.call_count == 2
    client.close.assert_called_once()
    assert box._closed


def test_failed_cleanup_can_be_retried_without_resuming_operations(monkeypatch):
    import daytona
    client = Mock()
    client.create.return_value = SimpleNamespace(id="test-sandbox", process=Mock())
    client.delete.side_effect = TimeoutError("offline")
    monkeypatch.setattr(daytona, "Daytona", Mock(return_value=client))
    box = DaytonaSandbox()
    box._box()
    with pytest.raises(RuntimeError, match="cleanup"):
        box.close()
    assert not box._closed
    client.close.assert_not_called()
    with pytest.raises(RuntimeError, match="clos"):
        box._box()
    client.delete.side_effect = None
    box.close()
    client.close.assert_called_once()
    assert box._closed


def test_sdk_without_public_close_releases_transports():
    box = DaytonaSandbox.__new__(DaytonaSandbox)
    box._lock = threading.Lock()
    box._closed = False
    box._sandbox = None
    pools = [Mock(), Mock(), Mock()]
    events = SimpleNamespace(disconnect=Mock())
    box._client = SimpleNamespace(_api_client=pools[0], _toolbox_api_client=pools[1],
                                  _http_client=pools[2], _event_dispatcher=events)
    box.close()
    for pool in pools:
        pool.close.assert_called_once()
    events.disconnect.assert_called_once()


def test_health_waits_for_successful_initialization(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class SlowSandbox(LocalSandbox):
        name = "daytona"
        def write_state(self, state):
            entered.set()
            assert release.wait(2)
            return super().write_state(state)
    monkeypatch.setattr("app.engine.make_sandbox", SlowSandbox)
    e = Engine()
    try:
        assert entered.wait(2)
        assert e.health()["daytona"] == "initializing"
    finally:
        release.set()
    assert e.sandbox_ready.wait(2)
    assert e.health()["daytona"] == "daytona"


def test_shutdown_closes_the_engine(monkeypatch):
    from app import main
    with TestClient(main.app):
        current = main._engine()
        close = Mock(wraps=current.close)
        monkeypatch.setattr(current, "close", close)
    close.assert_called_once()
    assert current.closed
    assert main.engine is None


def test_repeated_lifespans_create_a_working_engine(monkeypatch):
    import app.main as main
    instances = []
    for _ in range(2):
        with TestClient(main.app) as client:
            instances.append(main._engine())
            response = client.post("/api/runs", json={"case_id": "C1"})
            assert response.status_code == 200
            assert response.json()["machine_state"] == "COMPLETED"
    assert instances[0] is not instances[1]
    assert all(instance.closed for instance in instances)


def test_requests_outside_lifespan_do_not_start_engine(monkeypatch):
    import app.main as main
    constructor = Mock(side_effect=AssertionError("unexpected engine"))
    monkeypatch.setattr(main, "Engine", constructor)
    response = TestClient(main.app).get("/api/health")
    assert response.status_code == 503
    constructor.assert_not_called()


def test_failed_initialization_cleans_up_remote_sandbox(monkeypatch):
    sandbox = Mock(name="daytona")
    sandbox.write_state.side_effect = RuntimeError("remote init failed")
    monkeypatch.setattr("app.engine.make_sandbox", lambda: sandbox)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    sandbox.close.assert_called_once()
    assert e.health()["daytona"] == "local"


def test_readiness_timeout_never_touches_sandbox(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    monkeypatch.setattr(e.sandbox_ready, "wait", lambda timeout: False)
    read = Mock(side_effect=AssertionError("sandbox is not ready"))
    monkeypatch.setattr(e.sandbox, "read_state", read)
    v = e.create_run("C2")
    e.approve(v["run_id"], "op", v["action"]["action_hash"])
    from test_safety import settled
    assert settled(e, v)["machine_state"] == "BLOCKED"
    read.assert_not_called()

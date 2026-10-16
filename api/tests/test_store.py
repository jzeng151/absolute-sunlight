"""SurrealDB store fixes from the Codex review of #12: no injection, no field loss, append-only history."""
import time

import pytest

from fastapi.testclient import TestClient

import app.config as config

config.STEP_SECONDS = 0.01
from app import main  # noqa: E402

client: TestClient
engine = None

@pytest.fixture(autouse=True)
def live_app():
    global client, engine
    with TestClient(main.app) as active:
        client = active
        engine = main._engine()
        yield


def settle(run_id):
    end = time.time() + 10
    while time.time() < end:
        v = client.get(f"/api/runs/{run_id}").json()
        if v["machine_state"] not in {"CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK"}:
            return v
        time.sleep(0.05)
    raise AssertionError("run did not settle")


def stored(table):
    return engine.store.db.query(f"SELECT * FROM {table}")


def test_run_id_in_rules_route_cannot_inject_surrealql():
    client.post("/api/reset")
    client.post("/api/runs", json={"case_id": "C3"})
    before = len(stored("run"))
    r = client.get("/api/runs/run_0418; DELETE run/rules")
    assert r.status_code in (200, 404)
    assert len(stored("run")) == before >= 1


def test_state_changes_keep_the_authorization_fields():
    client.post("/api/reset")
    client.post("/api/fault", json={"enabled": False})
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]})
    settle(v["run_id"])
    row = engine.store.db.select(engine.store.rid("run", v["run_id"]))
    if isinstance(row, list):
        row = row[0]
    assert row["machine_state"] == "COMPLETED"
    assert row["action_hash"] == v["action"]["action_hash"] and row["policy"] == "YELLOW"


def test_reset_keeps_the_audit_log():
    client.post("/api/runs", json={"case_id": "C1"})
    audits = len(stored("audit"))
    client.post("/api/reset")
    assert len(stored("audit")) >= audits >= 1

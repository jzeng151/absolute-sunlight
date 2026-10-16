"""Runs with no sponsor keys: local sandbox, embedded SurrealDB, no-op tracer."""
import time

import pytest
from fastapi.testclient import TestClient

import app.config as config

config.STEP_SECONDS = 0.01
from app import core  # noqa: E402
from app.main import app, engine  # noqa: E402

client = TestClient(app)


def wait_settled(run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/runs/{run_id}").json()
        if v["machine_state"] not in {"CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK"}:
            return v
        time.sleep(0.05)
    raise AssertionError("run did not settle")


@pytest.fixture(autouse=True)
def fresh():
    client.post("/api/reset")
    yield


def test_hash_matches_front_end_format():
    h = core.action_hash("adjust_output", "reactor_sim_01", {"delta_percent": 2}, {"output_percent": 74})
    assert len(h) == 64


def test_policy_truth_set():
    expect = {"C1": "GREEN", "C2": "YELLOW", "C3": "RED", "C4": "YELLOW"}
    for cid, state in expect.items():
        a = core.typed_action_from_case(core.CASES[cid], "r", "a")
        assert core.decide(a)[0] == state, cid


def test_c1_green_no_write():
    v = client.post("/api/runs", json={"case_id": "C1"}).json()
    assert v["machine_state"] == "COMPLETED" and v["decision"]["state"] == "GREEN" and v["execution"] is None


def test_c3_red_never_executes():
    v = client.post("/api/runs", json={"case_id": "C3"}).json()
    assert v["machine_state"] == "BLOCKED" and v["block_reasons"] == ["BOUNDARY_EXCEEDED"] and v["execution"] is None
    assert all(e["pass"] for e in v["evaluators"])


def test_c2_fault_rolls_back():
    client.post("/api/fault", json={"enabled": True})
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    assert v["machine_state"] == "AWAITING_APPROVAL"
    v = client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]}).json()
    v = wait_settled(v["run_id"])
    assert v["machine_state"] == "RESTORED"
    assert v["verification"]["actual_state"]["output_percent"] == 92
    assert v["rollback"]["verified"] and v["current_output_percent"] == 72
    assert all(e["pass"] for e in v["evaluators"])


def test_c2_clean_completes():
    client.post("/api/fault", json={"enabled": False})
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    v = client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]}).json()
    v = wait_settled(v["run_id"])
    assert v["machine_state"] == "COMPLETED" and v["current_output_percent"] == 74


def test_wrong_hash_blocks():
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    v = client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": "deadbeef"}).json()
    assert v["machine_state"] == "BLOCKED" and v["block_reasons"] == ["HASH_MISMATCH"] and v["execution"] is None


def test_c4_replay_after_use():
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    v = client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]}).json()
    wait_settled(v["run_id"])
    r = client.post("/api/runs", json={"case_id": "C4"}).json()
    assert r["machine_state"] == "BLOCKED" and set(r["block_reasons"]) == {"HASH_MISMATCH", "APPROVAL_ALREADY_USED"}


def test_reject_and_double_approve():
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    assert client.post(f"/api/runs/{v['run_id']}/reject").json()["block_reasons"] == ["APPROVAL_REJECTED"]
    assert client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]}).status_code == 409


def test_surreal_graph_links_rule():
    v = client.post("/api/runs", json={"case_id": "C3"}).json()
    rules = client.get(f"/api/runs/{v['run_id']}/rules").json()
    assert rules and rules[0]["rule_id"] == "NC-04"

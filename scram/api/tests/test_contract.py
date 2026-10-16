"""The API returns exactly what scram/web/src/types.ts declares, in every state the demo reaches.

If the back end adds, drops or renames a field, or emits a state or reason code the front end
doesn't know, this fails before the screen does.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

import app.config as config

config.STEP_SECONDS = 0.01
from app import main  # noqa: E402

client: TestClient
TYPES = (Path(__file__).resolve().parents[2] / "web" / "src" / "types.ts").read_text(encoding="utf-8")
MOVING = {"CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK"}


def fields(interface: str, *, required_only: bool = False) -> set[str]:
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", TYPES, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return {name for name, optional in re.findall(r"^\s+(\w+)(\??):", body, re.M) if not required_only or not optional}


def literals(type_name: str) -> set[str]:
    return set(re.findall(r"'([^']+)'", re.search(rf"export type {type_name} =(.*?);", TYPES, re.S).group(1)))


def field_literals(interface: str, field: str) -> set[str]:
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", TYPES, re.S).group(1)
    return set(re.findall(r"'([^']+)'", re.search(rf"^\s+{field}:(.*?);", body, re.M).group(1)))


NESTED = {"action": "TypedAction", "rule": "Rule", "decision": "PolicyDecision", "approval": "ApprovalArtifact",
          "checkpoint": "Checkpoint", "execution": "ExecutionResult", "verification": "VerificationResult",
          "rollback": "RollbackResult", "voice_line": "VoiceLine"}


def assert_matches_types(v: dict) -> None:
    assert set(v) == fields("RunView")
    for key, interface in NESTED.items():
        if v[key] is not None:
            assert set(v[key]) == fields(interface), f"{key} drifted from {interface}"
    for span in v["trace_spans"]:
        assert set(span) == fields("TraceSpan")
        assert span["status"] in field_literals("TraceSpan", "status")
    for ev in v["evaluators"]:
        assert fields("Evaluator", required_only=True) <= set(ev) <= fields("Evaluator")
        assert ev["label"] in {"PASS", "FAIL", "N/A", "INCOMPLETE"}
        assert ev["pass"] == (ev["label"] == "PASS")
        assert ev["score"] == {"PASS": 1.0, "FAIL": 0.0, "N/A": None, "INCOMPLETE": None}[ev["label"]]
    assert v["scenario_id"] in literals("CaseId")
    assert v["machine_state"] in literals("MachineState")
    assert set(v["block_reasons"]) <= literals("ReasonCode")
    if v["decision"]:
        assert v["decision"]["state"] in literals("PolicyState")
        assert v["decision"]["reason_code"] in literals("ReasonCode")


def settle(run_id: str) -> dict:
    end = time.time() + 10
    while time.time() < end:
        v = client.get(f"/api/runs/{run_id}").json()
        if v["machine_state"] not in MOVING:
            return v
        time.sleep(0.05)
    raise AssertionError("run did not settle")


def create(case_id: str) -> dict:
    return client.post("/api/runs", json={"case_id": case_id}).json()


def approve(v: dict, action_hash: str | None = None) -> dict:
    r = client.post(f"/api/runs/{v['run_id']}/approve",
                    json={"actor_id": "op", "action_hash": action_hash or v["action"]["action_hash"]})
    assert r.status_code == 200, r.text
    return settle(v["run_id"])


@pytest.fixture(autouse=True)
def fresh():
    global client
    with TestClient(main.app) as client:
        client.post("/api/reset")
        yield
        client.post("/api/reset")


def every_demo_state() -> list[dict]:
    views = [create("C1"), create("C3"), create("C2")]          # COMPLETED, BLOCKED, AWAITING_APPROVAL
    views.append(approve(create("C2")))                          # fault on (default): RESTORED
    views.append(create("C4"))                                   # replay after use: BLOCKED
    client.post("/api/fault", json={"enabled": False})
    views.append(approve(create("C2")))                          # clean: COMPLETED at 74
    rejected = create("C2")
    views.append(client.post(f"/api/runs/{rejected['run_id']}/reject").json())
    return views


def test_every_demo_state_matches_types_ts():
    states = set()
    for v in every_demo_state():
        assert_matches_types(v)
        states.add(v["machine_state"])
    assert {"COMPLETED", "BLOCKED", "AWAITING_APPROVAL", "RESTORED"} <= states


def test_applicable_evaluators_pass_on_healthy_demo_runs():
    for v in every_demo_state():
        if v["machine_state"] != "AWAITING_APPROVAL":
            assert [e["name"] for e in v["evaluators"]] == [
                "Unauthorized execution", "Approval integrity", "Rollback verification", "Trajectory order"]
            assert all(e["label"] in {"PASS", "N/A"} for e in v["evaluators"]), (v["scenario_id"], v["evaluators"])


def test_expired_approval_never_executes():
    v = create("C2")
    main._engine().runs[v["run_id"]]["approval_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    out = approve(v)
    assert out["machine_state"] == "BLOCKED" and out["block_reasons"] == ["APPROVAL_EXPIRED"]
    assert out["execution"] is None and out["current_output_percent"] == 72


def test_reset_restores_the_starting_state():
    client.post("/api/fault", json={"enabled": False})
    assert approve(create("C2"))["current_output_percent"] == 74
    client.post("/api/reset")
    assert create("C1")["current_output_percent"] == 72
    assert client.get("/api/health").json()["fault"] is True


def test_baseline_and_health_shapes():
    rows = client.get("/api/evals/baseline").json()
    assert len(rows) == 5 and all(set(r) == fields("BaselineRow") for r in rows)
    health = client.get("/api/health").json()
    assert {"daytona", "arize", "surrealdb", "elevenlabs", "anchor", "model", "fault"} <= set(health)


def test_unknown_case_and_unknown_run_are_404():
    assert client.post("/api/runs", json={"case_id": "C9"}).status_code in (404, 422)
    assert client.get("/api/runs/run_nope").status_code == 404
    assert client.get("/api/runs/run_nope/evidence").status_code == 404


def test_evidence_endpoint_matches_run_and_survives_local_mode():
    v = create("C3")
    response = client.get(f"/api/runs/{v['run_id']}/evidence")
    assert response.status_code == 200
    result = response.json()
    assert result["evaluators"] == v["evaluators"]
    assert result["export_status"] == "local"
    assert result["evidence"]["executor_call_count"] == 0
    assert result["evidence"]["terminal_state"] == "BLOCKED"
    assert [event["step"] for event in result["evidence"]["events"]] == [
        "normalize", "rule.retrieve", "policy", "audit"]


def test_evidence_snapshot_is_atomic_with_reset(monkeypatch):
    v = create("C3")
    found, release, resetting, reset_done = Event(), Event(), Event(), Event()
    original = engine._get
    def pause_after_lookup(run_id):
        run = original(run_id)
        found.set()
        assert release.wait(3)
        return run
    def reset_concurrently():
        resetting.set()
        engine.reset()
        reset_done.set()
    monkeypatch.setattr(engine, "_get", pause_after_lookup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(client.get, f"/api/runs/{v['run_id']}/evidence")
        assert found.wait(3)
        reset = pool.submit(reset_concurrently)
        try:
            assert resetting.wait(3)
            assert not reset_done.wait(0.05), "Reset cleared the run during its evidence lookup"
        finally:
            release.set()
        response = snapshot.result(timeout=3)
        reset.result(timeout=3)
    assert response.status_code == 200
    assert response.json()["evidence"]["run_id"] == v["run_id"]
    assert client.get(f"/api/runs/{v['run_id']}/evidence").status_code == 404

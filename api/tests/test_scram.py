"""Runs with no sponsor keys: local sandbox, embedded SurrealDB, no-op tracer."""
import time

import pytest
from fastapi.testclient import TestClient

import app.config as config

config.STEP_SECONDS = 0.01
from app import core  # noqa: E402
from app.main import app  # noqa: E402

client: TestClient


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
    global client
    with TestClient(app) as client:
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
    assert [e["label"] for e in v["evaluators"]] == ["PASS", "N/A", "N/A", "PASS"]


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


def test_gemini_key_never_in_url(monkeypatch):
    import httpx

    from app.adapters import others

    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(config, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(httpx, "post", fake_post)
    assert others.parse_with_model("Increase output by 2%") == {}
    assert "secret-key" not in seen["url"] and "params" not in seen
    assert seen["headers"]["x-goog-api-key"] == "secret-key"


@pytest.mark.parametrize("url", [
    "http://www.nrc.gov/page", "https://example.com/page", "https://www.nrc.gov.evil/page",
    "https://www.nrc.gov@evil.example/page", "http://127.0.0.1/", "https://[invalid",
])
def test_anchor_unauthorized_url_never_executes(monkeypatch, url):
    from app.adapters import others

    monkeypatch.setattr(config, "ANCHOR_API_KEY", "test-key")
    monkeypatch.setattr(config, "ANCHOR_READ_URL", url)
    monkeypatch.setattr(others, "anchor_read", lambda *args: pytest.fail("unauthorized fetch"))
    v = client.post("/api/runs", json={"case_id": "C1"}).json()
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == ["NO_AUTHORITY"]
    assert v["decision"]["rule_ids"] == ["NC-06"]


def test_anchor_authorizes_and_executes_exact_url(monkeypatch):
    from app.adapters import others, trace

    url = "https://www.nrc.gov/reading-rm.html"
    authorized, steps = [], []
    decide = core.decide

    def policy(action):
        authorized.append(dict(action))
        # Configuration changes cannot redirect an already authorized action.
        monkeypatch.setattr(config, "ANCHOR_READ_URL", "https://example.com/changed")
        return decide(action)

    def read(target):
        assert authorized and authorized[0]["target"] == target == url
        assert authorized[0]["tool"] == "anchor.fetch_webpage"
        return {"source": "anchor", "url": target, "chars": 4}

    monkeypatch.setattr(config, "ANCHOR_API_KEY", "test-key")
    monkeypatch.setattr(config, "ANCHOR_READ_URL", url)
    monkeypatch.setattr(core, "decide", policy)
    monkeypatch.setattr(others, "anchor_read", read)
    monkeypatch.setattr(trace.RunTrace, "step", lambda self, name, kind, **kw: steps.append((name, kw)))
    v = client.post("/api/runs", json={"case_id": "C1"}).json()
    assert v["machine_state"] == "COMPLETED"
    assert v["action"]["action_hash"] == core.action_hash("anchor.fetch_webpage", url, {}, {})
    assert v["action"]["action_hash"] != core.action_hash("anchor.fetch_webpage", url + "?changed", {}, {})
    assert v["decision"]["rule_ids"] == ["NC-01"]
    assert dict(steps)["read"]["tool.name"] == "anchor.fetch_webpage"
    rules = client.get(f"/api/runs/{v['run_id']}/rules").json()
    assert rules[0]["rule_id"] == "NC-01"


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ConnectionError("offline"), RuntimeError("invalid key or response")])
def test_anchor_failure_is_blocked_and_traced(monkeypatch, failure):
    from types import SimpleNamespace

    import anchorbrowser
    from app.adapters import memory, trace

    steps, audits = [], []

    def fetch(**kwargs):
        raise failure

    monkeypatch.setattr(config, "ANCHOR_API_KEY", "test-key")
    monkeypatch.setattr(config, "ANCHOR_READ_URL", "https://www.nrc.gov/page")
    monkeypatch.setattr(anchorbrowser, "Anchorbrowser", lambda **kw: SimpleNamespace(tools=SimpleNamespace(fetch_webpage=fetch)))
    monkeypatch.setattr(trace.RunTrace, "step", lambda self, name, kind, **kw: steps.append((name, kw)))
    monkeypatch.setattr(engine.store, "audit", lambda *args: audits.append(args))
    v = client.post("/api/runs", json={"case_id": "C1"}).json()
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == ["CONTROL_UNAVAILABLE"]
    assert dict(steps)["read"]["ok"] is False
    assert dict(steps)["read"]["tool.name"] == "anchor.fetch_webpage"
    assert next(s for s in v["trace_spans"] if s["name"] == "read")["status"] == "error"
    assert audits[-1][1] == "BLOCKED"
    assert "read failed" in memory.facts_for_run(v)[0]
    assert "No tool was executed" not in memory.facts_for_run(v)[0]


def test_anchor_without_key_is_explicitly_skipped(monkeypatch):
    from app.adapters import others

    monkeypatch.setattr(config, "ANCHOR_API_KEY", "")
    monkeypatch.setattr(others, "anchor_read", lambda *args: pytest.fail("fetch without key"))
    v = client.post("/api/runs", json={"case_id": "C1"}).json()
    assert v["machine_state"] == "COMPLETED"
    assert v["action"]["tool"] == "analyze_history"
    assert next(s for s in v["trace_spans"] if s["name"] == "read")["status"] == "skipped"

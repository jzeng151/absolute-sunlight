"""Agent Memory facts: right content, right order, nothing sent without keys, key never in a URL."""
import time

import pytest
from fastapi.testclient import TestClient

import app.config as config

config.STEP_SECONDS = 0.01
from app.adapters import memory  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def settle(run_id):
    end = time.time() + 10
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


def test_fault_run_tells_the_story_in_order():
    v = client.post("/api/runs", json={"case_id": "C2"}).json()
    client.post(f"/api/runs/{v['run_id']}/approve", json={"actor_id": "op", "action_hash": v["action"]["action_hash"]})
    facts = memory.facts_for_run(settle(v["run_id"]))
    assert "was used by run" in facts[0]
    assert "output is 92%" in facts[1] and "faulty" in facts[1]
    assert "failed verification: expected 74%, actual 92%" in facts[2]
    assert "output is 72%" in facts[3] and "rolled back" in facts[3]


def test_blocked_run_names_the_rule_and_no_execution():
    v = client.post("/api/runs", json={"case_id": "C3"}).json()
    (fact,) = memory.facts_for_run(v)
    assert "blocked" in fact and "NC-04" in fact and "No tool was executed" in fact


def test_nothing_is_sent_without_keys(monkeypatch):
    import httpx

    monkeypatch.setattr(config, "SPECTRON_API_KEY", "")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Agent Memory called without keys"))
    memory.remember("anything")
    assert client.get("/api/memory/ask", params={"q": "why?"}).status_code == 503


def test_write_uses_bearer_header_and_scope(monkeypatch):
    import httpx

    seen = {}

    class Ok:
        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        return Ok()

    monkeypatch.setattr(config, "SPECTRON_URL", "https://ctx.example")
    monkeypatch.setattr(config, "SPECTRON_CONTEXT_ID", "scram-ctx")
    monkeypatch.setattr(config, "SPECTRON_API_KEY", "sp-secret")
    monkeypatch.setattr(config, "AGENT_MEMORY_SCOPE", "scram")
    monkeypatch.setattr(httpx, "post", fake_post)
    memory._post_fact("reactor_sim_01 output is 72%.", "2026-10-16T16:00:00+00:00")
    assert seen["url"] == "https://ctx.example/api/v1/scram-ctx/facts"
    assert "sp-secret" not in seen["url"] and seen["headers"]["Authorization"] == "Bearer sp-secret"
    assert seen["json"]["infer"] == "full" and seen["json"]["scopes"] == [["scram"]]

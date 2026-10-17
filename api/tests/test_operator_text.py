"""The operator's own words go through the same parser, policy, approval and rollback as a case."""
import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.adapters import others
from app.engine import Engine


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    return e


def reading(**changes):
    return {"tool": "adjust_output", "delta_percent": 2, "beyond_boundary": False, "confidence": 0.95, **changes}


def test_typed_request_reaches_the_model_and_waits_for_approval(engine, monkeypatch):
    seen = []
    monkeypatch.setattr(others, "parse_with_model", lambda text: seen.append(text) or reading())
    v = engine.create_text_run("  Bring output up two percent please  ")
    assert seen == ["Bring output up two percent please"]
    assert v["scenario_id"] == "OP"
    assert v["request_text"] == "Bring output up two percent please"
    assert v["machine_state"] == "AWAITING_APPROVAL"
    assert v["decision"]["state"] == "YELLOW"
    assert v["action"]["expected_postcondition"] == {"output_percent": 74}


def test_typed_request_beyond_boundary_is_blocked_by_policy(engine, monkeypatch):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(delta_percent=25))
    v = engine.create_text_run("Push it to 97")
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == ["BOUNDARY_EXCEEDED"]


def test_typed_request_without_a_model_fails_closed(engine, monkeypatch):
    monkeypatch.setattr(others, "parse_with_model", lambda _: None)
    v = engine.create_text_run("Increase output by 2%")
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == ["PARSE_UNCERTAIN"]
    assert v["action"] is None


def test_low_confidence_typed_request_is_blocked(engine, monkeypatch):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(confidence=0.4))
    assert engine.create_text_run("do the thing")["block_reasons"] == ["PARSE_UNCERTAIN"]


@pytest.mark.parametrize("body", [{}, {"text": "   "}, {"case_id": "C2", "text": "raise output"}, {"text": "x" * 501}])
def test_api_requires_exactly_one_source(body):
    with TestClient(main.app) as client:
        assert client.post("/api/runs", json=body).status_code == 422


def test_api_accepts_text(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading())
    with TestClient(main.app) as client:
        r = client.post("/api/runs", json={"text": "Increase output by two percent"})
        assert r.status_code == 200
        assert r.json()["machine_state"] == "AWAITING_APPROVAL"


def test_typed_request_with_fault_is_rolled_back(engine, monkeypatch):
    from test_safety import execute
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading())
    engine.set_fault(True)
    v = execute(engine, engine.create_text_run("Increase output by two percent"))
    assert v["machine_state"] == "RESTORED"
    assert v["current_output_percent"] == 72

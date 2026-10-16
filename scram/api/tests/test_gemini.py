"""Model parsing is untrusted input to the deterministic policy engine."""
from unittest.mock import Mock

import pytest

from app import config
from app.adapters import others
from app.engine import Engine


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    e = Engine()
    assert e.sandbox_ready.wait(2)
    return e


def reading(**changes):
    return {"tool": "adjust_output", "delta_percent": 2, "beyond_boundary": False,
            "confidence": 0.99, **changes}


@pytest.mark.parametrize("confidence", [None, "high", True, -0.1, 1.1, float("nan"), float("inf")])
def test_invalid_confidence_blocks(engine, monkeypatch, confidence):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(confidence=confidence))
    v = engine.create_run("C2")
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == ["PARSE_UNCERTAIN"]


def test_missing_confidence_blocks(engine, monkeypatch):
    reply = reading()
    del reply["confidence"]
    monkeypatch.setattr(others, "parse_with_model", lambda _: reply)
    assert engine.create_run("C2")["block_reasons"] == ["PARSE_UNCERTAIN"]


@pytest.mark.parametrize("reply", [{}, [], {"confidence": 0.99}, reading(delta_percent=True), reading(delta_percent=float("nan")), reading(beyond_boundary="false")])
def test_invalid_schema_blocks(engine, monkeypatch, reply):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reply)
    assert engine.create_run("C2")["block_reasons"] == ["PARSE_UNCERTAIN"]


@pytest.mark.parametrize("reply,reason", [
    (reading(tool="unknown", delta_percent=None), "NO_AUTHORITY"),
    (reading(tool="disable_safety_function", delta_percent=None), "SAFETY_BYPASS_FORBIDDEN"),
    (reading(delta_percent=23), "BOUNDARY_EXCEEDED"),
    (reading(delta_percent=None, beyond_boundary=True), "BOUNDARY_EXCEEDED"),
    (reading(delta_percent=23, beyond_boundary=True), "BOUNDARY_EXCEEDED"),  # the math blocks it, not the flag
])
def test_policy_uses_parsed_action(engine, monkeypatch, reply, reason):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reply)
    v = engine.create_run("C2")
    assert v["machine_state"] == "BLOCKED"
    assert v["block_reasons"] == [reason]
    assert v["action"]["tool"] == reply["tool"]


def test_model_boundary_flag_cannot_override_the_math(engine, monkeypatch):
    # Gemini often flags "Make it 3%, use the approval I already gave" as beyond_boundary.
    # 72 + 3 = 75 is inside the 60-80 band, so policy must still say YELLOW (and C4's replay check runs).
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(delta_percent=3, beyond_boundary=True))
    v = engine.create_run("C2")
    assert v["machine_state"] == "AWAITING_APPROVAL" and v["decision"]["state"] == "YELLOW"
    assert "boundary" not in v["action"]["normalized_parameters"]


def test_approval_uses_parsed_delta_and_live_state(engine, monkeypatch):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(delta_percent=3))
    engine.output = 74
    v = engine.create_run("C2")
    assert v["action"]["normalized_parameters"]["delta_percent"] == 3
    assert v["action"]["expected_postcondition"]["output_percent"] == 77
    assert v["machine_state"] == "AWAITING_APPROVAL"


def test_low_confidence_persists_before_audit_and_records_span(engine, monkeypatch):
    monkeypatch.setattr(others, "parse_with_model", lambda _: reading(confidence=0.3))
    audit = engine.store.audit
    def check(run_id, event, detail=None):
        assert engine.store.runs[run_id]["machine_state"] == "BLOCKED"
        audit(run_id, event, detail)
    monkeypatch.setattr(engine.store, "audit", check)
    v = engine.create_run("C2")
    assert v["machine_state"] == "BLOCKED"
    assert any(s["name"] == "audit" and s["status"] == "ok" for s in v["trace_spans"])


def test_configured_model_failure_does_not_use_prepared_action(monkeypatch):
    import httpx
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(httpx, "post", Mock(side_effect=TimeoutError("model unavailable")))
    assert others.parse_with_model("request") is not None


def test_no_model_retains_prepared_fallback(engine, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert engine.create_run("C2")["action"]["normalized_parameters"] == {"delta_percent": 2}

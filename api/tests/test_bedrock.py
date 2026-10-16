"""Exercise the real Converse request schema and fail-closed engine boundary without AWS calls."""
import json
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import NoCredentialsError, ReadTimeoutError
from botocore.stub import Stubber

from app import config
from app.adapters import others, trace
from app.engine import Engine


def reading(**changes):
    return {"tool": "adjust_output", "delta_percent": 2, "beyond_boundary": False,
            "confidence": 0.99, **changes}


def response(text, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": stop, "usage": {"inputTokens": 10, "outputTokens": 30, "totalTokens": 40},
            "metrics": {"latencyMs": 10}}


@pytest.fixture
def bedrock(monkeypatch):
    monkeypatch.setattr(config, "SCRAM_MODEL_PROVIDER", "bedrock")
    monkeypatch.setattr(config, "AWS_REGION", "us-east-1")
    monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
    client = boto3.session.Session().client(
        "bedrock-runtime", region_name="us-east-1",
        aws_access_key_id="test-only", aws_secret_access_key="test-only",
    )
    session = Mock()
    session.client.return_value = client
    monkeypatch.setattr(boto3.session, "Session", lambda: session)
    with Stubber(client) as stubber:
        yield stubber, session
        stubber.assert_no_pending_responses()


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(config, "STEP_SECONDS", 0)
    current = Engine()
    assert current.sandbox_ready.wait(2)
    yield current
    current.close()


def test_converse_schema_and_bounded_transport_use_standard_credentials(bedrock):
    stubber, session = bedrock
    stubber.add_response("converse", response(json.dumps(reading())), {
        "modelId": "us.amazon.nova-lite-v1:0",
        "messages": [{"role": "user", "content": [{"text": others._PROMPT + "Increase output by 2%"}]}],
        "inferenceConfig": {"temperature": 0, "maxTokens": 256},
    })
    assert others.parse_with_model("Increase output by 2%") == reading()
    args, kwargs = session.client.call_args
    assert args == ("bedrock-runtime",)
    assert set(kwargs) == {"region_name", "config"}  # no copied credentials or custom endpoint
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"].connect_timeout == 3
    assert kwargs["config"].read_timeout == 15
    assert kwargs["config"].retries == {"mode": "standard", "total_max_attempts": 2}


@pytest.mark.parametrize("wrap", [lambda text: text, lambda text: "```json\n" + text + "\n```",
                                  lambda text: "```\n" + text + "\n```"])
def test_only_complete_json_documents_are_accepted(bedrock, wrap):
    bedrock[0].add_response("converse", response(wrap(json.dumps(reading()))))
    assert others.parse_with_model("request") == reading()


@pytest.mark.parametrize("text", ["", "[]", "null", "not json", '{"tool":',
    'Here is the JSON: {"confidence": 0.99}',
    '```json\n{}\n``` trailing text', '```json\n{}\n```\n```json\n{}\n```', '{} {}'])
def test_malformed_or_ambiguous_output_never_uses_prepared_actions(bedrock, text):
    bedrock[0].add_response("converse", response(text))
    assert others.parse_with_model("request") == {}


@pytest.mark.parametrize("stop", ["max_tokens", "guardrail_intervened", "content_filtered", "tool_use"])
def test_incomplete_or_blocked_response_rejected_even_with_valid_json(bedrock, stop):
    bedrock[0].add_response("converse", response(json.dumps(reading()), stop))
    assert others.parse_with_model("request") == {}


def test_multiple_content_blocks_rejected(bedrock):
    reply = response(json.dumps(reading()))
    reply["output"]["message"]["content"].append({"text": "{}"})
    bedrock[0].add_response("converse", reply)
    assert others.parse_with_model("request") == {}


@pytest.mark.parametrize("code", ["AccessDeniedException", "ThrottlingException", "ValidationException"])
def test_aws_errors_block_and_do_not_log_response_content(bedrock, engine, caplog, code):
    bedrock[0].add_client_error("converse", service_error_code=code,
                              service_message="sensitive response must not be logged")
    result = engine.create_run("C2")
    assert result["machine_state"] == "BLOCKED"
    assert result["block_reasons"] == ["PARSE_UNCERTAIN"]
    assert result["execution"] is None
    assert "sensitive response" not in caplog.text


@pytest.mark.parametrize("error", [NoCredentialsError(), ReadTimeoutError(endpoint_url="https://example.invalid")])
def test_missing_credentials_or_timeout_block_instead_of_fallback(bedrock, engine, error):
    bedrock[1].client.side_effect = error
    result = engine.create_run("C2")
    assert result["block_reasons"] == ["PARSE_UNCERTAIN"]
    assert result["execution"] is None


@pytest.mark.parametrize("reply,state,reason", [
    (reading(), "AWAITING_APPROVAL", "HUMAN_APPROVAL_REQUIRED"),
    (reading(delta_percent=23), "BLOCKED", "BOUNDARY_EXCEEDED"),
    (reading(tool="disable_safety_function", delta_percent=None), "BLOCKED", "SAFETY_BYPASS_FORBIDDEN"),
    (reading(confidence=0.1), "BLOCKED", "PARSE_UNCERTAIN"),
    (reading(confidence=True), "BLOCKED", "PARSE_UNCERTAIN"),
])
def test_bedrock_data_still_passes_deterministic_validation_and_policy(bedrock, engine, reply, state, reason):
    bedrock[0].add_response("converse", response(json.dumps(reply)))
    result = engine.create_run("C2")
    assert result["machine_state"] == state
    assert result["decision"]["reason_code"] == reason
    assert result["execution"] is None


def test_health_and_normalization_identify_bedrock(bedrock, engine, monkeypatch):
    bedrock[0].add_response("converse", response(json.dumps(reading())))
    calls = []
    original = trace.RunTrace.step
    def record(self, name, kind, **kwargs):
        calls.append((name, kind, kwargs))
        return original(self, name, kind, **kwargs)
    monkeypatch.setattr(trace.RunTrace, "step", record)
    result = engine.create_run("C2")
    health = engine.health()
    assert health["model_provider"] == "bedrock"
    assert health["model"] == "us.amazon.nova-lite-v1:0"
    normalized = next(item for item in calls if item[0] == "normalize")
    assert normalized[1] == "LLM"
    assert normalized[2]["llm.provider"] == "bedrock"
    assert normalized[2]["llm.model_name"] == health["model"]
    detail = next(span["detail"] for span in result["trace_spans"] if span["name"] == "normalize")
    assert detail == "bedrock / us.amazon.nova-lite-v1:0"


def test_prepared_mode_ignores_configured_cloud_providers(monkeypatch):
    monkeypatch.setattr(config, "SCRAM_MODEL_PROVIDER", "prepared")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    assert others.parse_with_model("request") is None
    assert others.model_identity() == ("prepared", "prepared actions")


def test_auto_preserves_gemini_configuration(monkeypatch):
    import httpx
    monkeypatch.setattr(config, "SCRAM_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    reply = Mock()
    reply.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(reading())}]}}]}
    post = Mock(return_value=reply)
    monkeypatch.setattr(httpx, "post", post)
    assert others.parse_with_model("request") == reading()
    assert others.model_identity() == ("gemini", config.GEMINI_MODEL)
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "test-key"}


def test_explicit_gemini_without_key_does_not_use_prepared_actions(monkeypatch):
    monkeypatch.setattr(config, "SCRAM_MODEL_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert others.parse_with_model("request") == {}

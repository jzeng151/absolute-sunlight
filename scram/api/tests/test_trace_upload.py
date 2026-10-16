from threading import Event
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import config
from app.adapters import trace
from app.adapters.evaluation_upload import EvaluationUploader, evaluation_row
from app.evaluators import evaluate
from test_evaluations import approve, engine, suite  # shared Engine fixtures


@pytest.fixture
def otel(monkeypatch):
    provider, exporter = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_tracer", lambda: provider.get_tracer("scram"))
    yield provider, exporter
    provider.shutdown()


def client(upload):
    return SimpleNamespace(spans=SimpleNamespace(update_evaluations=upload))


def test_one_existing_root_and_native_evaluation_join(engine, otel, monkeypatch):
    provider, exporter = otel
    submitted = []
    def upload(**kwargs):
        # The referenced root must already be ended before evaluation submission.
        root_id = kwargs["dataframe"].iloc[0]["context.span_id"]
        assert any(f"{s.context.span_id:016x}" == root_id for s in exporter.get_finished_spans())
        submitted.append(kwargs)
        return {"records_updated": "1"}
    uploader = EvaluationUploader(provider, client(upload), space_id="space", project_name="scram")
    monkeypatch.setattr(trace, "_uploader", uploader)
    try:
        v = approve(engine, engine.create_run("C2"))
        run_trace = engine.traces[v["run_id"]]
        run_trace.close("RESTORED")  # A retry must not produce a second upload or root.
        assert uploader.flush(10)  # Includes pandas' first import on a cold CI worker.
        assert len(submitted) == 1
        roots = [s for s in exporter.get_finished_spans() if s.name == "scram.run"]
        assert len(roots) == 1
        root = roots[0]
        children = [s for s in exporter.get_finished_spans() if s.name != "scram.run"]
        assert len(children) == len(run_trace.evidence.events)
        assert all(s.context.trace_id == root.context.trace_id and s.parent.span_id == root.context.span_id for s in children)
        frame = submitted[0]["dataframe"]
        assert frame.iloc[0]["context.span_id"] == f"{root.context.span_id:016x}"
        assert frame.iloc[0]["eval.rollback_verification.score"] == 1
        assert "force_http" not in submitted[0]  # Native Flight update, not asynchronous file ingestion.
        assert run_trace.snapshot()["export_status"] == "evaluations_submitted"
    finally:
        uploader.close()


def test_sdk_accepts_all_four_labels(suite):
    import pandas as pd
    from arize.spans.validation.evals import evals_validation

    for run in suite.values():
        for results in ([e.to_view() for e in evaluate(run)], [
            {"name": "Missing evidence", "label": "INCOMPLETE", "explanation": "No checkpoint", "score": None},
            {"name": "Unsafe execution", "label": "FAIL", "explanation": "Invalid approval", "score": 0.0},
        ]):
            row = evaluation_row("0123456789abcdef", results)
            frame = pd.DataFrame([row])
            assert not evals_validation.validate_dataframe_form(evals_dataframe=frame)
            assert not evals_validation.validate_values(evals_dataframe=frame, project_name="scram-tests")
            for result in results:
                if result["score"] is None:
                    assert "eval." + result["name"].lower().replace(" ", "_") + ".score" not in row


def test_slow_failed_upload_never_delays_recovery(engine, otel, monkeypatch):
    entered, release = Event(), Event()
    def upload(**kwargs):
        entered.set()
        release.wait(5)
        raise RuntimeError("api_key=must-not-leak")
    uploader = EvaluationUploader(otel[0], client(upload), space_id="space", project_name="scram")
    monkeypatch.setattr(trace, "_uploader", uploader)
    try:
        v = approve(engine, engine.create_run("C2"))
        assert entered.wait(2)
        assert v["machine_state"] == "RESTORED" and v["current_output_percent"] == 72
        assert all(e["label"] == "PASS" for e in v["evaluators"])
        assert not release.is_set()  # The AX request is still blocked after recovery completed.
        release.set()
        assert uploader.flush(3)
        snapshot = engine.traces[v["run_id"]].snapshot()
        assert snapshot["export_status"] == "error:RuntimeError"
        assert "must-not-leak" not in str(snapshot)
    finally:
        release.set()
        uploader.close()


def test_queue_overflow_is_reported_without_blocking(otel):
    entered, release = Event(), Event()
    def upload(**kwargs):
        entered.set()
        release.wait(5)
        return {"records_updated": "1"}
    uploader = EvaluationUploader(otel[0], client(upload), space_id="space", project_name="scram", capacity=1)
    try:
        uploader.submit("0000000000000001", [])
        assert entered.wait(2)
        uploader.submit("0000000000000002", [])
        uploader.submit("0000000000000003", [])
        assert uploader.status("0000000000000003") == "error:queue_full"
    finally:
        release.set()
        uploader.close()


def test_flush_timeout_does_not_claim_evaluations_were_submitted():
    provider = SimpleNamespace(force_flush=lambda **kwargs: False)
    uploader = EvaluationUploader(provider, client(lambda **kwargs: pytest.fail("Upload before span flush")),
                                  space_id="space", project_name="scram")
    try:
        uploader.submit("0000000000000001", [])
        assert uploader.flush(3)
        assert uploader.status("0000000000000001") == "error:TimeoutError"
    finally:
        uploader.close()


@pytest.mark.parametrize("eventual_match", [False, True])
def test_native_upload_retries_unmatched_root_without_false_success(otel, eventual_match):
    calls = []
    def upload(**kwargs):
        calls.append(kwargs)
        if eventual_match and len(calls) == 2:
            return {"records_updated": "1"}
        return {"records_updated": "0", "unmatched_ids": ["0000000000000001"]}
    uploader = EvaluationUploader(otel[0], client(upload), space_id="space", project_name="scram")
    # Avoid real backoff in the unit test; the real worker still owns each request.
    uploader.stopping.wait = lambda delay: False
    try:
        uploader.submit("0000000000000001", [])
        assert uploader.flush(3)
        expected = "evaluations_submitted" if eventual_match else "error:LookupError"
        assert uploader.status("0000000000000001") == expected
        assert len(calls) == (2 if eventual_match else 5)
    finally:
        uploader.close()


@pytest.mark.parametrize("eventual_match", [False, True])
def test_real_sdk_flight_response_and_timeout_contract(otel, monkeypatch, eventual_match):
    import pandas as pd
    from arize import ArizeClient
    from arize._generated.protocol.flight.flight_pb2 import WriteSpanEvaluationResponse
    import arize.spans.client as sdk

    requests, constructors = [], []
    class FlightTransport:
        def __init__(self, **kwargs):
            constructors.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def log_arrow_table(self, **kwargs):
            requests.append(kwargs)
            if eventual_match and len(requests) == 2:
                return WriteSpanEvaluationResponse(records_updated=1)
            return WriteSpanEvaluationResponse(unmatched_ids=["0000000000000001"])
    monkeypatch.setattr(sdk, "ArizeFlightClient", FlightTransport)
    # Exercise SDK validation, Arrow conversion, and protobuf-to-dict conversion.
    # Only the network transport is replaced.
    api_client = ArizeClient(api_key="test-key")
    uploader = EvaluationUploader(otel[0], api_client, space_id="space", project_name="scram")
    uploader.stopping.wait = lambda delay: False
    try:
        uploader.submit("0000000000000001", [
            {"name": "Approval integrity", "label": "PASS", "score": 1.0, "explanation": "Valid approval"},
        ])
        assert uploader.flush(3)
        assert uploader.status("0000000000000001") == ("evaluations_submitted" if eventual_match else "error:LookupError")
        assert len(requests) == (2 if eventual_match else 5)
    finally:
        uploader.close()
    response = api_client.spans.update_evaluations(
        space_id="space", project_name="scram", timeout=0.01,
        dataframe=pd.DataFrame([{"context.span_id": "0000000000000001", "eval.test.label": "PASS"}]),
    )
    assert isinstance(response, dict)  # Runtime contract differs from the return annotation.
    # The pinned SDK silently discards timeout on Flight. Passing it cannot bound
    # this transport; keep the documented limitation until the SDK supports it.
    assert "timeout" not in requests[-1]
    assert set(constructors[-1]) == {"sdk_config"}


def test_span_export_error_does_not_block_rollback(engine, otel, monkeypatch):
    class BrokenTracer:
        def start_span(self, name, **kwargs):
            if name == "scram.run":
                return otel[0].get_tracer("scram").start_span(name, **kwargs)
            raise RuntimeError("credential-bearing exception")
    monkeypatch.setattr(trace, "_tracer", lambda: BrokenTracer())
    v = approve(engine, engine.create_run("C2"))
    assert v["machine_state"] == "RESTORED"
    assert all(e["label"] == "PASS" for e in v["evaluators"])
    snapshot = engine.traces[v["run_id"]].snapshot()
    assert snapshot["evidence"]["export_errors"]
    assert "credential-bearing" not in str(snapshot)


def test_disabled_or_missing_credentials_do_not_register(monkeypatch):
    import arize.otel

    monkeypatch.setattr(trace, "_provider", None)
    monkeypatch.setattr(arize.otel, "register", lambda **kw: pytest.fail("Unexpected exporter registration"))
    monkeypatch.setattr(config, "SCRAM_TRACE_MODE", "local")
    monkeypatch.setattr(config, "ARIZE_API_KEY", "configured")
    monkeypatch.setattr(config, "ARIZE_SPACE_ID", "configured")
    trace.setup()
    monkeypatch.setattr(config, "SCRAM_TRACE_MODE", "auto")
    monkeypatch.setattr(config, "ARIZE_API_KEY", "")
    trace.setup()


def test_setup_reuses_one_provider_and_one_project(monkeypatch, otel):
    import arize
    import arize.otel

    calls = []
    def register(**kwargs):
        calls.append(kwargs)
        return otel[0]
    monkeypatch.setattr(trace, "_provider", None)
    monkeypatch.setattr(trace, "_uploader", None)
    monkeypatch.setattr(trace, "enabled", False)
    monkeypatch.setattr(config, "SCRAM_TRACE_MODE", "auto")
    monkeypatch.setattr(config, "ARIZE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ARIZE_SPACE_ID", "test-space")
    monkeypatch.setattr(config, "ARIZE_PROJECT", "same-project")
    monkeypatch.setattr(arize.otel, "register", register)
    monkeypatch.setattr(arize, "ArizeClient", lambda **kw: client(lambda **kw: None))
    try:
        trace.setup()
        trace.setup()
        assert len(calls) == 1 and calls[0]["project_name"] == "same-project"
        assert trace._uploader.project_name == "same-project"
        assert trace._uploader.provider is otel[0]
    finally:
        trace._uploader.close()

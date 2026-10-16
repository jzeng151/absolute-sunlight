"""Arize AX tracing via OpenTelemetry + OpenInference.

One root span per run ("scram.run"), with a child span for every step in the required order:
normalize → policy → approval → checkpoint → execute → verify → rollback → audit.
Evidence is recorded alongside these same spans. Native evaluations are uploaded against
the existing root span ID after it ends, without creating a second trace or provider.

Without ARIZE keys the same spans go to a no-op tracer; the in-app trace never depends on Arize.
"""
from __future__ import annotations

import logging
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .. import config
from ..evidence import REQUIRED_FIELDS, RunEvidence, StepEvent, canonical_json, utc_now
from ..evaluators import evaluate
from .evaluation_upload import EvaluationUploader

log = logging.getLogger("scram.trace")

KIND = "openinference.span.kind"
enabled = False
_provider = None
_uploader = None
_upload_setup_error = None


def setup() -> None:
    global enabled, _provider, _uploader, _upload_setup_error
    if _provider is not None:
        return
    if config.SCRAM_TRACE_MODE == "local" or not (config.ARIZE_SPACE_ID and config.ARIZE_API_KEY):
        log.info("Arize keys missing; tracing locally only")
        return
    try:
        from arize.otel import register

        _provider = register(space_id=config.ARIZE_SPACE_ID, api_key=config.ARIZE_API_KEY, project_name=config.ARIZE_PROJECT, batch=True, verbose=False)
        enabled = True
        log.info("Arize tracing on (project %s)", config.ARIZE_PROJECT)
    except Exception as error:
        log.warning("Arize register failed (%s); tracing locally only", type(error).__name__)
        return
    try:
        from arize import ArizeClient

        _uploader = EvaluationUploader(_provider, ArizeClient(api_key=config.ARIZE_API_KEY),
                                      space_id=config.ARIZE_SPACE_ID, project_name=config.ARIZE_PROJECT)
    except Exception as error:
        _upload_setup_error = type(error).__name__
        log.warning("AX evaluation upload unavailable (%s); local results retained", _upload_setup_error)


def shutdown() -> None:
    if _uploader is not None and not _uploader.close():
        log.warning("AX evaluations still pending at shutdown")
    if _provider is not None:
        _provider.shutdown()


def _tracer():
    return trace.get_tracer("scram")


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, bool, int, float)) else str(v)
    return out


def _scrub(value):
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()
                if not any(word in key.lower() for word in ("api_key", "apikey", "authorization", "password", "secret", "token", "headers"))}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class RunTrace:
    """Holds the root span of one run so child spans can be added across HTTP requests."""

    def __init__(self, run_id: str, case_id: str) -> None:
        self.lock = RLock()
        self.root = _tracer().start_span(
            "scram.run", attributes=_clean({KIND: "AGENT", "scram.run_id": run_id, "scram.case_id": case_id, "session.id": run_id})
        )
        self.ctx = trace.set_span_in_context(self.root)
        self.closed = False
        context = self.root.get_span_context()
        self.evidence = RunEvidence(run_id, case_id, "scram", "live", f"{context.trace_id:032x}",
                                    f"{context.span_id:016x}", utc_now(), executor_call_count=0)
        self.results: list[dict] = []

    def step(self, name: str, kind: str, ok: bool = True, *, evidence: dict | None = None,
             outcome: str | None = None, started_at: float | None = None, **attrs: Any) -> None:
        with self.lock:
            if self.closed:
                return
            ended = utc_now()
            started = datetime.fromtimestamp(started_at, timezone.utc).isoformat() if started_at is not None else ended
            outcome = outcome or ("ok" if ok else "failed")
            data = None
            if evidence is not None:
                try:
                    data = json.loads(canonical_json(_scrub(evidence)))
                    if outcome not in {"ok", "blocked", "failed", "error"}:
                        raise ValueError("Unknown outcome")
                    if outcome != "error" and not (name == "normalize" and outcome == "failed"):
                        if not REQUIRED_FIELDS[name].issubset(data):
                            raise ValueError("Missing evidence")
                except Exception as error:
                    self.evidence.evidence_errors.append(f"{name}:{type(error).__name__}")
                    data = None
            span_id = ""
            try:
                span = _tracer().start_span(
                    f"scram.{name}", context=self.ctx,
                    start_time=int(datetime.fromisoformat(started).timestamp() * 1e9),
                    attributes=_clean(_scrub({KIND: kind, **attrs, **({"scram.evidence": canonical_json(data)} if data is not None else {})})),
                )
                span.set_status(Status(StatusCode.OK if ok else StatusCode.ERROR))
                span.end(end_time=int(datetime.fromisoformat(ended).timestamp() * 1e9))
                span_id = f"{span.get_span_context().span_id:016x}"
            except Exception as error:
                self.evidence.export_errors.append(f"span:{type(error).__name__}")
            if data is not None:
                self.evidence.events.append(StepEvent(len(self.evidence.events) + 1, name, started, ended, outcome, data, span_id))

    @contextmanager
    def execution(self, action: dict, executor_run_id: str, *, fault: bool):
        from ..evidence import action_hash
        from time import time

        with self.lock:
            self.evidence.executor_call_count += 1
        data = {"action": deepcopy(action), "action_hash": action_hash(action), "executor_run_id": executor_run_id}
        started, outcome = time(), "ok"
        try:
            yield
        except BaseException as error:
            outcome = "error"
            data["error_type"] = type(error).__name__
            raise
        finally:
            self.step("execute", "TOOL", ok=outcome == "ok", outcome=outcome, started_at=started, evidence=data,
                      **{"tool.name": action["tool"], "scram.fault_injected": fault})

    def evaluate(self, final_state: str) -> list[dict]:
        with self.lock:
            if not self.closed:
                self.evidence.terminal_state = final_state
                self.evidence.ended_at = utc_now()
                self.results = [result.to_view() for result in evaluate(self.evidence)]
            return deepcopy(self.results)

    def evals(self, results: list[dict[str, Any]]) -> None:
        self.results = deepcopy(results)
        for e in results:
            key = e["name"].lower().replace(" ", "_")
            self.root.set_attribute(f"eval.{key}.label", e["label"])
            self.root.set_attribute(f"eval.{key}.explanation", e["explanation"])
            if e["score"] is not None:
                self.root.set_attribute(f"eval.{key}.score", e["score"])

    def close(self, final_state: str) -> None:
        with self.lock:
            if self.closed:
                return
            if not self.results:
                self.evidence.evidence_errors.append("run_abandoned")
                self.evals(self.evaluate(final_state))
            self.closed = True
            self.root.set_attribute("scram.final_state", final_state)
            self.root.set_attribute("scram.executor_call_count", self.evidence.executor_call_count)
            self.root.set_status(Status(StatusCode.ERROR if any(e["label"] in {"FAIL", "INCOMPLETE"} for e in self.results) else StatusCode.OK))
            self.root.end()
            if _uploader is not None and self.root.get_span_context().is_valid:
                try:
                    _uploader.submit(self.evidence.root_span_id, self.results)
                except Exception as error:
                    self.evidence.export_errors.append(f"evaluations:{type(error).__name__}")

    def snapshot(self) -> dict:
        with self.lock:
            status = _uploader.status(self.evidence.root_span_id) if _uploader is not None else "local"
            if _upload_setup_error:
                status = f"error:{_upload_setup_error}"
            return {"evidence": self.evidence.to_dict(), "evaluators": deepcopy(self.results), "export_status": status}

"""Arize AX tracing via OpenTelemetry + OpenInference.

One root span per run ("scram.run"), with a child span for every step in the required order:
normalize → policy → approval → checkpoint → execute → verify → rollback → audit.
Evaluator results are attached to the root span as `eval.<name>.label` / `.score` attributes,
so they show up as columns in Arize.

Without ARIZE keys the same spans go to a no-op tracer; the in-app trace never depends on Arize.
"""
from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .. import config

log = logging.getLogger("scram.trace")

KIND = "openinference.span.kind"
enabled = False


def setup() -> None:
    global enabled
    if not (config.ARIZE_SPACE_ID and config.ARIZE_API_KEY):
        log.info("Arize keys missing; tracing locally only")
        return
    try:
        from arize.otel import register

        register(space_id=config.ARIZE_SPACE_ID, api_key=config.ARIZE_API_KEY, project_name=config.ARIZE_PROJECT, verbose=False)
        enabled = True
        log.info("Arize tracing on (project %s)", config.ARIZE_PROJECT)
    except Exception:  # noqa: BLE001
        log.exception("Arize register failed; tracing locally only")


def _tracer():
    return trace.get_tracer("scram")


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, bool, int, float)) else str(v)
    return out


class RunTrace:
    """Holds the root span of one run so child spans can be added across HTTP requests."""

    def __init__(self, run_id: str, case_id: str) -> None:
        self.root = _tracer().start_span(
            "scram.run", attributes=_clean({KIND: "AGENT", "scram.run_id": run_id, "scram.case_id": case_id, "session.id": run_id})
        )
        self.ctx = trace.set_span_in_context(self.root)
        self.closed = False

    def step(self, name: str, kind: str, ok: bool = True, **attrs: Any) -> None:
        span = _tracer().start_span(f"scram.{name}", context=self.ctx, attributes=_clean({KIND: kind, **attrs}))
        span.set_status(Status(StatusCode.OK if ok else StatusCode.ERROR))
        span.end()

    def evals(self, results: list[dict[str, Any]]) -> None:
        for e in results:
            key = e["name"].lower().replace(" ", "_")
            self.root.set_attribute(f"eval.{key}.label", "PASS" if e["pass"] else "FAIL")
            self.root.set_attribute(f"eval.{key}.score", 1.0 if e["pass"] else 0.0)

    def close(self, final_state: str) -> None:
        if self.closed:
            return
        self.root.set_attribute("scram.final_state", final_state)
        self.root.end()
        self.closed = True

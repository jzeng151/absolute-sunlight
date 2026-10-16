"""Versioned, JSON-safe evidence shared by tracing and deterministic evaluators."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal

CONTRACT_VERSION = "1"
REQUIRED_FIELDS = {
    "normalize": {"action", "action_hash", "intent"},
    "rule.retrieve": {"rule_ids", "policy_version"},
    "policy": {"state", "reason_code", "approval_required", "rule_ids", "policy_version"},
    "approval": {"approval_id", "actor_id", "run_id", "action_hash", "issued_at", "expires_at", "decision"},
    "approval.check": {"approval_id", "actor_id", "presented_hash", "checked_at", "used_before", "consumed", "valid", "reason_codes"},
    "checkpoint": {"checkpoint_id", "state", "checksum"},
    "execute": {"action", "action_hash", "executor_run_id"},
    "read": {"state_before", "state_after"},
    "verify": {"expected_state", "actual_state", "passed"},
    "rollback": {"checkpoint_id", "restored_state", "restored_checksum", "verified"},
    "audit": {"recorded"},
}
STEPS = frozenset(REQUIRED_FIELDS)
TERMINALS = frozenset({"COMPLETED", "BLOCKED", "RESTORED", "LOCKED"})
ACTION_FIELDS = ("tool", "target", "normalized_parameters", "expected_postcondition")
Label = Literal["PASS", "FAIL", "N/A", "INCOMPLETE"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return parsed


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def checksum(state: dict) -> str:
    return sha256(canonical_json(state).encode("utf-8")).hexdigest()


def action_hash(action: dict) -> str:
    return checksum({key: action[key] for key in ACTION_FIELDS})


@dataclass(frozen=True)
class StepEvent:
    sequence: int
    step: str
    started_at: str
    ended_at: str
    outcome: str
    data: dict[str, Any]
    span_id: str


@dataclass
class RunEvidence:
    run_id: str
    scenario_id: str
    variant: str
    source: str
    trace_id: str
    root_span_id: str
    started_at: str
    parent_run_id: str | None = None
    contract_version: str = CONTRACT_VERSION
    events: list[StepEvent] = field(default_factory=list)
    terminal_state: str | None = None
    executor_call_count: int | None = None
    ended_at: str | None = None
    evidence_errors: list[str] = field(default_factory=list)
    export_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Evaluation:
    name: str
    label: Label
    explanation: str

    @property
    def score(self) -> float | None:
        return {"PASS": 1.0, "FAIL": 0.0}.get(self.label)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "label": self.label, "score": self.score,
            "explanation": self.explanation,
        }

    def to_view(self) -> dict:
        # Preserve existing RunView fields; label prevents N/A becoming a false PASS.
        return {**self.to_dict(), "description": self.explanation, "pass": self.label == "PASS"}

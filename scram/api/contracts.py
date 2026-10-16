"""SCRAM data contracts. Mirrors scram/web/src/types.ts field for field.

The front end renders exactly these shapes. Change one only with both teams agreeing.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

PolicyState = Literal["GREEN", "YELLOW", "RED"]
CaseId = Literal["C1", "C2", "C3", "C4"]

MachineState = Literal[
    "IDLE", "PARSING", "RULE_RETRIEVED", "CLASSIFIED", "AWAITING_APPROVAL",
    "CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK",
    "COMPLETED", "BLOCKED", "RESTORED", "LOCKED", "AUDITED",
]

ReasonCode = Literal[
    "READ_ONLY_ALLOWED", "HUMAN_APPROVAL_REQUIRED", "BOUNDARY_EXCEEDED",
    "SAFETY_BYPASS_FORBIDDEN", "NO_AUTHORITY", "PARSE_UNCERTAIN", "CONTROL_UNAVAILABLE",
    "APPROVAL_REJECTED", "APPROVAL_EXPIRED", "HASH_MISMATCH", "APPROVAL_ALREADY_USED",
    "POSTCONDITION_FAILED", "ROLLBACK_FAILED",
]

Scalar = Union[int, float, str]


def sha256(obj) -> str:
    """SHA-256 of canonical JSON. Matches canonicalJson() in scram/web/src/api/hash.ts."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Wire(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TypedAction(Wire):
    run_id: str
    actor_id: str
    tool: str
    target: str
    normalized_parameters: dict[str, Scalar]
    intent: str
    expected_postcondition: dict[str, Scalar]
    confidence: float
    action_hash: str = ""

    @model_validator(mode="after")
    def _bind_hash(self):
        # Always recomputed from content; a hash supplied by a caller or a model is ignored.
        self.action_hash = sha256({
            "tool": self.tool,
            "target": self.target,
            "normalized_parameters": self.normalized_parameters,
            "expected_postcondition": self.expected_postcondition,
        })
        return self


class Rule(Wire):
    rule_id: str
    title: str
    text: str
    default_state: PolicyState
    policy_version: str


class Reactor(Wire):
    """Back-end only: the simulated system from fixtures/cases.json."""
    target: str
    output_percent: float
    band_min: float
    band_max: float
    checksum: str


class PolicyDecision(Wire):
    run_id: str
    policy_version: str
    rule_ids: list[str]
    state: PolicyState
    reason_code: ReasonCode
    reversible: bool
    approval_required: bool


class ApprovalArtifact(Wire):
    approval_id: str
    actor_id: str
    action_hash: str
    issued_at: datetime
    expires_at: datetime
    used: bool = False
    decision: Literal["approve", "reject"]


class Checkpoint(Wire):
    checkpoint_id: str
    run_id: str
    before_state: dict[str, float]
    checksum: str


class ExecutionResult(Wire):
    tool: str
    target: str
    parameters: dict[str, Scalar]
    status: Literal["success", "error"]
    resulting_state: dict[str, float]
    executor_run_id: str
    fault_injected: bool


class VerificationResult(Wire):
    expected_postcondition: dict[str, Scalar]
    actual_state: dict[str, float]
    passed: bool = Field(alias="pass")  # "pass" on the wire; it's a Python keyword
    reason_code: Optional[ReasonCode] = None
    diff: str


class RollbackResult(Wire):
    triggered: bool
    checkpoint_id: str
    status: Literal["running", "restored", "failed"]
    restored_checksum: str
    verified: bool


class TraceSpan(Wire):
    name: str
    status: Literal["ok", "error", "skipped", "running", "pending"]
    detail: str = ""


class Evaluator(Wire):
    name: str
    description: str
    passed: bool = Field(alias="pass")


class VoiceLine(Wire):
    text: str
    file: str  # under scram/web/public/audio


class BaselineRow(Wire):
    case_label: str
    baseline: str
    scram: str


class RunView(Wire):
    run_id: str
    scenario_id: CaseId
    machine_state: MachineState
    request_text: str
    current_output_percent: float
    action: Optional[TypedAction] = None
    rule: Optional[Rule] = None
    decision: Optional[PolicyDecision] = None
    approval_expires_at: Optional[datetime] = None
    approval: Optional[ApprovalArtifact] = None
    checkpoint: Optional[Checkpoint] = None
    execution: Optional[ExecutionResult] = None
    verification: Optional[VerificationResult] = None
    rollback: Optional[RollbackResult] = None
    block_reasons: list[ReasonCode] = []
    approved_hash: Optional[str] = None
    voice_line: Optional[VoiceLine] = None
    trace_spans: list[TraceSpan] = []
    evaluators: list[Evaluator] = []

"""Single-use, expiring, hash-bound approvals (build spec sections 3 and 4)."""

from datetime import datetime, timedelta
from uuid import uuid4

from api.contracts import ApprovalArtifact, ReasonCode, TypedAction

TTL_SECONDS = 300  # matches the front end's approval window


def issue(action: TypedAction, actor_id: str, now: datetime, decision: str = "approve",
          approval_id: str | None = None) -> ApprovalArtifact:
    return ApprovalArtifact(
        approval_id=approval_id or f"apv_{uuid4().hex[:8]}",
        actor_id=actor_id,
        action_hash=action.action_hash,
        issued_at=now,
        expires_at=now + timedelta(seconds=TTL_SECONDS),
        decision=decision,
    )


def check(approval: ApprovalArtifact, action: TypedAction, now: datetime) -> list[ReasonCode]:
    """Every reason this approval can't authorize this action. Empty list = valid."""
    reasons: list[ReasonCode] = []
    if approval.decision != "approve":
        reasons.append("APPROVAL_REJECTED")
    if approval.action_hash != action.action_hash:
        reasons.append("HASH_MISMATCH")
    if approval.used:
        reasons.append("APPROVAL_ALREADY_USED")
    if now >= approval.expires_at:
        reasons.append("APPROVAL_EXPIRED")
    return reasons


def consume(approval: ApprovalArtifact) -> ApprovalArtifact:
    return approval.model_copy(update={"used": True})

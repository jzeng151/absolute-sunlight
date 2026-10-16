"""Approvals are hash-bound, single-use and expiring. Case C4 lives here."""

from datetime import datetime, timedelta, timezone

from api import approvals
from api.contracts import TypedAction
from api.parser import parse

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
SOON = NOW + timedelta(seconds=30)


def act(case_id):
    return parse(case_id, "run_test", "operator_1")


def test_valid_approval_passes():
    a = act("C2")
    assert approvals.check(approvals.issue(a, "operator_1", NOW), a, SOON) == []


def test_c4_replay_after_c2_executed():
    used = approvals.consume(approvals.issue(act("C2"), "operator_1", NOW))
    assert approvals.check(used, act("C4"), SOON) == ["HASH_MISMATCH", "APPROVAL_ALREADY_USED"]


def test_changed_parameter_voids_approval():
    apr = approvals.issue(act("C2"), "operator_1", NOW)
    assert approvals.check(apr, act("C4"), SOON) == ["HASH_MISMATCH"]


def test_approval_expires():
    a = act("C2")
    late = NOW + timedelta(seconds=approvals.TTL_SECONDS)
    assert approvals.check(approvals.issue(a, "operator_1", NOW), a, late) == ["APPROVAL_EXPIRED"]


def test_rejection_never_authorizes():
    a = act("C2")
    apr = approvals.issue(a, "operator_1", NOW, decision="reject")
    assert approvals.check(apr, a, SOON) == ["APPROVAL_REJECTED"]


def test_hash_ignores_identity_and_key_order():
    base = act("C2").model_dump()
    a = TypedAction(**{**base, "normalized_parameters": {"delta_percent": 2, "unit": "pct"}})
    b = TypedAction(**{**base, "run_id": "other", "actor_id": "someone_else",
                       "normalized_parameters": {"unit": "pct", "delta_percent": 2}})
    assert a.action_hash == b.action_hash


def test_supplied_hash_is_ignored():
    assert TypedAction(**{**act("C2").model_dump(), "action_hash": "deadbeef"}).action_hash != "deadbeef"

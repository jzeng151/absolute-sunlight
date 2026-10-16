"""Deterministic core: fixtures, action hash, policy. No network, no model.

The policy engine is the only thing that decides GREEN / YELLOW / RED.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import config

CASES_FILE = json.loads((config.FIXTURES / "cases.json").read_text())
RULEBOOK: list[dict[str, Any]] = json.loads((config.FIXTURES / "rulebook.json").read_text())
BASELINE: list[dict[str, Any]] = json.loads((config.FIXTURES / "baseline.json").read_text())
REACTOR: dict[str, Any] = CASES_FILE["reactor"]
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in CASES_FILE["cases"]}
RULES: dict[str, dict[str, Any]] = {r["rule_id"]: r for r in RULEBOOK}
POLICY_VERSION = RULEBOOK[0]["policy_version"]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def action_hash(tool: str, target: str, normalized_parameters: dict, expected_postcondition: dict) -> str:
    """SHA-256 of canonical JSON. Identical to web/src/api/hash.ts."""
    payload = {
        "tool": tool,
        "target": target,
        "normalized_parameters": normalized_parameters,
        "expected_postcondition": expected_postcondition,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def state_checksum(state: dict) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()[:6]


def decide(action: dict) -> tuple[str, str, str]:
    """Return (state, reason_code, rule_id) for a typed action. Pure and deterministic.

    Order matters: hard limits first, then authority, then approval.
    """
    tool = action.get("tool")
    params = action.get("normalized_parameters") or {}
    post = action.get("expected_postcondition") or {}

    if tool == "disable_safety_function":
        return "RED", "SAFETY_BYPASS_FORBIDDEN", "NC-05"
    if tool == "analyze_history" and action.get("intent") == "read":
        return "GREEN", "READ_ONLY_ALLOWED", "NC-01"
    if tool == "adjust_output":
        target = post.get("output_percent")
        if not isinstance(target, (int, float)) or "delta_percent" not in params:
            return "RED", "NO_AUTHORITY", "NC-06"
        if target > REACTOR["band_max"] or target < REACTOR["band_min"]:
            return "RED", "BOUNDARY_EXCEEDED", "NC-04"
        return "YELLOW", "HUMAN_APPROVAL_REQUIRED", "NC-03"
    return "RED", "NO_AUTHORITY", "NC-06"


def typed_action_from_case(case: dict, run_id: str, actor_id: str, confidence: float = 1.0) -> dict:
    params = case["normalized_parameters"]
    post = case["expected_postcondition"]
    return {
        "run_id": run_id,
        "actor_id": actor_id,
        "tool": case["tool"],
        "target": REACTOR["target"],
        "normalized_parameters": params,
        "intent": case["intent"],
        "expected_postcondition": post,
        "confidence": confidence,
        "action_hash": action_hash(case["tool"], REACTOR["target"], params, post),
    }

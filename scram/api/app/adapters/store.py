"""In-memory store: rulebook, runs, approvals, checkpoints and an append-only audit log.

Same interface a database-backed store implements, including the run -> rule link that
answers "which rule authorized this action?". Everything lasts as long as the process.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


class Store:
    kind = "in-memory"
    url = "process"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rules: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.approvals: dict[str, dict[str, Any]] = {}
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self.audit_log: list[dict[str, Any]] = []

    def seed_rules(self, rules: list[dict[str, Any]]) -> None:
        with self._lock:
            for r in rules:
                self.rules[r["rule_id"]] = dict(r)

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.rules.get(rule_id)
        return dict(row) if row else None

    def save_run(self, run_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.runs[run_id] = dict(data)

    def link_rule(self, run_id: str, rule_id: str, policy: str, reason: str) -> None:
        with self._lock:
            self.links.append({"run": run_id, "rule": rule_id, "policy": policy, "reason_code": reason})

    def save_approval(self, approval: dict[str, Any]) -> None:
        with self._lock:
            self.approvals[approval["approval_id"]] = dict(approval)

    def save_checkpoint(self, cp: dict[str, Any]) -> None:
        with self._lock:
            self.checkpoints[cp["checkpoint_id"]] = dict(cp)

    def audit(self, run_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        """Append-only. A terminal state may not reset until its audit record is written."""
        with self._lock:
            self.audit_log.append({"run": run_id, "event": event, "detail": detail or {}, "at": datetime.now(timezone.utc).isoformat()})

    def rules_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(self.rules[l["rule"]]) for l in self.links if l["run"] == run_id and l["rule"] in self.rules]

    def wipe_runs(self) -> None:
        with self._lock:
            self.runs.clear()
            self.links.clear()
            self.approvals.clear()
            self.checkpoints.clear()
            self.audit_log.clear()


def make_store() -> Store:
    return Store()

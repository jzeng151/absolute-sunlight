"""SurrealDB store: rulebook, runs, approvals, checkpoints and an append-only audit log.

Runs are linked to the rules that governed them with a graph edge:
    run:<id> ->governed_by-> rule:<NC_0x>
so "which rule authorized this action?" is one query:
    SELECT ->governed_by->rule.* FROM run:<id>

SURREAL_URL=mem:// (the default) runs SurrealDB embedded in this process — still SurrealDB,
no server needed. Point it at ws://… or Surreal Cloud for a shared instance.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from .. import config

log = logging.getLogger("scram.store")


def _key(rule_id: str) -> str:
    return rule_id.replace("-", "_")


class Store:
    def __init__(self) -> None:
        from surrealdb import RecordID, Surreal

        self._RecordID = RecordID
        self._lock = threading.RLock()
        self.url = config.SURREAL_URL
        self.db = Surreal(self.url)
        self.db.connect()
        if config.SURREAL_USER:
            self.db.signin({"username": config.SURREAL_USER, "password": config.SURREAL_PASS})
        self.db.use(config.SURREAL_NS, config.SURREAL_DB)
        self.kind = "embedded" if self.url.startswith("mem") else "server"

    def rid(self, table: str, key: str):
        return self._RecordID(table, key)

    def seed_rules(self, rules: list[dict[str, Any]]) -> None:
        with self._lock:
            for r in rules:
                self.db.upsert(self.rid("rule", _key(r["rule_id"])), dict(r))

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.select(self.rid("rule", _key(rule_id)))
        if isinstance(row, list):
            row = row[0] if row else None
        if not isinstance(row, dict):
            return None
        row = {k: v for k, v in row.items() if k != "id"}
        return row

    def save_run(self, run_id: str, data: dict[str, Any]) -> None:
        """Create the run, or merge fields into it. A plain upsert would replace the record and drop
        action_hash and policy on the next state change."""
        with self._lock:
            self.db.query("UPSERT $rid MERGE $data", {"rid": self.rid("run", run_id), "data": data})

    def link_rule(self, run_id: str, rule_id: str, policy: str, reason: str) -> None:
        with self._lock:
            self.db.insert_relation(
                "governed_by",
                {"in": self.rid("run", run_id), "out": self.rid("rule", _key(rule_id)), "policy": policy, "reason_code": reason},
            )

    def save_approval(self, approval: dict[str, Any]) -> None:
        with self._lock:
            self.db.upsert(self.rid("approval", approval["approval_id"]), approval)

    def save_checkpoint(self, cp: dict[str, Any]) -> None:
        with self._lock:
            self.db.upsert(self.rid("checkpoint", cp["checkpoint_id"]), cp)

    def audit(self, run_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        """Append-only. A terminal state may not reset until its audit record is written."""
        with self._lock:
            self.db.create(
                "audit",
                {"run": self.rid("run", run_id), "event": event, "detail": detail or {}, "at": datetime.now(timezone.utc).isoformat()},
            )

    def finalize_run(self, run_id: str, data: dict[str, Any]) -> None:
        """Commit terminal state and audit in one database transaction."""
        with self._lock:
            response = self.db.query_raw(
                "BEGIN TRANSACTION; UPSERT $rid MERGE $data; CREATE audit CONTENT $audit; COMMIT TRANSACTION;",
                {"rid": self.rid("run", run_id), "data": data,
                 "audit": {"run": self.rid("run", run_id), "event": data["machine_state"],
                           "detail": {"block_reasons": data["block_reasons"]},
                           "at": datetime.now(timezone.utc).isoformat()}},
            )
            self.db.check_response_for_error(response, "finalize_run")
            for result in response.get("result", []):
                self.db._check_query_result(result)

    def rules_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            # Bound as a record, never interpolated: a run_id like "x; DELETE run" must stay data.
            res = self.db.query("SELECT ->governed_by->rule.* AS rules FROM $run", {"run": self.rid("run", run_id)})
        try:
            rules = res[0]["rules"]
        except (IndexError, KeyError, TypeError):
            return []
        return [{k: (str(v) if k == "id" else v) for k, v in r.items()} for r in rules if isinstance(r, dict)]

    def wipe_runs(self) -> None:
        """Clears run state for a demo reset. The audit log is append-only and is never deleted."""
        with self._lock:
            for t in ("run", "approval", "checkpoint", "governed_by"):
                self.db.query(f"DELETE {t}")


def make_store() -> Store:
    try:
        return Store()
    except Exception:  # noqa: BLE001
        log.exception("SurrealDB at %s unavailable; falling back to embedded mem://", config.SURREAL_URL)
        config.SURREAL_URL = "mem://"
        config.SURREAL_USER = ""
        return Store()

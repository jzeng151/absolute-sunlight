"""The SCRAM state machine. Every endpoint returns a RunView (web/src/types.ts).

Allowed path:
  IDLE → PARSING → RULE_RETRIEVED → CLASSIFIED
  CLASSIFIED: GREEN → COMPLETED | YELLOW → AWAITING_APPROVAL | RED → BLOCKED
  AWAITING_APPROVAL: approved → CHECKPOINTING | rejected/expired/hash mismatch → BLOCKED
  CHECKPOINTING → EXECUTING → VERIFYING → COMPLETED | ROLLING_BACK → RESTORED | LOCKED
Forbidden transitions raise ForbiddenTransition and are logged.
"""
from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any
from uuid import uuid4

from . import config, core
from .adapters import others, trace
from .adapters.sandbox import make_sandbox
from .adapters.store import make_store

log = logging.getLogger("scram.engine")
ACTOR = "operator_nw"

ALLOWED = {
    "CLASSIFIED": {"COMPLETED", "AWAITING_APPROVAL", "BLOCKED"},
    "AWAITING_APPROVAL": {"CHECKPOINTING", "BLOCKED"},
    "CHECKPOINTING": {"EXECUTING", "BLOCKED"},
    "EXECUTING": {"VERIFYING", "ROLLING_BACK"},
    "VERIFYING": {"COMPLETED", "ROLLING_BACK"},
    "ROLLING_BACK": {"RESTORED", "LOCKED"},
}


class ForbiddenTransition(RuntimeError):
    pass


class NotFound(KeyError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _span(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class Engine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.transaction_lock = threading.Lock()
        self.writes_locked = False
        self.store = make_store()
        self.store.seed_rules(core.RULEBOOK)
        self.sandbox = make_sandbox()
        self.fault = True
        self.counter = 417
        self.runs: dict[str, dict[str, Any]] = {}
        self.traces: dict[str, trace.RunTrace] = {}
        self.used_approval_hashes: set[str] = set()
        self.output = core.REACTOR["output_percent"]
        self.sandbox_ready = threading.Event()
        threading.Thread(target=self._init_sandbox, daemon=True).start()

    # ---------- setup ----------

    def _initial_state(self) -> dict[str, Any]:
        return {"target": core.REACTOR["target"], "output_percent": core.REACTOR["output_percent"]}

    def _init_sandbox(self) -> None:
        try:
            st = self.sandbox.write_state(self._initial_state())
            self.output = st["output_percent"]
            log.info("sandbox ready (%s)", self.sandbox.name)
        except Exception:  # noqa: BLE001
            log.exception("sandbox init failed; switching to local sandbox")
            from .adapters.sandbox import LocalSandbox

            self.sandbox = LocalSandbox()
            self.sandbox.write_state(self._initial_state())
        finally:
            self.sandbox_ready.set()

    def health(self) -> dict[str, Any]:
        return {
            "daytona": self.sandbox.name,
            "sandbox_id": self.sandbox.run_id,
            "arize": "on" if trace.enabled else "local only",
            "surrealdb": f"{self.store.kind} ({self.store.url})",
            "elevenlabs": "cached clips only",
            "anchor": "off",
            "model": "prepared actions",
            "fault": self.fault,
        }

    # ---------- helpers ----------

    def _move(self, run: dict, to: str) -> None:
        frm = run["machine_state"]
        if frm in ALLOWED and to not in ALLOWED[frm]:
            log.error("forbidden transition %s -> %s on %s", frm, to, run["run_id"])
            raise ForbiddenTransition(f"{frm} → {to} is not allowed")
        if to in {"COMPLETED", "BLOCKED", "RESTORED", "LOCKED"}:
            run["_final_state"] = to
            return
        run["machine_state"] = to
        self.store.save_run(run["run_id"], {"machine_state": to, "scenario_id": run["scenario_id"]})

    def _get(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if not run:
            raise NotFound(run_id)
        return run

    def _evaluate(self, run: dict) -> list[dict[str, Any]]:
        ex = run["execution"]
        ver = run["verification"]
        rb = run["rollback"]
        order = [s["name"].split(" ")[0] for s in run["trace_spans"]]
        idx = {n: i for i, n in enumerate(order)}
        required = ["policy", "approval", "checkpoint", "execute", "verify"]
        seq_ok = ex is None or (all(n in idx for n in required)
                               and all(idx[a] < idx[b] for a, b in zip(required, required[1:])))
        approval = run["approval"]
        authorized = bool(approval and approval["decision"] == "approve" and approval["used"]
                          and approval["action_hash"] == run["action"]["action_hash"]
                          and run.get("_authorized_at_write", False))
        results = [
            {"name": "Unauthorized execution", "description": "RED or invalid approval never reaches a tool.",
             "pass": ex is None or (run["decision"]["state"] == "YELLOW" and authorized)},
            {"name": "Approval integrity", "description": "Matching, unexpired, unused hash.",
             "pass": ex is None or authorized},
            {"name": "Rollback verification", "description": "Failed postcondition ends restored.",
             "pass": bool(rb["verified"]) if rb else (ex is None or bool(ver and ver["pass"]))},
            {"name": "Trajectory order", "description": "No step skipped or out of sequence.", "pass": seq_ok},
        ]
        return results

    def _trace(self, tr, method: str, *args, **kwargs) -> None:
        try:
            getattr(tr, method)(*args, **kwargs)
        except Exception:
            log.exception("trace export failed during %s", method)

    @synchronized
    def _finish(self, run: dict) -> None:
        final = run.pop("_final_state", run["machine_state"])
        result = deepcopy(run)
        result["machine_state"] = final
        result["evaluators"] = self._evaluate(result)
        tr = self.traces.get(run["run_id"])
        try:
            self.store.save_run(run["run_id"], result)
            self.store.audit(run["run_id"], final, {"block_reasons": run["block_reasons"]})
        except Exception:
            log.exception("could not finalize audit for %s", run["run_id"])
            self.writes_locked = True
            result["machine_state"] = "LOCKED" if run["execution"] else "BLOCKED"
            result["block_reasons"] = ["CONTROL_UNAVAILABLE"]
            self._set_span(result, "audit", "error", "audit unavailable; writes locked")
        else:
            self._set_span(result, "audit", "ok", "recorded")
        if tr:
            self._trace(tr, "step", "audit", "CHAIN", recorded=result["machine_state"] == final)
            self._trace(tr, "evals", result["evaluators"])
            self._trace(tr, "close", result["machine_state"])
        # Readers cannot observe terminal state before its evidence is complete.
        run.update(result)

    @synchronized
    def view(self, run: dict) -> dict[str, Any]:
        v = deepcopy({k: val for k, val in run.items() if not k.startswith("_")})
        v["current_output_percent"] = self.output
        return v

    # ---------- endpoints ----------

    @synchronized
    def create_run(self, case_id: str) -> dict[str, Any]:
        if case_id not in core.CASES:
            raise NotFound(case_id)
        case = core.CASES[case_id]
        with self.lock:
            self.counter += 1
            run_id = f"run_{uuid4().hex}"
        tr = trace.RunTrace(run_id, case_id)
        self.traces[run_id] = tr

        run: dict[str, Any] = {
            "run_id": run_id, "scenario_id": case_id, "machine_state": "PARSING", "request_text": case["request_text"],
            "current_output_percent": self.output, "action": None, "rule": None, "decision": None,
            "approval_expires_at": None, "approval": None, "checkpoint": None, "execution": None,
            "verification": None, "rollback": None, "block_reasons": [], "approved_hash": None,
            "voice_line": None, "trace_spans": [_span("request", "ok")], "evaluators": [],
        }
        self.runs[run_id] = run

        # 1. Normalize. The prepared typed action is the contract.
        run["action"] = core.typed_action_from_case(case, run_id, ACTOR, current_output=self.output)
        tr.step("normalize", "CHAIN", **{"input.value": case["request_text"], "output.value": str(run["action"]), "scram.confidence": 1.0, "llm.model_name": "prepared"})
        run["trace_spans"].append(_span("normalize", "ok", "prepared"))

        # 2. Rule and policy — deterministic, never the model.
        state, reason, rule_id = core.decide(run["action"])
        run["machine_state"] = "RULE_RETRIEVED"
        run["rule"] = self.store.get_rule(rule_id) or core.RULES[rule_id]
        run["trace_spans"].append(_span(f"rule · {rule_id}", "ok"))
        run["machine_state"] = "CLASSIFIED"
        run["decision"] = {"run_id": run_id, "policy_version": core.POLICY_VERSION, "rule_ids": [rule_id], "state": state,
                           "reason_code": reason, "reversible": run["action"]["intent"] == "write", "approval_required": state == "YELLOW"}
        self.store.save_run(run_id, {"scenario_id": case_id, "machine_state": "CLASSIFIED", "action_hash": run["action"]["action_hash"], "policy": state})
        self.store.link_rule(run_id, rule_id, state, reason)
        tr.step("policy", "GUARDRAIL", ok=state != "RED", **{"scram.rule_id": rule_id, "scram.policy": state, "scram.reason_code": reason})
        run["trace_spans"].append(_span(f"policy · {state}", "error" if state == "RED" else "ok"))

        if state == "GREEN":
            tr.step("read", "TOOL", **{"tool.name": "analyze_history"})
            run["trace_spans"].append(_span("read", "ok", "no write"))
            self._move(run, "COMPLETED")
            run["trace_spans"].append(_span("audit", "ok", "recorded"))
            self._finish(run)
            return self.view(run)

        if state == "RED":
            self._move(run, "BLOCKED")
            run["block_reasons"] = [reason]
            run["voice_line"] = others.VOICE_LINES["red"]
            run["trace_spans"] += [_span("execute", "skipped", "never"), _span("audit", "ok", "recorded")]
            self._finish(run)
            return self.view(run)

        if case_id == "C4":
            # The operator changed +2 % to +3 % and tried to reuse the earlier approval.
            c2_hash = core.typed_action_from_case(core.CASES["C2"], run_id, ACTOR, current_output=self.output)["action_hash"]
            run["approved_hash"] = c2_hash
            reasons = ["HASH_MISMATCH"] + (["APPROVAL_ALREADY_USED"] if c2_hash in self.used_approval_hashes else [])
            self._move(run, "BLOCKED")
            run["block_reasons"] = reasons
            run["voice_line"] = others.VOICE_LINES["replay"]
            tr.step("approval_check", "GUARDRAIL", ok=False, **{"scram.approved_hash": c2_hash, "scram.presented_hash": run["action"]["action_hash"], "scram.reasons": ",".join(reasons)})
            run["trace_spans"] += [_span("approval check", "error", "hash mismatch"), _span("execute", "skipped", "never"), _span("audit", "ok", "recorded")]
            self._finish(run)
            return self.view(run)

        self._move(run, "AWAITING_APPROVAL")
        run["approval_expires_at"] = (_now() + timedelta(seconds=config.APPROVAL_TTL_SECONDS)).isoformat()
        run["voice_line"] = others.VOICE_LINES["yellow"]
        run["trace_spans"].append(_span("approval", "running", "waiting"))
        return self.view(run)

    @synchronized
    def reject(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        if run["machine_state"] != "AWAITING_APPROVAL":
            raise ForbiddenTransition("run is not awaiting approval")
        self._move(run, "BLOCKED")
        run["block_reasons"] = ["APPROVAL_REJECTED"]
        run["voice_line"] = None
        run["approval"] = {"approval_id": f"apv_{run_id}", "actor_id": ACTOR, "action_hash": run["action"]["action_hash"],
                           "issued_at": _now().isoformat(), "expires_at": run["approval_expires_at"], "used": True, "decision": "reject"}
        self.store.save_approval(run["approval"])
        self.traces[run_id].step("approval", "GUARDRAIL", ok=False, **{"scram.decision": "reject"})
        run["trace_spans"][-1] = _span("approval", "error", "rejected")
        run["trace_spans"].append(_span("audit", "ok", "recorded"))
        self._finish(run)
        return self.view(run)

    @synchronized
    def approve(self, run_id: str, actor_id: str, presented_hash: str | None) -> dict[str, Any]:
        run = self._get(run_id)
        if run["machine_state"] != "AWAITING_APPROVAL":
            raise ForbiddenTransition("run is not awaiting approval")
        tr = self.traces[run_id]
        expected = run["action"]["action_hash"]
        expired = _now() > datetime.fromisoformat(run["approval_expires_at"])
        if presented_hash != expected or expired or self.writes_locked:
            reason = "ROLLBACK_FAILED" if self.writes_locked else "APPROVAL_EXPIRED" if expired else "HASH_MISMATCH"
            self._move(run, "BLOCKED")
            run["block_reasons"] = [reason]
            run["approved_hash"] = presented_hash
            tr.step("approval", "GUARDRAIL", ok=False, **{"scram.reason": reason})
            run["trace_spans"][-1] = _span("approval", "error", reason.lower())
            run["trace_spans"].append(_span("audit", "ok", "recorded"))
            self._finish(run)
            return self.view(run)

        run["approval"] = {"approval_id": f"apv_{run_id}", "actor_id": actor_id, "action_hash": expected,
                           "issued_at": _now().isoformat(), "expires_at": run["approval_expires_at"], "used": True, "decision": "approve"}
        self.used_approval_hashes.add(expected)
        self.store.save_approval(run["approval"])
        tr.step("approval", "GUARDRAIL", **{"scram.approver": actor_id, "scram.action_hash": expected})
        run["trace_spans"][-1] = _span("approval", "ok", expected)
        run["trace_spans"] += [_span("checkpoint", "running"), _span("execute", "pending"), _span("verify", "pending")]
        run["voice_line"] = others.VOICE_LINES["approved"]
        self._move(run, "CHECKPOINTING")
        threading.Thread(target=self._execute, args=(run, self.fault), daemon=True).start()
        return self.view(run)

    @synchronized
    def get(self, run_id: str) -> dict[str, Any]:
        return self.view(self._get(run_id))

    # ---------- execution (background) ----------

    def _set_span(self, run: dict, name: str, status: str, detail: str = "") -> None:
        for i, s in enumerate(run["trace_spans"]):
            if s["name"] == name:
                run["trace_spans"][i] = _span(name, status, detail)
                return
        run["trace_spans"].append(_span(name, status, detail))

    def _execute(self, run: dict, fault: bool) -> None:
        with self.transaction_lock:
            with self.lock:
                # Reset can discard a queued worker before it acquires the transaction.
                if self.runs.get(run["run_id"]) is not run:
                    return
            self._execute_transaction(run, fault)

    def _execute_transaction(self, run: dict, fault: bool) -> None:
        tr = self.traces[run["run_id"]]
        expected = run["action"]["expected_postcondition"]["output_percent"]
        delta = run["action"]["normalized_parameters"]["delta_percent"]
        wrote = False
        before: dict[str, Any] = {}
        try:
            if self.writes_locked:
                raise ForbiddenTransition("writes locked until reset")
            if not self.sandbox_ready.wait(timeout=180):
                raise TimeoutError("sandbox initialization is still pending")
            # Checkpoint: read the state inside the sandbox and store the copy.
            t = time.time()
            before = self.sandbox.read_state()
            if before["output_percent"] + delta != expected:
                raise ForbiddenTransition("reactor state changed; request a fresh approval")
            cp = {"checkpoint_id": f"ckpt_{run['run_id']}", "run_id": run["run_id"], "before_state": {"output_percent": before["output_percent"]}, "checksum": core.state_checksum(before)}
            self.store.save_checkpoint({**cp, "full_state": before})
            run["checkpoint"] = cp
            tr.step("checkpoint", "TOOL", **{"scram.checksum": cp["checksum"], "scram.sandbox": self.sandbox.name})
            self._set_span(run, "checkpoint", "ok", cp["checkpoint_id"])
            self._pace(t)

            # Re-check the approval hash immediately before changing state.
            self.store.audit(run["run_id"], "WRITE_INTENT", {"action_hash": run["action"]["action_hash"], "checkpoint_id": cp["checkpoint_id"]})
            approval = run["approval"]
            if _now() >= datetime.fromisoformat(approval["expires_at"]):
                run["block_reasons"] = ["APPROVAL_EXPIRED"]
                raise ForbiddenTransition("approval expired before execution")
            if (approval["action_hash"] != run["action"]["action_hash"]
                    or approval["decision"] != "approve" or not approval["used"]
                    or core.decide(run["action"])[0] != "YELLOW"):
                raise ForbiddenTransition("approval hash changed before execution")
            self._move(run, "EXECUTING")
            self._set_span(run, "execute", "running")
            t = time.time()
            run["_authorized_at_write"] = True
            # A remote timeout can happen after the write has taken effect.
            wrote = True
            run["execution"] = {"tool": run["action"]["tool"], "target": run["action"]["target"], "parameters": run["action"]["normalized_parameters"],
                                "status": "error", "resulting_state": {"output_percent": before["output_percent"]}, "executor_run_id": f"sbx_{self.sandbox.run_id}", "fault_injected": fault}
            after = self.sandbox.adjust_output(delta, fault)
            self.output = after["output_percent"]
            run["execution"] = {"tool": run["action"]["tool"], "target": run["action"]["target"], "parameters": run["action"]["normalized_parameters"],
                                "status": "success", "resulting_state": {"output_percent": after["output_percent"]}, "executor_run_id": f"sbx_{self.sandbox.run_id}", "fault_injected": fault}
            tr.step("execute", "TOOL", **{"tool.name": "adjust_output", "scram.sandbox": self.sandbox.name, "scram.fault_injected": fault, "scram.tool_status": "success"})
            self._set_span(run, "execute", "ok", self.sandbox.name.capitalize())
            self._pace(t)

            # Verify against the expected postcondition, reading back from the sandbox.
            self._move(run, "VERIFYING")
            self._set_span(run, "verify", "running")
            t = time.time()
            actual = self.sandbox.read_state()["output_percent"]
            ok = actual == expected
            run["verification"] = {"expected_postcondition": {"output_percent": expected}, "actual_state": {"output_percent": actual},
                                   "pass": ok, "reason_code": None if ok else "POSTCONDITION_FAILED", "diff": "0" if ok else f"{actual - expected:+g}"}
            tr.step("verify", "EVALUATOR", ok=ok, **{"scram.expected": expected, "scram.actual": actual})
            self._set_span(run, "verify", "ok" if ok else "error", "pass" if ok else f"{actual:g} ≠ {expected:g}")
            self._pace(t)

            if ok:
                self._move(run, "COMPLETED")
                run["voice_line"] = None
                self._set_span(run, "audit", "ok", "recorded")
                self._finish(run)
                return
            self._rollback(run, before)
        except Exception as e:  # noqa: BLE001
            log.exception("execution error on %s", run["run_id"])
            if wrote and before:
                self._rollback(run, before)
            else:
                run["_final_state"] = "BLOCKED"
                run["block_reasons"] = run["block_reasons"] or ["CONTROL_UNAVAILABLE"]
                run["voice_line"] = None
                self._set_span(run, "audit", "ok", f"blocked: {type(e).__name__}")
                self._finish(run)

    def _rollback(self, run: dict, before: dict) -> None:
        tr = self.traces[run["run_id"]]
        run["machine_state"] = "ROLLING_BACK"
        run["rollback"] = {"triggered": True, "checkpoint_id": run["checkpoint"]["checkpoint_id"], "status": "running", "restored_checksum": "", "verified": False}
        self._set_span(run, "rollback", "running")
        t = time.time()
        try:
            self.sandbox.write_state(before)
            restored = self.sandbox.read_state()
            self.output = restored["output_percent"]
            checksum = core.state_checksum(restored)
            verified = checksum == run["checkpoint"]["checksum"]
        except Exception:  # noqa: BLE001
            log.exception("rollback failed")
            checksum, verified = "", False
        self._pace(t)
        run["rollback"].update(status="restored" if verified else "failed", restored_checksum=checksum, verified=verified)
        self._trace(tr, "step", "rollback", "TOOL", ok=verified, **{"scram.checksum_before": run["checkpoint"]["checksum"], "scram.checksum_after": checksum})
        self._set_span(run, "rollback", "ok" if verified else "error", "verified" if verified else "failed")
        run["_final_state"] = "RESTORED" if verified else "LOCKED"
        if not verified:
            self.writes_locked = True
        run["voice_line"] = others.VOICE_LINES["restored" if verified else "locked"]
        self._set_span(run, "audit", "ok", "recorded")
        self._finish(run)

    @staticmethod
    def _pace(started: float) -> None:
        left = config.STEP_SECONDS - (time.time() - started)
        if left > 0:
            time.sleep(left)

    # ---------- controls ----------

    def set_fault(self, enabled: bool) -> None:
        self.fault = bool(enabled)

    def reset(self) -> None:
        with self.transaction_lock, self.lock:
            if not self.sandbox_ready.wait(timeout=180):
                raise ForbiddenTransition("sandbox initialization is still pending")
            self.writes_locked = True
            initial = self._initial_state()
            self.sandbox.write_state(initial)
            restored = self.sandbox.read_state()
            if restored != initial:
                raise ForbiddenTransition("reset state could not be verified")
            self.store.audit("reset", "RESET", {"output_percent": restored["output_percent"]})
            for tr in self.traces.values():
                self._trace(tr, "close", "RESET")
            self.runs.clear()
            self.traces.clear()
            self.used_approval_hashes.clear()
            self.fault = True
            self.store.wipe_runs()
            self.output = restored["output_percent"]
            self.writes_locked = False

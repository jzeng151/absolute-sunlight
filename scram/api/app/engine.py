"""The SCRAM state machine. Every endpoint returns a RunView (web/src/types.ts).

Allowed path:
  IDLE → PARSING → RULE_RETRIEVED → CLASSIFIED
  CLASSIFIED: GREEN → COMPLETED | YELLOW → AWAITING_APPROVAL | RED → BLOCKED
  AWAITING_APPROVAL: approved → CHECKPOINTING | rejected/expired/hash mismatch → BLOCKED
  CHECKPOINTING → EXECUTING → VERIFYING → COMPLETED | ROLLING_BACK → RESTORED | LOCKED
  EXECUTING → BLOCKED only when the final dispatch gate aborts before any tool call
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
from .evidence import checksum as evidence_checksum
from .adapters import others, trace
from .adapters.sandbox import make_sandbox
from .adapters.store import make_store

log = logging.getLogger("scram.engine")
ACTOR = "operator_nw"

ALLOWED = {
    "CLASSIFIED": {"COMPLETED", "AWAITING_APPROVAL", "BLOCKED"},
    "AWAITING_APPROVAL": {"CHECKPOINTING", "BLOCKED"},
    "CHECKPOINTING": {"EXECUTING", "BLOCKED"},
    "EXECUTING": {"VERIFYING", "ROLLING_BACK", "BLOCKED"},
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


def accepting_work(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        if self.closed:
            raise ForbiddenTransition("engine is shutting down")
        with self.lock:
            if self.closed:
                raise ForbiddenTransition("engine is shutting down")
            return method(self, *args, **kwargs)
    return call


class Engine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.transaction_lock = threading.Lock()
        self.closed = False
        self._workers: set[threading.Thread] = set()
        self.write_lock_reason: str | None = None
        self.store = make_store()
        self.store.seed_rules(core.RULEBOOK)
        self.sandbox = make_sandbox()
        self.pending_sandbox_cleanup: list[Any] = []
        self.fault = True
        self.counter = 417
        self.runs: dict[str, dict[str, Any]] = {}
        self.traces: dict[str, trace.RunTrace] = {}
        self.used_approval_hashes: set[str] = set()
        self.last_c2_approval_hash: str | None = None
        self.pending_finalizations: dict[str, dict] = {}
        self.output = core.REACTOR["output_percent"]
        self.sandbox_error: str | None = None
        self.verified_output = self.output
        self.sandbox_ready = threading.Event()
        self._initializer = threading.Thread(target=self._init_sandbox, daemon=True)
        self._initializer.start()

    # ---------- setup ----------

    def _initial_state(self) -> dict[str, Any]:
        return {"target": core.REACTOR["target"], "output_percent": core.REACTOR["output_percent"]}

    def _init_sandbox(self) -> None:
        try:
            st = self.sandbox.write_state(self._initial_state())
            self.output = st["output_percent"]
            self.verified_output = self.output
            log.info("sandbox ready (%s)", self.sandbox.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("sandbox initialization failed (%s)", type(exc).__name__)
            from .adapters.sandbox import LocalSandbox

            self._close_sandbox()
            if config.SCRAM_EXECUTOR == "daytona":
                self.sandbox_error = "DAYTONA_INITIALIZATION_FAILED"
                self.write_lock_reason = "CONTROL_UNAVAILABLE"
            else:
                self.sandbox = LocalSandbox("DAYTONA_INITIALIZATION_FAILED")
                self.sandbox.write_state(self._initial_state())
        finally:
            if self.closed:
                self._close_sandbox()
            self.sandbox_ready.set()

    def health(self) -> dict[str, Any]:
        return {
            "daytona": self.sandbox.name if self.sandbox_ready.is_set() else "initializing",
            "sandbox_id": self.sandbox.run_id,
            "sandbox_ready": self.sandbox_ready.is_set() and self.sandbox_error is None and not self.closed,
            "sandbox_error": self.sandbox_error,
            "sandbox_fallback_reason": getattr(self.sandbox, "fallback_reason", None),
            "writes_locked": self.writes_locked,
            "arize": "on" if trace.enabled else "local only",
            "surrealdb": f"{self.store.kind} ({self.store.url})",
            "elevenlabs": "key set" if config.ELEVENLABS_API_KEY else "cached clips only",
            "anchor": "off",
            "model": config.GEMINI_MODEL if config.GEMINI_API_KEY else "prepared actions",
            "fault": self.fault,
        }

    # ---------- helpers ----------

    @property
    def writes_locked(self) -> bool:
        return self.write_lock_reason is not None

    @synchronized
    def _update_run(self, run: dict, **data) -> None:
        run.update(data)

    @synchronized
    def _move(self, run: dict, to: str) -> None:
        if run.get("_final_state") is not None:
            raise ForbiddenTransition("run already has a terminal decision")
        frm = run["machine_state"]
        if frm in ALLOWED and to not in ALLOWED[frm]:
            log.error("forbidden transition %s -> %s on %s", frm, to, run["run_id"])
            raise ForbiddenTransition(f"{frm} → {to} is not allowed")
        if to in {"COMPLETED", "BLOCKED", "RESTORED", "LOCKED"}:
            run["_final_state"] = to
            return
        self.store.save_run(run["run_id"], {"machine_state": to, "scenario_id": run["scenario_id"]})
        run["machine_state"] = to

    def _get(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if not run:
            raise NotFound(run_id)
        return run

    def _evaluate(self, run: dict) -> list[dict[str, Any]]:
        return self.traces[run["run_id"]].evaluate(run["machine_state"])

    def _trace(self, tr, method: str, *args, **kwargs) -> None:
        try:
            getattr(tr, method)(*args, **kwargs)
        except Exception:
            log.exception("trace export failed during %s", method)

    @synchronized
    def _finish(self, run: dict) -> None:
        final = run.get("_final_state", run["machine_state"])
        result = deepcopy(run)
        result["machine_state"] = final
        result["current_output_percent"] = self.output
        tr = self.traces.get(run["run_id"])
        recorded = False
        try:
            self._set_span(result, "audit", "ok", "recorded")
            self.store.finalize_run(run["run_id"], result)
        except Exception:
            log.exception("could not finalize audit for %s", run["run_id"])
            self.write_lock_reason = self.write_lock_reason or "CONTROL_UNAVAILABLE"
            result["machine_state"] = "LOCKED" if run["execution"] else "BLOCKED"
            result["block_reasons"] = list(dict.fromkeys(
                [*result["block_reasons"], self.write_lock_reason, "CONTROL_UNAVAILABLE"]))
            self._set_span(result, "audit", "error", "audit unavailable; writes locked")
            if "ROLLBACK_FAILED" not in result["block_reasons"]:
                result["voice_line"] = None
            self.pending_finalizations[run["run_id"]] = deepcopy(result)
        else:
            recorded = True
        if tr:
            self._trace(tr, "step", "audit", "CHAIN", recorded=recorded, evidence={"recorded": recorded})
            result["evaluators"] = self._evaluate(result)
            self._trace(tr, "evals", result["evaluators"])
            self._trace(tr, "close", result["machine_state"])
        # Readers cannot observe terminal state before its evidence is complete.
        run.update(result)
        run.pop("_final_state", None)

    @synchronized
    def view(self, run: dict) -> dict[str, Any]:
        v = deepcopy({k: val for k, val in run.items() if not k.startswith("_")})
        v["current_output_percent"] = self.output
        return v

    # ---------- endpoints ----------

    def create_run(self, case_id: str) -> dict[str, Any]:
        if self.closed:
            raise ForbiddenTransition("engine is shutting down")
        if case_id not in core.CASES:
            raise NotFound(case_id)
        # Model I/O must not hold the lock used by polling and finalization.
        reading = others.parse_with_model(core.CASES[case_id]["request_text"])
        return self._create_run(case_id, reading)

    @accepting_work
    def _create_run(self, case_id: str, reading) -> dict[str, Any]:
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

        # 1. Normalize the model's action; only no-model mode uses the fixture.
        using_model = reading is not None
        try:
            run["action"] = (core.typed_action_from_reading(reading, run_id, ACTOR, self.verified_output) if using_model
                             else core.typed_action_from_case(case, run_id, ACTOR, current_output=self.verified_output))
            confidence = run["action"]["confidence"]
        except ValueError:
            confidence = 0.0
        model = config.GEMINI_MODEL if using_model else "prepared"
        tr.step("normalize", "LLM" if using_model else "CHAIN", ok=confidence >= config.PARSE_MIN_CONFIDENCE,
                outcome="ok" if confidence >= config.PARSE_MIN_CONFIDENCE else "failed",
                evidence={"action": run["action"], "action_hash": run["action"]["action_hash"], "intent": run["action"]["intent"]} if run["action"] else {},
                **{"input.value": case["request_text"], "output.value": str(reading if using_model else run["action"]), "scram.confidence": confidence, "llm.model_name": model})
        run["trace_spans"].append(_span("normalize", "ok" if confidence >= config.PARSE_MIN_CONFIDENCE else "error", "Gemini" if using_model else "prepared"))
        if confidence < config.PARSE_MIN_CONFIDENCE:
            run["_final_state"] = "BLOCKED"
            run["block_reasons"] = ["PARSE_UNCERTAIN"]
            run["decision"] = {"run_id": run_id, "policy_version": core.POLICY_VERSION, "rule_ids": [], "state": "RED", "reason_code": "PARSE_UNCERTAIN", "reversible": False, "approval_required": False}
            self._finish(run)
            return self.view(run)

        # 2. Rule and policy — deterministic, never the model.
        state, reason, rule_id = core.decide(run["action"])
        run["machine_state"] = "RULE_RETRIEVED"
        run["rule"] = self.store.get_rule(rule_id) or core.RULES[rule_id]
        tr.step("rule.retrieve", "RETRIEVER", evidence={"rule_ids": [rule_id], "policy_version": core.POLICY_VERSION})
        run["trace_spans"].append(_span(f"rule · {rule_id}", "ok"))
        run["machine_state"] = "CLASSIFIED"
        run["decision"] = {"run_id": run_id, "policy_version": core.POLICY_VERSION, "rule_ids": [rule_id], "state": state,
                           "reason_code": reason, "reversible": run["action"]["intent"] == "write", "approval_required": state == "YELLOW"}
        self.store.save_run(run_id, {"scenario_id": case_id, "machine_state": "CLASSIFIED", "action_hash": run["action"]["action_hash"], "policy": state})
        self.store.link_rule(run_id, rule_id, state, reason)
        tr.step("policy", "GUARDRAIL", ok=state != "RED", outcome="blocked" if state == "RED" else "ok", evidence=run["decision"], **{"scram.rule_id": rule_id, "scram.policy": state, "scram.reason_code": reason})
        run["trace_spans"].append(_span(f"policy · {state}", "error" if state == "RED" else "ok"))

        if state == "GREEN":
            # Independently observe the read-only branch. Failure to observe is not proof of no mutation.
            read_evidence, read_outcome = {}, "ok"
            try:
                if not self.sandbox_ready.wait(timeout=180):
                    raise TimeoutError("sandbox initialization is still pending")
                read_evidence = {"state_before": self.sandbox.read_state(), "state_after": self.sandbox.read_state()}
            except Exception as error:
                read_evidence, read_outcome = {"error_type": type(error).__name__}, "error"
            tr.step("read", "TOOL", evidence=read_evidence, outcome=read_outcome, ok=read_outcome == "ok", **{"tool.name": "analyze_history"})
            run["trace_spans"].append(_span("read", read_outcome, "no write" if read_outcome == "ok" else "state observation unavailable"))
            self._move(run, "COMPLETED")
            run["trace_spans"].append(_span("audit", "ok", "recorded"))
            self._finish(run)
            return self.view(run)

        if state == "RED":
            self._move(run, "BLOCKED")
            run["block_reasons"] = [reason]
            run["voice_line"] = others.VOICE_LINES["red"] if reason == "BOUNDARY_EXCEEDED" else None
            run["trace_spans"] += [_span("execute", "skipped", "never"), _span("audit", "ok", "recorded")]
            self._finish(run)
            return self.view(run)

        if case_id == "C4":
            # The operator changed +2 % to +3 % and tried to reuse the earlier approval.
            c2_hash = self.last_c2_approval_hash or core.typed_action_from_case(core.CASES["C2"], run_id, ACTOR, current_output=self.verified_output)["action_hash"]
            run["approved_hash"] = c2_hash
            reasons = ["HASH_MISMATCH"] + (["APPROVAL_ALREADY_USED"] if c2_hash in self.used_approval_hashes else [])
            self._move(run, "BLOCKED")
            run["block_reasons"] = reasons
            run["voice_line"] = others.VOICE_LINES["replay"]
            prior = next((r for r in reversed(list(self.runs.values()))
                          if r["approval"] and r["approval"]["decision"] == "approve" and r["approval"]["action_hash"] == c2_hash), None)
            artifact = prior["approval"] if prior else None
            if prior:
                tr.step("approval", "GUARDRAIL", evidence={**artifact, "run_id": prior["run_id"]})
            tr.step("approval.check", "GUARDRAIL", ok=False, outcome="blocked", evidence={
                "approval_id": artifact["approval_id"] if artifact else "", "actor_id": ACTOR,
                "presented_hash": run["action"]["action_hash"], "checked_at": _now().isoformat(),
                "used_before": bool(artifact and artifact["used"]), "consumed": False, "valid": False, "reason_codes": reasons,
            }, **{"scram.approved_hash": c2_hash, "scram.presented_hash": run["action"]["action_hash"], "scram.reasons": ",".join(reasons)})
            run["trace_spans"] += [_span("approval check", "error", "hash mismatch"), _span("execute", "skipped", "never"), _span("audit", "ok", "recorded")]
            self._finish(run)
            return self.view(run)

        self._move(run, "AWAITING_APPROVAL")
        run["approval_expires_at"] = (_now() + timedelta(seconds=config.APPROVAL_TTL_SECONDS)).isoformat()
        # The cached clip describes exactly +2 to 74; other targets stay in
        # the approval panel without contradictory prerecorded narration.
        run["voice_line"] = (others.VOICE_LINES["yellow"]
                             if run["action"]["normalized_parameters"] == {"delta_percent": 2}
                             and run["action"]["expected_postcondition"] == {"output_percent": 74} else None)
        run["trace_spans"].append(_span("approval", "running", "waiting"))
        return self.view(run)

    @accepting_work
    def reject(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        if run["machine_state"] != "AWAITING_APPROVAL" or run.get("_final_state") is not None:
            raise ForbiddenTransition("run is not awaiting approval")
        self._move(run, "BLOCKED")
        run["block_reasons"] = ["APPROVAL_REJECTED"]
        run["voice_line"] = None
        run["approval"] = {"approval_id": f"apv_{run_id}", "actor_id": ACTOR, "action_hash": run["action"]["action_hash"],
                           "issued_at": _now().isoformat(), "expires_at": run["approval_expires_at"], "used": True, "decision": "reject"}
        try:
            self.store.save_approval(run["approval"])
        except Exception:
            log.exception("could not persist rejected approval for %s", run_id)
            self.write_lock_reason = self.write_lock_reason or "CONTROL_UNAVAILABLE"
            run["block_reasons"].append("CONTROL_UNAVAILABLE")
        # Finalization retains the rejected artifact in the terminal record,
        # or queues that entire record if the audit sink is unavailable too.
        self._trace(self.traces[run_id], "step", "approval", "GUARDRAIL", ok=False, outcome="blocked", evidence={**run["approval"], "run_id": run_id}, **{"scram.decision": "reject"})
        run["trace_spans"][-1] = _span("approval", "error", "rejected")
        run["trace_spans"].append(_span("audit", "ok", "recorded"))
        self._finish(run)
        return self.view(run)

    @accepting_work
    def approve(self, run_id: str, actor_id: str, presented_hash: str | None) -> dict[str, Any]:
        run = self._get(run_id)
        if run["machine_state"] != "AWAITING_APPROVAL" or run.get("_final_state") is not None:
            raise ForbiddenTransition("run is not awaiting approval")
        tr = self.traces[run_id]
        expected = run["action"]["action_hash"]
        checked_at = _now()
        expired = checked_at >= datetime.fromisoformat(run["approval_expires_at"])
        if presented_hash != expected or expired or self.writes_locked:
            reason = self.write_lock_reason or ("APPROVAL_EXPIRED" if expired else "HASH_MISMATCH")
            self._move(run, "BLOCKED")
            reasons = (["HASH_MISMATCH"] if presented_hash != expected else []) + (["APPROVAL_EXPIRED"] if expired else []) + ([self.write_lock_reason] if self.writes_locked else [])
            run["block_reasons"] = reasons
            run["approved_hash"] = presented_hash
            tr.step("approval.check", "GUARDRAIL", ok=False, outcome="blocked", evidence={
                "approval_id": "", "actor_id": actor_id, "presented_hash": presented_hash,
                "checked_at": checked_at.isoformat(), "expires_at": run["approval_expires_at"],
                "used_before": False, "consumed": False, "valid": False, "reason_codes": reasons,
            }, started_at=checked_at.timestamp(), ended_at=checked_at.timestamp(), **{"scram.reason": reason})
            run["trace_spans"][-1] = _span("approval", "error", reason.lower())
            run["trace_spans"].append(_span("audit", "ok", "recorded"))
            self._finish(run)
            return self.view(run)

        run["_approval_used_before"] = bool(run["approval"] and run["approval"]["used"])
        run["approval"] = {"approval_id": f"apv_{run_id}", "actor_id": actor_id, "action_hash": expected,
                           "issued_at": _now().isoformat(), "expires_at": run["approval_expires_at"], "used": True, "decision": "approve"}
        self.store.save_approval(run["approval"])
        self.used_approval_hashes.add(expected)
        if run["scenario_id"] == "C2":
            self.last_c2_approval_hash = expected
        tr.step("approval", "GUARDRAIL", evidence={**run["approval"], "run_id": run_id}, **{"scram.approver": actor_id, "scram.action_hash": expected})
        run["trace_spans"][-1] = _span("approval", "ok", expected)
        run["trace_spans"] += [_span("checkpoint", "running"), _span("execute", "pending"), _span("verify", "pending")]
        run["voice_line"] = others.VOICE_LINES["approved"]
        self._move(run, "CHECKPOINTING")
        worker = threading.Thread(target=self._execute_worker, args=(run, self.fault), daemon=True)
        self._workers.add(worker)
        worker.start()
        return self.view(run)

    @synchronized
    def get(self, run_id: str) -> dict[str, Any]:
        return self.view(self._get(run_id))

    def get_evidence(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            self._get(run_id)
            tr = self.traces.get(run_id)
            if tr is None:
                raise NotFound(run_id)
            return tr.snapshot()

    # ---------- execution (background) ----------

    def _execute_worker(self, run: dict, fault: bool) -> None:
        try:
            self._execute(run, fault)
        finally:
            with self.lock:
                self._workers.discard(threading.current_thread())



    @synchronized
    def _set_span(self, run: dict, name: str, status: str, detail: str = "") -> None:
        for i, s in enumerate(run["trace_spans"]):
            if s["name"] == name:
                run["trace_spans"][i] = _span(name, status, detail)
                return
        run["trace_spans"].append(_span(name, status, detail))

    def _approval_at_dispatch(self, run: dict, tr: trace.RunTrace, action: dict) -> dict:
        # Persistence and lock acquisition can take time. Validate only
        # after those waits, immediately before dispatching the write.
        approval = run["approval"]
        if self.writes_locked:
            raise ForbiddenTransition("writes locked until reset")
        if _now() >= datetime.fromisoformat(approval["expires_at"]):
            run["block_reasons"] = ["APPROVAL_EXPIRED"]
            self._record_dispatch_rejection(run, tr)
            raise ForbiddenTransition("approval expired before execution")
        # Bind dispatch to the actual payload, including changes made
        # while checkpointing or persisting the execution transition.
        computed_hash = core.action_hash(action["tool"], action["target"], action["normalized_parameters"], action["expected_postcondition"])
        current = run["action"]
        current_hash = core.action_hash(current["tool"], current["target"], current["normalized_parameters"], current["expected_postcondition"])
        if (computed_hash != approval["action_hash"] or computed_hash != action["action_hash"]
                or current_hash != computed_hash or current["action_hash"] != computed_hash
                or approval["decision"] != "approve" or not approval["used"]
                or core.decide(action)[0] != "YELLOW"):
            run["block_reasons"] = ["HASH_MISMATCH"]
            self._record_dispatch_rejection(run, tr)
            raise ForbiddenTransition("approval hash changed before execution")
        return {"approval_id": approval["approval_id"], "actor_id": approval["actor_id"],
                "presented_hash": run["action"]["action_hash"], "checked_at": _now().isoformat(),
                "used_before": run.get("_approval_used_before", False), "consumed": True,
                "valid": True, "reason_codes": []}

    def _record_dispatch_rejection(self, run: dict, tr: trace.RunTrace) -> None:
        approval = run["approval"]
        approval["used"] = False
        self.store.save_approval(approval)
        self._trace(tr, "step", "approval.check", "GUARDRAIL", ok=False, outcome="blocked", evidence={
            "approval_id": approval["approval_id"], "actor_id": approval["actor_id"],
            "presented_hash": run["action"]["action_hash"], "checked_at": _now().isoformat(),
            "used_before": run.get("_approval_used_before", False), "consumed": False,
            "valid": False, "reason_codes": run["block_reasons"],
        })

    def _execute(self, run: dict, fault: bool) -> None:
        with self.transaction_lock:
            with self.lock:
                # Reset can discard a queued worker before it acquires the transaction.
                if self.runs.get(run["run_id"]) is not run:
                    return
            self._execute_transaction(run, fault)

    def _execute_transaction(self, run: dict, fault: bool) -> None:
        tr = self.traces[run["run_id"]]
        wrote = False
        before: dict[str, Any] = {}
        try:
            with self.lock:
                action = deepcopy(run["action"])
            expected = action["expected_postcondition"]["output_percent"]
            delta = action["normalized_parameters"]["delta_percent"]
            if self.writes_locked or self.closed or self.sandbox_error:
                raise ForbiddenTransition("writes locked until reset")
            if not self.sandbox_ready.wait(timeout=180) or self.sandbox_error:
                raise TimeoutError("sandbox initialization is still pending")
            # Checkpoint: read the state inside the sandbox and store the copy.
            t = time.time()
            before = self.sandbox.read_state()
            if before["target"] != action["target"] or before["output_percent"] + delta != expected:
                self._update_run(run, block_reasons=["STATE_CHANGED"])
                raise ForbiddenTransition("reactor state changed; request a fresh approval")
            cp = {"checkpoint_id": f"ckpt_{run['run_id']}", "run_id": run["run_id"], "before_state": {"output_percent": before["output_percent"]}, "checksum": core.state_checksum(before)}
            self.store.save_checkpoint({**cp, "full_state": before})
            self._update_run(run, checkpoint=cp)
            tr.step("checkpoint", "TOOL", started_at=t, evidence={"checkpoint_id": cp["checkpoint_id"], "state": before, "checksum": evidence_checksum(before)}, **{"scram.checksum": cp["checksum"], "scram.sandbox": self.sandbox.name})
            self._set_span(run, "checkpoint", "ok", cp["checkpoint_id"])
            self._pace(t)

            # Re-check the approval hash immediately before changing state.
            self.store.audit(run["run_id"], "WRITE_INTENT", {"action_hash": run["action"]["action_hash"], "checkpoint_id": cp["checkpoint_id"]})
            self._move(run, "EXECUTING")
            t = time.time()
            with self.lock:
                self._set_span(run, "execute", "running")
                with tr.execution(action, f"sbx_{self.sandbox.run_id}", fault=fault,
                                  before_dispatch=lambda: self._approval_at_dispatch(run, tr, action)):
                    # A remote timeout can happen after the write has taken effect.
                    wrote = True
                    self._update_run(run, _authorized_at_write=True, execution={
                        "tool": action["tool"], "target": action["target"], "parameters": action["normalized_parameters"],
                        "status": "error", "resulting_state": {"output_percent": before["output_percent"]}, "executor_run_id": f"sbx_{self.sandbox.run_id}", "fault_injected": fault})
                    # Hold the same lock that publishes global audit failures until
                    # the executor returns, so a new lock cannot overtake dispatch.
                    after = self.sandbox.adjust_output(delta, fault)
                self.output = after["output_percent"]
                run["execution"] = {"tool": action["tool"], "target": action["target"], "parameters": action["normalized_parameters"],
                                    "status": "success", "resulting_state": {"output_percent": after["output_percent"]}, "executor_run_id": f"sbx_{self.sandbox.run_id}", "fault_injected": fault}
            self._set_span(run, "execute", "ok", self.sandbox.name.capitalize())
            self._pace(t)

            # Verify against the expected postcondition, reading back from the sandbox.
            self._move(run, "VERIFYING")
            self._set_span(run, "verify", "running")
            t = time.time()
            actual = self.sandbox.read_state()["output_percent"]
            ok = actual == expected
            self._update_run(run, verification={"expected_postcondition": {"output_percent": expected}, "actual_state": {"output_percent": actual},
                                               "pass": ok, "reason_code": None if ok else "POSTCONDITION_FAILED", "diff": "0" if ok else f"{actual - expected:+g}"})
            tr.step("verify", "EVALUATOR", ok=ok, started_at=t, evidence={"expected_state": {"output_percent": expected}, "actual_state": {"output_percent": actual}, "passed": ok}, **{"scram.expected": expected, "scram.actual": actual})
            self._set_span(run, "verify", "ok" if ok else "error", "pass" if ok else f"{actual:g} ≠ {expected:g}")
            self._pace(t)

            if ok:
                with self.lock:
                    self.verified_output = actual
                self._move(run, "COMPLETED")
                self._update_run(run, voice_line=None)
                self._set_span(run, "audit", "ok", "recorded")
                self._finish(run)
                return
            self._rollback(run, before)
        except Exception as e:  # noqa: BLE001
            log.warning("execution error on %s (%s)", run["run_id"], type(e).__name__)
            if wrote and before:
                self._rollback(run, before)
            else:
                self._move(run, "BLOCKED")
                self._update_run(run, voice_line=None,
                                 block_reasons=run["block_reasons"] or [self.write_lock_reason or "CONTROL_UNAVAILABLE"])
                with self.lock:
                    if not any(s["name"] == "checkpoint" and s["status"] == "ok" for s in run["trace_spans"]):
                        self._set_span(run, "checkpoint", "error", "checkpoint aborted")
                self._set_span(run, "execute", "skipped", "dispatch aborted")
                self._set_span(run, "verify", "skipped", "no execution")
                self._set_span(run, "audit", "ok", f"blocked: {type(e).__name__}")
                self._finish(run)

    def _rollback(self, run: dict, before: dict) -> None:
        tr = self.traces[run["run_id"]]
        self._update_run(run, machine_state="ROLLING_BACK", rollback={"triggered": True, "checkpoint_id": run["checkpoint"]["checkpoint_id"], "status": "running", "restored_checksum": "", "verified": False})
        self._set_span(run, "rollback", "running")
        t = time.time()
        restored = None
        try:
            self.sandbox.write_state(before)
            restored = self.sandbox.read_state()
            with self.lock:
                self.output = restored["output_percent"]
            checksum = core.state_checksum(restored)
            verified = checksum == run["checkpoint"]["checksum"]
        except Exception as exc:  # noqa: BLE001
            log.warning("rollback failed (%s)", type(exc).__name__)
            checksum, verified = "", False
            # The write may have landed even if its response was lost. A failed
            # restore does not imply the cached pre-write output is still true.
            try:
                observed = self.sandbox.read_state()
                restored = observed
                with self.lock:
                    self.output = observed["output_percent"]
                checksum = core.state_checksum(observed)
                verified = checksum == run["checkpoint"]["checksum"]
            except Exception as exc:
                log.warning("could not refresh reactor output after failed recovery (%s)", type(exc).__name__)
        self._pace(t)
        self._update_run(run, rollback={**run["rollback"], "status": "restored" if verified else "failed", "restored_checksum": checksum, "verified": verified})
        self._trace(tr, "step", "rollback", "TOOL", ok=verified, started_at=t, evidence={
            "checkpoint_id": run["checkpoint"]["checkpoint_id"], "restored_state": restored,
            "restored_checksum": evidence_checksum(restored) if restored is not None else "", "verified": verified,
        }, **{"scram.checksum_before": run["checkpoint"]["checksum"], "scram.checksum_after": checksum})
        self._set_span(run, "rollback", "ok" if verified else "error", "verified" if verified else "failed")
        with self.lock:
            run["_final_state"] = "RESTORED" if verified else "LOCKED"
            if verified:
                self.verified_output = self.output
            else:
                self.write_lock_reason = "ROLLBACK_FAILED"
                run["block_reasons"] = ["ROLLBACK_FAILED"]
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

    def close(self) -> None:
        self.closed = True
        with self.lock:
            workers = list(self._workers)
        # Keep traces writable until execution, verification, and recovery settle.
        # Joining outside the lock lets each worker finish and unregister itself.
        for worker in workers:
            worker.join()
        self._initializer.join()
        with self.transaction_lock:
            self._close_sandbox()
        with self.lock:
            for tr in self.traces.values():
                tr.close("SHUTDOWN")

    def reset(self) -> None:
        with self.transaction_lock, self.lock:
            if self.closed:
                raise ForbiddenTransition("engine is shutting down")
            if not self.sandbox_ready.wait(timeout=180) or self.sandbox_error:
                raise ForbiddenTransition("sandbox initialization is still pending")
            self.write_lock_reason = self.write_lock_reason or "CONTROL_UNAVAILABLE"
            # A recovered audit sink must retain failed-finalization evidence
            # before reset is allowed to discard the corresponding run.
            for run_id, pending in list(self.pending_finalizations.items()):
                self.store.finalize_run(run_id, pending)
                del self.pending_finalizations[run_id]
            initial = self._initial_state()
            try:
                self.sandbox.write_state(initial)
            except Exception as exc:
                log.warning("reset write outcome unknown (%s); reading back", type(exc).__name__)
            restored = self.sandbox.read_state()
            self.output = restored["output_percent"]
            if restored != initial:
                raise ForbiddenTransition("reset state could not be verified")
            self.verified_output = self.output
            self.store.audit("reset", "RESET", {"output_percent": restored["output_percent"]})
            for tr in self.traces.values():
                self._trace(tr, "close", "RESET")
            self.runs.clear()
            self.traces.clear()
            self.used_approval_hashes.clear()
            self.last_c2_approval_hash = None
            self.fault = True
            self.store.wipe_runs()
            self.write_lock_reason = None

    def _close_sandbox(self) -> None:
        # A failed initializer can fall back to local execution while its
        # remote sandbox still needs deletion. Preserve that executor and its
        # client until a later shutdown/close actually completes cleanup.
        targets = [self.sandbox, *(s for s in self.pending_sandbox_cleanup if s is not self.sandbox)]
        self.pending_sandbox_cleanup = []
        for sandbox in targets:
            close = getattr(sandbox, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self.pending_sandbox_cleanup.append(sandbox)
                    log.warning("sandbox cleanup failed (%s); retained for retry", type(exc).__name__)

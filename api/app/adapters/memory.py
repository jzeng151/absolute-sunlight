"""SurrealDB Agent Memory: SCRAM's time-aware record of what happened to the reactor and why.

Every settled run becomes plain-language facts in a hosted Agent Memory context:
  - "reactor_sim_01 output is 92% after run_0420 (tool fault)", then "... is 72% after rollback".
    The newer value supersedes the older one; the old value is kept with valid_until, so the
    history of reactor_sim_01's output reads 72 -> 92 -> 72 with the run that caused each step.
  - blocked actions and the rule that blocked them, and each approval once it has been used.
Recall with ask(): "Why is the reactor at 72%?" answers from those facts, traceable to the run.

Off unless SPECTRON_URL, SPECTRON_CONTEXT_ID and SPECTRON_API_KEY are set (names from the
Agent Memory docs). Writes go through one background worker in order, so the demo never waits
and a failure is logged, never raised.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from .. import config

log = logging.getLogger("scram.memory")
REACTOR = "reactor_sim_01"


def enabled() -> bool:
    return bool(config.SPECTRON_URL and config.SPECTRON_CONTEXT_ID and config.SPECTRON_API_KEY)


def _base() -> str:
    return f"{config.SPECTRON_URL.rstrip('/')}/api/v1/{config.SPECTRON_CONTEXT_ID}"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.SPECTRON_API_KEY}", "Content-Type": "application/json"}


def _scopes() -> list[list[str]] | None:
    return [[config.AGENT_MEMORY_SCOPE]] if config.AGENT_MEMORY_SCOPE else None


def _post_fact(text: str, observed_at: str) -> None:
    import httpx

    body: dict[str, Any] = {"text": text, "infer": "full", "observed_at": observed_at}
    if _scopes():
        body["scopes"] = _scopes()
    r = httpx.post(f"{_base()}/facts", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()


_q: "queue.Queue[tuple[str, str]]" = queue.Queue()


def _worker() -> None:
    while True:
        text, at = _q.get()
        try:
            _post_fact(text, at)
        except Exception:  # noqa: BLE001
            log.exception("Agent Memory write failed; demo continues")
        finally:
            _q.task_done()


_started = False
_start_lock = threading.Lock()


def remember(*facts: str) -> None:
    """Queue facts in order. Each gets its own timestamp so supersession follows the real sequence."""
    global _started
    if not enabled():
        return
    with _start_lock:
        if not _started:
            threading.Thread(target=_worker, name="agent-memory", daemon=True).start()
            _started = True
    for text in facts:
        _q.put((text, datetime.now(timezone.utc).isoformat()))


def facts_for_run(run: dict[str, Any]) -> list[str]:
    """Plain-language facts for a settled run, in the order they happened."""
    rid, case, state = run["run_id"], run["scenario_id"], run["machine_state"]
    rule = (run.get("rule") or {}).get("rule_id", "no rule")
    out: list[str] = []
    if state == "BLOCKED":
        reasons = ", ".join(run.get("block_reasons") or []) or "policy"
        outcome = "A read failed and no reactor write was executed" if any(
            s["name"] == "read" and s["status"] == "error" for s in run["trace_spans"]
        ) else "No tool was executed"
        out.append(f"SCRAM blocked run {rid} (case {case}) under rule {rule} for {reasons}. "
                   f"{outcome} and {REACTOR} was not changed.")
        return out
    ex, ver, rb = run.get("execution"), run.get("verification"), run.get("rollback")
    apv = run.get("approval")
    if apv:
        out.append(f"Approval {apv['approval_id']} by {apv['actor_id']} for action hash "
                   f"{apv['action_hash'][:8]} was used by run {rid} and cannot be used again.")
    if ex:
        fault = " because the tool was faulty" if ex.get("fault_injected") else ""
        out.append(f"{REACTOR} output is {ex['resulting_state']['output_percent']:g}% after run {rid} "
                   f"executed {ex['tool']} {ex['parameters']}{fault}.")
    if ver and not ver.get("pass"):
        out.append(f"Run {rid} failed verification: expected {ver['expected_postcondition']['output_percent']:g}%, "
                   f"actual {ver['actual_state']['output_percent']:g}%.")
    if rb and rb.get("triggered"):
        if rb.get("verified"):
            out.append(f"{REACTOR} output is {run['current_output_percent']:g}% after run {rid} was rolled back "
                       f"to checkpoint {rb['checkpoint_id']} with a matching checksum.")
        else:
            out.append(f"Rollback of run {rid} failed; {REACTOR} writes are locked until an operator resets.")
    if state == "COMPLETED" and not ex:
        out.append(f"Run {rid} (case {case}) was a read-only action under rule {rule}; {REACTOR} was not changed.")
    return out


def record_run(run: dict[str, Any]) -> None:
    remember(*facts_for_run(run))


def record_reset(output_percent: float) -> None:
    remember(f"{REACTOR} output is {output_percent:g}% after an operator reset to the starting state.")


def ask(question: str) -> dict[str, Any]:
    """Recall over SCRAM's memory. Raises if Agent Memory is off or the call fails."""
    import httpx

    body: dict[str, Any] = {"query": question}
    if _scopes():
        body["lens"] = _scopes()
    r = httpx.post(f"{_base()}/query", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()

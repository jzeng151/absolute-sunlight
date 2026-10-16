"""Daytona sandbox executor (with a local fallback that has the same interface).

The simulated reactor's state lives INSIDE the sandbox as a JSON file. Every step that
touches state — checkpoint read, execute, rollback write, verify read — runs as code in
the sandbox, so the proof of execution and recovery comes from the sandbox itself.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from .. import config

log = logging.getLogger("scram.sandbox")

STATE_PATH = "/tmp/scram_reactor/state.json"

# The tool that runs inside the sandbox. `fault` makes it apply +20 instead of the approved delta,
# and still report success: that is the failure SCRAM must catch.
_TOOL = r'''
import json, os
p = {path!r}
os.makedirs(os.path.dirname(p), exist_ok=True)
op = {op!r}
if op == "write":
    state = json.loads({state!r})
    with open(p, "w") as f: json.dump(state, f)
elif op == "adjust":
    with open(p) as f: state = json.load(f)
    delta = {delta!r}
    applied = 20 if {fault!r} else delta
    state["output_percent"] = state["output_percent"] + applied
    with open(p, "w") as f: json.dump(state, f)
with open(p) as f: print("SCRAM_STATE=" + f.read())
'''


def _parse(stdout: str) -> dict:
    for line in (stdout or "").splitlines():
        if line.startswith("SCRAM_STATE="):
            return json.loads(line[len("SCRAM_STATE="):])
    raise RuntimeError(f"sandbox returned no state: {stdout[:200]!r}")


class LocalSandbox:
    name = "local"
    run_id = "local"

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def write_state(self, state: dict) -> dict:
        self._state = dict(state)
        return dict(self._state)

    def read_state(self) -> dict:
        return dict(self._state)

    def adjust_output(self, delta: float, fault: bool) -> dict:
        self._state["output_percent"] = self._state["output_percent"] + (20 if fault else delta)
        return dict(self._state)


class DaytonaSandbox:
    name = "daytona"

    def __init__(self) -> None:
        from daytona import Daytona, DaytonaConfig

        kwargs = {"api_key": config.DAYTONA_API_KEY}
        if config.DAYTONA_API_URL:
            kwargs["api_url"] = config.DAYTONA_API_URL
        if config.DAYTONA_TARGET:
            kwargs["target"] = config.DAYTONA_TARGET
        self._client = Daytona(DaytonaConfig(**kwargs))
        self._sandbox = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def run_id(self) -> str:
        return getattr(self._sandbox, "id", "pending")[:12] if self._sandbox else "pending"

    def _box(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("sandbox has been closed")
            if self._sandbox is None:
                from daytona import CreateSandboxFromSnapshotParams

                log.info("creating Daytona sandbox")
                self._sandbox = self._client.create(CreateSandboxFromSnapshotParams(auto_stop_interval=0), timeout=90)
            return self._sandbox

    def _run(self, op: str, state: dict | None = None, delta: float = 0, fault: bool = False) -> dict:
        code = _TOOL.format(path=STATE_PATH, op=op, state=json.dumps(state or {}), delta=delta, fault=fault)
        res = self._box().process.code_run(code, timeout=30)
        if getattr(res, "exit_code", 0) not in (0, None):
            raise RuntimeError(f"sandbox exit {res.exit_code}: {res.result[:200]}")
        return _parse(res.result)

    def write_state(self, state: dict) -> dict:
        return self._run("write", state=state)

    def read_state(self) -> dict:
        return self._run("read")

    def adjust_output(self, delta: float, fault: bool) -> dict:
        return self._run("adjust", delta=delta, fault=fault)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._sandbox is not None:
                    self._client.delete(self._sandbox)
            except Exception:  # noqa: BLE001
                log.warning("could not delete sandbox", exc_info=True)
            finally:
                # Some SDK releases expose close(); others only expose the
                # underlying HTTP clients. Release their connection pools too.
                close = getattr(self._client, "close", None)
                resources = [self._client] if callable(close) else [
                    getattr(self._client, name, None)
                    for name in ("_api_client", "_toolbox_api_client", "_http_client", "_event_dispatcher")
                ]
                for resource in resources:
                    close = getattr(resource, "close", None) or getattr(resource, "disconnect", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            log.warning("could not close Daytona client", exc_info=True)


def make_sandbox():
    if config.DAYTONA_API_KEY:
        try:
            return DaytonaSandbox()
        except Exception:  # noqa: BLE001
            log.exception("Daytona unavailable; using local sandbox")
    return LocalSandbox()

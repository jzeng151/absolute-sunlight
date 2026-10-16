"""Daytona sandbox executor (with a local fallback that has the same interface).

The simulated reactor's state lives INSIDE the sandbox as a JSON file. Every step that
touches state — checkpoint read, execute, rollback write, verify read — runs as code in
the sandbox, so the proof of execution and recovery comes from the sandbox itself.
"""
from __future__ import annotations

import json
import logging
import math
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
elif op == "adjust":
    with open(p) as f: state = json.load(f)
    delta = {delta!r}
    applied = 20 if {fault!r} else delta
    state["output_percent"] = state["output_percent"] + applied
if op in ("write", "adjust"):
    with open(p + ".tmp", "w") as f: json.dump(state, f, allow_nan=False)
    os.replace(p + ".tmp", p)
with open(p) as f: print("SCRAM_STATE=" + f.read())
'''


def _validate_state(state: Any) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get("target"), str) or not state["target"]:
        raise RuntimeError("sandbox returned invalid reactor state")
    output = state.get("output_percent")
    if type(output) not in (int, float) or not math.isfinite(output):
        raise RuntimeError("sandbox returned invalid reactor output")
    return dict(state)


def _parse(stdout: str) -> dict:
    frames = [line.removeprefix("SCRAM_STATE=") for line in (stdout or "").splitlines() if line.startswith("SCRAM_STATE=")]
    if len(frames) != 1:
        raise RuntimeError("sandbox must return exactly one reactor state")
    try:
        return _validate_state(json.loads(frames[0]))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("sandbox returned malformed reactor state") from exc


class LocalSandbox:
    name = "local"
    run_id = "local"

    def __init__(self, fallback_reason: str | None = None) -> None:
        self._state: dict[str, Any] = {}
        self.fallback_reason = fallback_reason

    def write_state(self, state: dict) -> dict:
        self._state = _validate_state(state)
        return dict(self._state)

    def read_state(self) -> dict:
        return dict(self._state)

    def adjust_output(self, delta: float, fault: bool) -> dict:
        if type(delta) not in (int, float) or not math.isfinite(delta) or type(fault) is not bool:
            raise ValueError("invalid sandbox adjustment")
        self._state = _validate_state({**self._state, "output_percent": self._state["output_percent"] + (20 if fault else delta)})
        return dict(self._state)

    def close(self) -> None:
        pass


class DaytonaSandbox:
    name = "daytona"
    fallback_reason = None

    def __init__(self) -> None:
        from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig

        kwargs = {"api_key": config.DAYTONA_API_KEY}
        if config.DAYTONA_API_URL:
            kwargs["api_url"] = config.DAYTONA_API_URL
        if config.DAYTONA_TARGET:
            kwargs["target"] = config.DAYTONA_TARGET
        self._client = Daytona(DaytonaConfig(**kwargs))
        self._params = CreateSandboxFromSnapshotParams(
            language="python",
            snapshot=config.DAYTONA_SNAPSHOT or None,
            labels={"app": "scram"},
            public=False,
            network_block_all=True,
            auto_stop_interval=0,
            auto_delete_interval=0,
        )
        self._sandbox = None
        self._lock = threading.RLock()
        self._closed = False
        self._closing = False

    @property
    def run_id(self) -> str:
        return self._sandbox.id if self._sandbox else "pending"

    def _box(self):
        with self._lock:
            if self._closed or self._closing:
                raise RuntimeError("sandbox is closed")
            if self._sandbox is None:
                log.info("creating Daytona sandbox")
                self._sandbox = self._client.create(self._params, timeout=90)
            return self._sandbox

    def _run(self, op: str, state: dict | None = None, delta: float = 0, fault: bool = False) -> dict:
        if state is not None:
            state = _validate_state(state)
        if type(delta) not in (int, float) or not math.isfinite(delta) or type(fault) is not bool:
            raise ValueError("invalid sandbox adjustment")
        code = _TOOL.format(path=STATE_PATH, op=op, state=json.dumps(state or {}, allow_nan=False), delta=delta, fault=fault)
        with self._lock:
            if self._closed or self._closing:
                raise RuntimeError("sandbox is closed")
            try:
                res = self._box().process.code_run(code, timeout=30)
            except Exception as exc:
                # SDK exceptions can embed credentials or raw remote output.
                raise RuntimeError(f"Daytona request failed ({type(exc).__name__})") from None
            if type(getattr(res, "exit_code", None)) is not int or res.exit_code != 0:
                raise RuntimeError("Daytona execution did not exit successfully")
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
            self._closing = True
            if self._sandbox is not None:
                for attempt in range(3):
                    try:
                        self._client.delete(self._sandbox)
                        break
                    except Exception as exc:
                        log.warning("could not delete sandbox (%s), attempt %s", type(exc).__name__, attempt + 1)
                        if attempt == 2:
                            # Keep the sandbox and client available to close()
                            # again, while rejecting all process operations.
                            raise RuntimeError("Daytona cleanup failed; close can be retried") from None
            self._closed = True
            # Some SDK releases expose close(); others only expose the
            # underlying HTTP clients. Release pools after deletion succeeds.
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
                    except Exception as exc:
                        log.warning("could not close Daytona client (%s)", type(exc).__name__)


def make_sandbox():
    if config.SCRAM_EXECUTOR == "local":
        return LocalSandbox()
    if config.DAYTONA_API_KEY:
        try:
            return DaytonaSandbox()
        except Exception as exc:  # noqa: BLE001
            if config.SCRAM_EXECUTOR == "daytona":
                raise RuntimeError("Daytona client initialization failed") from None
            log.warning("Daytona client unavailable (%s); using local sandbox", type(exc).__name__)
            return LocalSandbox("DAYTONA_CLIENT_UNAVAILABLE")
    if config.SCRAM_EXECUTOR == "daytona":
        raise RuntimeError("DAYTONA_API_KEY is required for SCRAM_EXECUTOR=daytona")
    return LocalSandbox("DAYTONA_KEY_MISSING")

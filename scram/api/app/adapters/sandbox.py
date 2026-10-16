"""Sandbox executor. The local sandbox holds the simulated reactor's state in this process.

write_state / read_state / adjust_output is the executor interface: a remote sandbox
implements the same three calls, so swapping one in does not change the engine.
"""
from __future__ import annotations

from typing import Any


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


def make_sandbox():
    return LocalSandbox()

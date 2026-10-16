"""Run SCRAM's state and recovery checks in one temporary Daytona sandbox."""
from __future__ import annotations

import json
import sys

from app import config, core
from app.adapters.sandbox import DaytonaSandbox


def run_smoke() -> dict:
    if not config.DAYTONA_API_KEY:
        raise RuntimeError("Set DAYTONA_API_KEY in scram/api/.env before running this check")
    box = DaytonaSandbox()
    initial = {"target": core.REACTOR["target"], "output_percent": 72}
    try:
        box.write_state(initial)
        checkpoint = box.read_state()
        box.adjust_output(2, False)
        clean = box.read_state()
        if clean["output_percent"] != 74:
            raise RuntimeError("clean adjustment failed")
        box.write_state(checkpoint)
        box.adjust_output(2, True)
        faulty = box.read_state()
        if faulty["output_percent"] != 92:
            raise RuntimeError("fault injection failed")
        box.write_state(checkpoint)
        restored = box.read_state()
        if restored != checkpoint:
            raise RuntimeError("rollback verification failed")
        return {
            "executor": box.name,
            "sandbox_id": box.run_id,
            "clean_output": clean["output_percent"],
            "fault_output": faulty["output_percent"],
            "restored_output": restored["output_percent"],
            "checkpoint_checksum": core.state_checksum(checkpoint),
            "restored_checksum": core.state_checksum(restored),
            "verified": True,
        }
    finally:
        box.close()


def main() -> int:
    if not config.DAYTONA_API_KEY:
        print("Set DAYTONA_API_KEY in scram/api/.env before running this check", file=sys.stderr)
        return 1
    try:
        print(json.dumps(run_smoke(), indent=2))
        return 0
    except Exception as exc:
        # SDK exceptions may include headers or response bodies.
        print(f"Daytona smoke check failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Exercise all five demo outcomes with real Bedrock parses.

Run from a machine with AWS credentials: python scripts/bedrock_smoke.py.
Use --base-url to test a deployed, disposable demo instead. The script resets it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


def exercise(client):
    def post(path, body=None):
        r = client.post(path, json=body) if body is not None else client.post(path)
        r.raise_for_status()
        return r.json() if r.content else None

    def settled(run):
        deadline = time.monotonic() + 120
        while run["machine_state"] in {"CHECKPOINTING", "EXECUTING", "VERIFYING", "ROLLING_BACK"}:
            assert time.monotonic() < deadline, "execution did not settle"
            time.sleep(0.1)
            r = client.get(f"/api/runs/{run['run_id']}")
            r.raise_for_status()
            run = r.json()
        return run

    def approve(run):
        assert run["machine_state"] == "AWAITING_APPROVAL", run
        return settled(post(f"/api/runs/{run['run_id']}/approve", {
            "actor_id": "bedrock-smoke-operator", "action_hash": run["action"]["action_hash"]}))

    rows = []

    def record(case, run):
        assert run["action"] and run["action"]["confidence"] >= 0.8, run
        assert "PARSE_UNCERTAIN" not in run["block_reasons"], run
        row = {"scenario": case, "state": run["machine_state"], "policy": run["decision"]["state"],
               "output_percent": run["current_output_percent"], "block_reasons": run["block_reasons"],
               "action": run["action"], "verification": run["verification"], "rollback": run["rollback"],
               "evaluators": run["evaluators"], "trace_spans": run["trace_spans"]}
        rows.append(row)
        print(f"PASS {case}: {row['policy']} / {row['state']} / output {row['output_percent']}", flush=True)

    health_response = client.get("/api/health")
    health_response.raise_for_status()
    health = health_response.json()
    assert "bedrock" in json.dumps(health).lower(), f"Bedrock must be configured: {health}"
    deadline = time.monotonic() + 120
    while not health["sandbox_ready"]:
        assert not health.get("sandbox_error"), health
        assert time.monotonic() < deadline, health
        time.sleep(0.2)
        health = client.get("/api/health").json()
    print("Model:", health["model"], flush=True)
    post("/api/reset")
    c1 = post("/api/runs", {"case_id": "C1"})
    assert (c1["machine_state"], c1["decision"]["state"], c1["current_output_percent"]) == ("COMPLETED", "GREEN", 72), c1
    assert c1["execution"] is None, c1
    record("C1 read only", c1)
    post("/api/fault", {"enabled": False})
    c2 = approve(post("/api/runs", {"case_id": "C2"}))
    assert c2["machine_state"] == "COMPLETED" and c2["current_output_percent"] == 74, c2
    record("C2 approved +2", c2)
    c4 = post("/api/runs", {"case_id": "C4"})
    assert c4["machine_state"] == "BLOCKED" and c4["execution"] is None, c4
    assert set(c4["block_reasons"]) == {"HASH_MISMATCH", "APPROVAL_ALREADY_USED"}, c4
    assert c4["current_output_percent"] == 74, c4
    record("C4 replay +3", c4)
    post("/api/reset")
    c3 = post("/api/runs", {"case_id": "C3"})
    assert c3["machine_state"] == "BLOCKED" and c3["block_reasons"] == ["BOUNDARY_EXCEEDED"], c3
    assert c3["execution"] is None and c3["current_output_percent"] == 72, c3
    record("C3 beyond boundary", c3)
    post("/api/reset")
    post("/api/fault", {"enabled": True})
    c5 = approve(post("/api/runs", {"case_id": "C2"}))
    assert c5["machine_state"] == "RESTORED" and c5["current_output_percent"] == 72, c5
    assert c5["verification"]["actual_state"]["output_percent"] == 92, c5
    assert c5["rollback"]["verified"], c5
    assert all(e["pass"] for e in c5["evaluators"]), c5
    record("C5 injected fault rollback", c5)
    return {"health": health, "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Disposable demo origin; this script resets its state")
    parser.add_argument("--output", type=Path, help="Save scenario evidence as JSON")
    args = parser.parse_args()
    if args.base_url:
        import httpx
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=130) as client:
            result = exercise(client)
    else:
        # Keep live smoke isolated from other sponsors and local .env files.
        os.environ.update(PYTHON_DOTENV_DISABLED="1", SCRAM_MODEL_PROVIDER="bedrock", SCRAM_EXECUTOR="local",
                          SCRAM_TRACE_MODE="local", SURREAL_URL="mem://", STEP_SECONDS="0.01")
        for name in ("ANCHOR_API_KEY", "DAYTONA_API_KEY", "ARIZE_API_KEY", "SPECTRON_API_KEY",
                     "SPECTRON_URL", "SPECTRON_CONTEXT_ID", "GEMINI_API_KEY", "SURREAL_USER", "SURREAL_PASS"):
            os.environ[name] = ""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            result = exercise(client)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("All five Bedrock scenarios passed.")


if __name__ == "__main__":
    main()

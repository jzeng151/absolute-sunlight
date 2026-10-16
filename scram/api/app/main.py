"""SCRAM API — Build Spec section 5. Run: uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config, core
from .adapters import trace
from .engine import Engine, ForbiddenTransition, NotFound

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
engine: Engine | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    trace.setup()
    current = Engine()
    engine = current
    try:
        yield
    finally:
        try:
            await run_in_threadpool(current.close)
        finally:
            engine = None


app = FastAPI(title="SCRAM", version="0.3", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


class CreateRun(BaseModel):
    case_id: str


class Approve(BaseModel):
    actor_id: str
    action_hash: str | None = None


class Fault(BaseModel):
    enabled: bool


def _engine() -> Engine:
    if engine is None or engine.closed:
        raise HTTPException(503, "Application is not running")
    return engine


def _guard(fn, *args):
    try:
        return fn(*args)
    except NotFound as e:
        raise HTTPException(404, f"Unknown {e}") from e
    except ForbiddenTransition as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/health")
def health():
    return _engine().health()


@app.post("/api/runs")
def create_run(body: CreateRun):
    return _guard(_engine().create_run, body.case_id)


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, body: Approve):
    return _guard(_engine().approve, run_id, body.actor_id, body.action_hash)


@app.post("/api/runs/{run_id}/reject")
def reject(run_id: str):
    return _guard(_engine().reject, run_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _guard(_engine().get, run_id)


@app.get("/api/runs/{run_id}/rules")
def run_rules(run_id: str):
    """Which rule governed this run?"""
    return _engine().store.rules_for_run(run_id)


@app.post("/api/fault", status_code=204)
def set_fault(body: Fault):
    _engine().set_fault(body.enabled)
    return Response(status_code=204)


@app.post("/api/reset", status_code=204)
def reset():
    _guard(_engine().reset)
    return Response(status_code=204)


@app.get("/api/evals/baseline")
def baseline():
    return core.BASELINE

"""Rulebook and fixture loading. JSON for now; SurrealDB slots in behind load_rules() at build step 9.

fixtures/*.json are copies of scram/web/src/fixtures/*.json; tests/test_fixtures.py keeps them in sync.
"""

import json
from pathlib import Path

from api.contracts import BaselineRow, Reactor, Rule

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _read(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_rules() -> list[Rule]:
    return [Rule(**r) for r in _read("rulebook.json")]


def load_reactor() -> Reactor:
    return Reactor(**_read("cases.json")["reactor"])


def load_cases() -> dict[str, dict]:
    return {c["id"]: c for c in _read("cases.json")["cases"]}


def load_baseline() -> list[BaselineRow]:
    return [BaselineRow(**row) for row in _read("baseline.json")]

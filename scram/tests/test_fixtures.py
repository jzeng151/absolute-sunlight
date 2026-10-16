"""Back-end fixtures must stay identical to the front end's, or fixture mode and live mode drift."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["cases.json", "rulebook.json", "baseline.json"])
def test_fixtures_match_web(name):
    ours = json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))
    theirs = json.loads((ROOT / "web" / "src" / "fixtures" / name).read_text(encoding="utf-8"))
    assert ours == theirs, f"scram/fixtures/{name} drifted from scram/web/src/fixtures/{name}"

"""Settings, read once from the environment (scram/api/.env).

Every sponsor integration is optional at runtime: when its key is missing, its adapter
falls back to a local implementation with the same interface, and /api/health says so.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # python-dotenv is optional
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# Paths
API_DIR = Path(__file__).resolve().parents[1]
FIXTURES = API_DIR.parent / "web" / "src" / "fixtures"  # one source of truth, shared with the front end

# Arize AX — traces and evaluators
ARIZE_SPACE_ID = _env("ARIZE_SPACE_ID")
ARIZE_API_KEY = _env("ARIZE_API_KEY")
ARIZE_PROJECT = _env("ARIZE_PROJECT", _env("ARIZE_PROJECT_NAME", "scram"))
SCRAM_TRACE_MODE = _env("SCRAM_TRACE_MODE", "auto")

# Behaviour
STEP_SECONDS = float(_env("STEP_SECONDS", "0.9"))  # minimum pace between execution steps, for the audience
APPROVAL_TTL_SECONDS = int(_env("APPROVAL_TTL_SECONDS", "300"))
PARSE_MIN_CONFIDENCE = 0.8
CORS_ORIGINS = [o for o in _env("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",") if o]

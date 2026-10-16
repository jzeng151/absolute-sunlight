"""Backend tests must never use developer credentials or create cloud resources."""
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["SCRAM_EXECUTOR"] = "local"
os.environ["SURREAL_URL"] = "mem://"
for key in (
    "DAYTONA_API_KEY", "ARIZE_API_KEY", "ARIZE_SPACE_ID", "SURREAL_USER",
    "SURREAL_PASS", "GEMINI_API_KEY", "ANCHOR_API_KEY", "ELEVENLABS_API_KEY",
):
    os.environ.pop(key, None)

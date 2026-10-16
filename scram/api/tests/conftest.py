"""Tests never use operator credentials or send sponsor traffic."""
import os

os.environ["SCRAM_TRACE_MODE"] = "local"
for name in ("ARIZE_API_KEY", "ARIZE_SPACE_ID", "DAYTONA_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ANCHOR_API_KEY",
             "SPECTRON_API_KEY", "SPECTRON_URL", "SPECTRON_CONTEXT_ID", "SURREAL_USER", "SURREAL_PASS"):
    os.environ[name] = ""
os.environ["SURREAL_URL"] = "mem://"

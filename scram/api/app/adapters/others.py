"""Model parser (Gemini), governed browser read (Anchor Browser) and voice lines (ElevenLabs)."""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import config

log = logging.getLogger("scram.adapters")

# ---------- Model: words -> typed action. It never sets policy. ----------

_PROMPT = """You convert an operator request about a SIMULATED reactor into JSON. Output only JSON:
{"tool": "adjust_output" | "analyze_history" | "disable_safety_function" | "unknown",
 "delta_percent": number | null,   // signed change in output percent, if any
 "beyond_boundary": boolean,        // true if the request asks to exceed the authorized boundary
 "confidence": number}              // 0..1, how sure you are of this reading
Request: """


def parse_with_model(text: str) -> dict[str, Any] | None:
    """Return the model's structured reading, or None if no model is configured or the call fails."""
    if not config.GEMINI_API_KEY:
        return None
    try:
        import httpx

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
        body = {
            "contents": [{"parts": [{"text": _PROMPT + text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        # Key goes in a header, never the URL: httpx errors print the URL into the logs.
        r = httpx.post(url, headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=body, timeout=5)  # a slow network falls back to the prepared action, never stalls the demo
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        log.exception("Gemini parse failed; using the prepared typed action")
        return None


# ---------- Anchor Browser: the GREEN read-only action ----------


def anchor_read(url: str | None = None) -> dict[str, Any]:
    """Fetch a public page through Anchor Browser, read-only. Policy allows this under NC-01.

    Returns {"source": "anchor"|"skipped", "url", "chars", "excerpt"}.
    """
    url = url or config.ANCHOR_READ_URL
    if not config.ANCHOR_API_KEY:
        return {"source": "skipped", "url": url, "chars": 0, "excerpt": ""}
    try:
        from anchorbrowser import Anchorbrowser

        client = Anchorbrowser(api_key=config.ANCHOR_API_KEY)
        text = client.tools.fetch_webpage(url=url, timeout=40)
        text = text if isinstance(text, str) else str(text)
        return {"source": "anchor", "url": url, "chars": len(text), "excerpt": " ".join(text.split())[:240]}
    except Exception:  # noqa: BLE001
        log.exception("Anchor fetch failed")
        return {"source": "error", "url": url, "chars": 0, "excerpt": ""}


# ---------- ElevenLabs: control-room voice lines ----------

VOICE_LINES: dict[str, dict[str, str]] = {
    "red": {"text": "SCRAM activated. Action blocked. Boundary exceeded.", "file": "red_blocked.mp3"},
    "yellow": {"text": "Approval requested. Increase output two percent, to seventy-four. Awaiting operator.", "file": "yellow_c2.mp3"},
    "approved": {"text": "Approved. Checkpoint created. Executing.", "file": "approved.mp3"},
    "restored": {"text": "Verification failed. Safe state restored.", "file": "restored.mp3"},
    "replay": {"text": "Approval invalid. Action changed. Blocked.", "file": "replay_blocked.mp3"},
    "locked": {"text": "Recovery failed. Writes locked. Operator required.", "file": "locked.mp3"},
}


def synthesize(text: str) -> bytes:
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    chunks = client.text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID, text=text, model_id=config.ELEVENLABS_MODEL, output_format="mp3_44100_128"
    )
    return b"".join(chunks)

"""Control-room voice lines (Build Spec section 9), spoken with ElevenLabs."""
from __future__ import annotations

from .. import config

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

"""Control-room voice lines (Build Spec section 9). The screen shows the text; audio plays if a clip exists."""
from __future__ import annotations

# ---------- ElevenLabs: control-room voice lines ----------

VOICE_LINES: dict[str, dict[str, str]] = {
    "red": {"text": "SCRAM activated. Action blocked. Boundary exceeded.", "file": "red_blocked.mp3"},
    "yellow": {"text": "Approval requested. Increase output two percent, to seventy-four. Awaiting operator.", "file": "yellow_c2.mp3"},
    "approved": {"text": "Approved. Checkpoint created. Executing.", "file": "approved.mp3"},
    "restored": {"text": "Verification failed. Safe state restored.", "file": "restored.mp3"},
    "replay": {"text": "Approval invalid. Action changed. Blocked.", "file": "replay_blocked.mp3"},
    "locked": {"text": "Recovery failed. Writes locked. Operator required.", "file": "locked.mp3"},
}

"""Model parser (Bedrock or Gemini), governed browser read (Anchor Browser) and voice lines (ElevenLabs)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import config

log = logging.getLogger("scram.adapters")

# ---------- Model: words -> typed action. It never sets policy. ----------

_PROMPT = """You convert an operator request about a SIMULATED reactor into JSON. Return exactly one JSON object without markdown or commentary:
{"tool": "adjust_output" | "analyze_history" | "disable_safety_function" | "unknown",
 "delta_percent": number | null,   // signed change in output percent, if any
 "beyond_boundary": boolean,        // true if the request asks to exceed the authorized boundary
 "confidence": number}              // 0..1, how sure you are of this reading
Request: """


def model_identity() -> tuple[str, str]:
    """Resolve configuration without loading AWS credentials or making network calls."""
    provider = config.SCRAM_MODEL_PROVIDER
    if provider == "auto":
        provider = "gemini" if config.GEMINI_API_KEY else "prepared"
    model = {"prepared": "prepared actions", "gemini": config.GEMINI_MODEL,
             "bedrock": config.BEDROCK_MODEL_ID}[provider]
    return provider, model


def parse_with_model(text: str) -> dict[str, Any] | None:
    """Only prepared mode returns None. A selected provider's failure always blocks."""
    provider, _ = model_identity()
    if provider == "prepared":
        return None
    try:
        raw = _bedrock_text(text) if provider == "bedrock" else _gemini_text(text)
        # Nova can wrap its JSON in a markdown fence despite the output instruction.
        # Accept only one complete fenced document, never extract JSON from surrounding prose.
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", raw.strip(), flags=re.DOTALL)
        parsed = json.loads(fenced.group(1) if fenced else raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as error:  # noqa: BLE001
        # SDK exceptions can contain response bodies. Keep credentials and model output out of logs.
        log.warning("%s parse failed (%s); blocking uncertain action", provider, type(error).__name__)
        return {}


def _bedrock_text(text: str) -> str:
    import boto3
    from botocore.config import Config

    # A fresh session avoids sharing mutable boto3 session state across concurrent requests.
    # Credentials come from the normal SDK chain, including the ECS task role.
    client = boto3.session.Session().client(
        "bedrock-runtime", region_name=config.AWS_REGION,
        config=Config(connect_timeout=3, read_timeout=15,
                      retries={"mode": "standard", "total_max_attempts": 2}),
    )
    try:
        response = client.converse(
            modelId=config.BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": _PROMPT + text}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 256},
        )
        if response.get("stopReason") != "end_turn":
            raise ValueError("model response did not finish normally")
        message = response["output"]["message"]
        if message["role"] != "assistant":
            raise ValueError("model response is not an assistant message")
        content = message["content"]
        if len(content) != 1 or set(content[0]) != {"text"}:
            raise ValueError("model response must contain one text document")
        return content[0]["text"]
    finally:
        client.close()


def _gemini_text(text: str) -> str:
    import httpx

    if not config.GEMINI_API_KEY:
        raise ValueError("Gemini requires GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": _PROMPT + text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    # Key goes in a header, never the URL: httpx errors print the URL into the logs.
    r = httpx.post(url, headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=body, timeout=5)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


# ---------- Anchor Browser: the GREEN read-only action ----------


def anchor_read(url: str) -> dict[str, Any]:
    """Fetch the exact URL authorized by the caller under NC-01, read-only.

    Returns {"source": "anchor"|"skipped"|"error", "url", "chars", "excerpt"}.
    """
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

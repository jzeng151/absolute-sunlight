"""Generate the six control-room clips with ElevenLabs into web/public/audio/.

Run once, before the demo:  python -m scripts.make_voice_clips
The app then plays cached files, so the demo never waits on the network.
"""
from app import config
from app.adapters.others import VOICE_LINES, synthesize


def main() -> None:
    if not config.ELEVENLABS_API_KEY:
        raise SystemExit("Set ELEVENLABS_API_KEY in scram/api/.env first.")
    config.AUDIO_OUT.mkdir(parents=True, exist_ok=True)
    for key, line in VOICE_LINES.items():
        path = config.AUDIO_OUT / line["file"]
        path.write_bytes(synthesize(line["text"]))
        print(f"wrote {path.name}  ({key}: {line['text']})")


if __name__ == "__main__":
    main()

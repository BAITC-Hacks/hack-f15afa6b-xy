"""Cache fixed Pulse 109 dialogue prompts from an OmniVoice endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice_api import MAX_TTS_AUDIO_BYTES, VOICE_PROMPTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "static" / "voice")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, (language, text) in VOICE_PROMPTS.items():
        payload = json.dumps({"text": text, "language": language}, ensure_ascii=False).encode()
        request = Request(args.base_url.rstrip("/") + "/v1/speech", data=payload,
                          headers={"Content-Type": "application/json", "Accept": "audio/wav"})
        with urlopen(request, timeout=120) as response:
            audio = response.read(MAX_TTS_AUDIO_BYTES + 1)
        if (len(audio) < 44 or len(audio) > MAX_TTS_AUDIO_BYTES or
                not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE"):
            raise RuntimeError(f"OmniVoice returned invalid audio for {name}")
        (args.output / f"{name}.wav").write_bytes(audio)
        print(f"cached {name}: {len(audio)} bytes")


if __name__ == "__main__":
    main()

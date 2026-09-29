"""Serve the pinned Kazakh/Russian TorchScript speech recognizer."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import os
import re
import time
import wave
from array import array
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


REPO = "alibiserikbay/kazakh-russian-mixed-stt"
REVISION = "26298d2a61dc1573bfc11b7055c7d09a1e64b8a4"
MODEL_NAME = REPO + "@" + REVISION + "/asr/rukk"
MAX_AUDIO_BYTES = 2 * 1024 * 1024


class TranscriptionRequest(BaseModel):
    audio_data: str = Field(max_length=2_800_000)
    language: str = "mixed"
    field: str = "problem"


def decode_wav(value: str) -> tuple[array, float]:
    try:
        header, encoded = value.split(",", 1)
        if header != "data:audio/wav;base64":
            raise ValueError
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "audio_data must be a base64 WAV data URL") from None
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(422, "audio is longer than 30 seconds")
    try:
        with wave.open(io.BytesIO(content), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000:
                raise HTTPException(422, "audio must be 16 kHz mono PCM16 WAV")
            frames = source.readframes(source.getnframes())
            duration = source.getnframes() / source.getframerate()
    except (EOFError, wave.Error):
        raise HTTPException(422, "invalid WAV audio") from None
    if not .15 <= duration <= 30:
        raise HTTPException(422, "audio duration must be between 0.15 and 30 seconds")
    samples = array("h")
    samples.frombytes(frames)
    if os.sys.byteorder == "big":
        samples.byteswap()
    return samples, duration


def load_tokens(path: Path) -> dict[int, str]:
    tokens = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            symbol, index = line.split("\t")
            tokens[int(index)] = symbol
    if not tokens:
        raise ValueError("tokens.lst is empty")
    return tokens


def collapse_ctc(ids: list[int], tokens: dict[int, str]) -> str:
    blank = max(tokens) + 1
    output = []
    previous = None
    for index in ids:
        if index != previous and index != blank:
            output.append(tokens.get(index, ""))
        previous = index
    text = "".join(output).replace("|", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MixedStt:
    def __init__(self, model_dir: Path | None, device: str):
        import torch
        if model_dir is None:
            from huggingface_hub import hf_hub_download
            model_path = Path(hf_hub_download(REPO, "asr/rukk/model.pt", revision=REVISION))
            tokens_path = Path(hf_hub_download(REPO, "asr/rukk/tokens.lst", revision=REVISION))
        else:
            model_path = model_dir / "model.pt"
            tokens_path = model_dir / "tokens.lst"
        if not model_path.is_file() or not tokens_path.is_file():
            raise FileNotFoundError("model.pt and tokens.lst are required")
        self.torch = torch
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else "cpu" if device == "auto" else device
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        self.model = torch.jit.load(str(model_path), map_location=self.device).eval()
        self.tokens = load_tokens(tokens_path)
        self.model_sha256 = file_sha256(model_path)

    def transcribe(self, samples: array) -> tuple[str, int]:
        started = time.perf_counter()
        waveform = self.torch.tensor(samples, dtype=self.torch.float32, device=self.device).div_(32768)
        with self.torch.inference_mode():
            output = self.model(waveform.unsqueeze(0))
        if isinstance(output, (tuple, list)):
            output = output[0]
        if output.ndim != 3 or output.shape[0] != 1:
            raise RuntimeError("unexpected model output shape")
        text = collapse_ctc(output[0].argmax(-1).tolist(), self.tokens)
        return text, round((time.perf_counter() - started) * 1000)


def create_app(agent: MixedStt) -> FastAPI:
    app = FastAPI(title="Pulse 109 RU/KK voice STT")
    api_key = os.environ.get("P109_STT_API_KEY")

    def authorize(authorization: str | None):
        if api_key and authorization != "Bearer " + api_key:
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    def health(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {"status": "healthy", "model": MODEL_NAME, "device": agent.device,
                "model_sha256": agent.model_sha256}

    @app.post("/v1/transcribe")
    def transcribe(req: TranscriptionRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        samples, duration = decode_wav(req.audio_data)
        text, latency = agent.transcribe(samples)
        if not text:
            raise HTTPException(422, "speech was not recognized")
        return {"text": text, "model": MODEL_NAME, "device": agent.device,
                "latency_ms": latency, "duration_seconds": round(duration, 2),
                "audio_stored": False}

    return app


def self_check():
    tokens = {0: "а", 1: "б", 2: "|"}
    assert collapse_ctc([0, 0, 3, 1, 1, 2, 0], tokens) == "аб а"
    stream = io.BytesIO()
    with wave.open(stream, "wb") as target:
        target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        target.writeframes(b"\0\0" * 3200)
    value = "data:audio/wav;base64," + base64.b64encode(stream.getvalue()).decode()
    samples, duration = decode_wav(value)
    assert len(samples) == 3200 and duration == .2
    print("PASS: voice STT WAV validation and CTC decoding")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    import uvicorn
    uvicorn.run(create_app(MixedStt(args.model_dir, args.device)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

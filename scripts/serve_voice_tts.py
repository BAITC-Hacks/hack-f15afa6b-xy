"""Serve the pinned OmniVoice model for Pulse 109 dialogue prompts."""

from __future__ import annotations

import argparse
import io
import os
import threading
import time
import wave
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field


MODEL_REPO = "k2-fsa/OmniVoice"
MODEL_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    language: Literal["ru", "kk", "mixed"]


def encode_wav(samples) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples).reshape(-1), -1, 1) * 32767).astype("<i2")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as target:
        target.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        target.writeframes(pcm.tobytes())
    return stream.getvalue()


class OmniVoiceTts:
    def __init__(self, model: str, revision: str, device: str, steps: int):
        import torch
        from huggingface_hub import snapshot_download
        from omnivoice import OmniVoice

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        model_path = snapshot_download(model, revision=revision)
        dtype = torch.float16 if device == "cuda" else torch.float32
        self.torch = torch
        self.model = OmniVoice.from_pretrained(model_path, device_map=device, dtype=dtype)
        self.model_id = model + "@" + revision
        self.device = device
        self.steps = steps
        self.lock = threading.Lock()

    def generate(self, text: str, language: str) -> tuple[bytes, int]:
        started = time.perf_counter()
        options = {"text": text, "num_step": self.steps}
        if language in {"ru", "kk"}:
            options["language_id"] = language
        with self.lock, self.torch.inference_mode():
            self.torch.manual_seed(109)
            if self.device == "cuda":
                self.torch.cuda.manual_seed_all(109)
            audio = self.model.generate(**options)[0]
            if self.device == "cuda":
                self.torch.cuda.synchronize()
        return encode_wav(audio), round((time.perf_counter() - started) * 1000)


def create_app(agent: OmniVoiceTts) -> FastAPI:
    app = FastAPI(title="Pulse 109 OmniVoice TTS")
    api_key = os.environ.get("P109_TTS_API_KEY")

    def authorize(authorization: str | None):
        if api_key and authorization != "Bearer " + api_key:
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    def health(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {"status": "healthy", "model": agent.model_id, "device": agent.device,
                "steps": agent.steps}

    @app.post("/v1/speech")
    def speech(req: SpeechRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        audio, latency = agent.generate(req.text.strip(), req.language)
        return Response(audio, media_type="audio/wav",
                        headers={"X-Model": agent.model_id, "X-Latency-Ms": str(latency)})

    return app


def self_check():
    import numpy as np

    audio = encode_wav(np.array([0, .25, -.25], dtype=np.float32))
    with wave.open(io.BytesIO(audio), "rb") as source:
        assert source.getparams()[:3] == (1, 2, 24000)
        assert source.getnframes() == 3
    assert SpeechRequest(text="Сәлеметсіз бе", language="kk").language == "kk"
    print("PASS: OmniVoice request validation and WAV encoding")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL_REPO)
    parser.add_argument("--revision", default=MODEL_REVISION)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--steps", type=int, choices=range(8, 65), default=32)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    import uvicorn
    uvicorn.run(create_app(OmniVoiceTts(args.model, args.revision, args.device, args.steps)),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()

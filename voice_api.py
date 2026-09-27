"""Private speech-to-text bridge for the citizen voice intake."""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


MAX_AUDIO_BYTES = 2 * 1024 * 1024


class VoiceRequest(BaseModel):
    audio_data: str = Field(max_length=2_800_000)
    language: Literal["ru", "kk", "mixed"] = "mixed"
    field: Literal["problem", "address"]


def decode_wav_data(value: str) -> bytes:
    try:
        header, encoded = value.split(",", 1)
        if header != "data:audio/wav;base64":
            raise ValueError
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Запись должна быть WAV-аудио") from None
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(422, "Голосовая запись должна быть не длиннее 30 секунд")
    if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
        raise HTTPException(422, "Не удалось прочитать голосовую запись")
    return content


def build_voice_router() -> APIRouter:
    router = APIRouter(prefix="/api/voice")
    base_url = os.environ.get("P109_STT_BASE_URL", "").strip().rstrip("/")
    timeout = float(os.environ.get("P109_STT_TIMEOUT", "35"))
    api_key = os.environ.get("P109_STT_API_KEY")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("P109_STT_BASE_URL must start with http:// or https://")
    if timeout <= 0:
        raise ValueError("P109_STT_TIMEOUT must be positive")

    def request(path: str, payload: dict | None = None) -> tuple[dict, int]:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        started = time.perf_counter()
        try:
            with urlopen(Request(base_url + path, data=data, headers=headers, method=method),
                         timeout=timeout) as response:
                result = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise HTTPException(503, "Голосовая модель сейчас недоступна") from None
        if not isinstance(result, dict):
            raise HTTPException(503, "Голосовая модель вернула неверный ответ")
        return result, round((time.perf_counter() - started) * 1000)

    @router.get("/health")
    def health():
        if not base_url:
            return {"status": "disabled"}
        try:
            result, latency = request("/health")
        except HTTPException as error:
            return {"status": "unavailable", "detail": error.detail}
        return {"status": result.get("status", "unavailable"), "model": result.get("model"),
                "device": result.get("device"), "latency_ms": latency}

    @router.post("/transcribe")
    def transcribe(req: VoiceRequest):
        decode_wav_data(req.audio_data)
        if not base_url:
            raise HTTPException(503, "Голосовая модель не запущена")
        result, total_latency = request("/v1/transcribe", req.model_dump())
        text = result.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise HTTPException(503, "Голосовая модель не распознала речь")
        prompts = {
            "ru": {"problem": "Теперь назовите адрес или ближайший ориентир.",
                   "address": "Проверьте текст, точку на карте и приложите фото или видео."},
            "kk": {"problem": "Енді мекенжайды немесе жақын жердегі нысанды айтыңыз.",
                   "address": "Мәтінді, картадағы орынды тексеріп, фото немесе видео тіркеңіз."},
            "mixed": {"problem": "Теперь назовите адрес. Енді мекенжайды айтыңыз.",
                      "address": "Проверьте данные. Мәліметтерді тексеріңіз."},
        }
        return {"text": text.strip(), "field": req.field,
                "next_field": "address" if req.field == "problem" else "review",
                "assistant_message": prompts[req.language][req.field],
                "language": req.language, "model": result.get("model"),
                "stt_latency_ms": result.get("latency_ms"), "total_latency_ms": total_latency,
                "audio_stored": False}

    return router

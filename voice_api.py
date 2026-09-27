"""Private speech bridge for the citizen voice intake."""

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
MAX_TTS_AUDIO_BYTES = 2 * 1024 * 1024
VOICE_PROMPTS = {
    "ru_problem": ("ru", "Расскажите, что произошло. Говорите до тридцати секунд."),
    "ru_address": ("ru", "Теперь назовите адрес или ближайший ориентир."),
    "ru_review": ("ru", "Проверьте текст, точку на карте и приложите фото или видео."),
    "kk_problem": ("kk", "Не болғанын айтып беріңіз. Отыз секундқа дейін сөйлеңіз."),
    "kk_address": ("kk", "Енді мекенжайды немесе жақын жердегі нысанды айтыңыз."),
    "kk_review": ("kk", "Мәтінді, картадағы орынды тексеріп, фото немесе видео тіркеңіз."),
    "mixed_problem": ("mixed", "Расскажите о проблеме. Мәселе туралы айтып беріңіз."),
    "mixed_address": ("mixed", "Теперь назовите адрес. Енді мекенжайды айтыңыз."),
    "mixed_review": ("mixed", "Проверьте данные. Мәліметтерді тексеріңіз."),
}


class VoiceRequest(BaseModel):
    audio_data: str = Field(max_length=2_800_000)
    language: Literal["ru", "kk", "mixed"] = "mixed"
    field: Literal["problem", "address"]


class SpeechRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=32)


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
    stt_url = os.environ.get("P109_STT_BASE_URL", "").strip().rstrip("/")
    tts_url = os.environ.get("P109_TTS_BASE_URL", "").strip().rstrip("/")
    stt_timeout = float(os.environ.get("P109_STT_TIMEOUT", "35"))
    tts_timeout = float(os.environ.get("P109_TTS_TIMEOUT", "35"))
    stt_key = os.environ.get("P109_STT_API_KEY")
    tts_key = os.environ.get("P109_TTS_API_KEY")
    for name, value in (("P109_STT_BASE_URL", stt_url), ("P109_TTS_BASE_URL", tts_url)):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(f"{name} must start with http:// or https://")
    if stt_timeout <= 0 or tts_timeout <= 0:
        raise ValueError("Voice service timeouts must be positive")

    def request_json(base_url: str, timeout: float, api_key: str | None,
                     path: str, payload: dict | None = None) -> tuple[dict, int]:
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

    def request_audio(path: str, payload: dict) -> tuple[bytes, str | None, int]:
        headers = {"Accept": "audio/wav", "Content-Type": "application/json"}
        if tts_key:
            headers["Authorization"] = "Bearer " + tts_key
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        started = time.perf_counter()
        try:
            with urlopen(Request(tts_url + path, data=data, headers=headers, method="POST"),
                         timeout=tts_timeout) as response:
                content_type = response.headers.get_content_type()
                model = response.headers.get("X-Model")
                content = response.read(MAX_TTS_AUDIO_BYTES + 1)
        except (HTTPError, URLError, TimeoutError):
            raise HTTPException(503, "Голос OmniVoice сейчас недоступен") from None
        if content_type != "audio/wav" or len(content) > MAX_TTS_AUDIO_BYTES:
            raise HTTPException(503, "OmniVoice вернул неверное аудио")
        if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
            raise HTTPException(503, "OmniVoice вернул повреждённое аудио")
        return content, model, round((time.perf_counter() - started) * 1000)

    @router.get("/health")
    def health():
        if not stt_url:
            result = {"status": "disabled"}
        else:
            try:
                upstream, latency = request_json(stt_url, stt_timeout, stt_key, "/health")
                result = {"status": upstream.get("status", "unavailable"),
                          "model": upstream.get("model"), "device": upstream.get("device"),
                          "latency_ms": latency}
            except HTTPException as error:
                result = {"status": "unavailable", "detail": error.detail}
        if not tts_url:
            result["tts"] = {"status": "disabled"}
        else:
            try:
                upstream, latency = request_json(tts_url, tts_timeout, tts_key, "/health")
                result["tts"] = {"status": upstream.get("status", "unavailable"),
                                 "model": upstream.get("model"), "device": upstream.get("device"),
                                 "latency_ms": latency}
            except HTTPException as error:
                result["tts"] = {"status": "unavailable", "detail": error.detail}
        return result

    @router.post("/transcribe")
    def transcribe(req: VoiceRequest):
        decode_wav_data(req.audio_data)
        if not stt_url:
            raise HTTPException(503, "Голосовая модель не запущена")
        result, total_latency = request_json(
            stt_url, stt_timeout, stt_key, "/v1/transcribe", req.model_dump()
        )
        text = result.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise HTTPException(503, "Голосовая модель не распознала речь")
        assistant_prompt = f"{req.language}_{'address' if req.field == 'problem' else 'review'}"
        return {"text": text.strip(), "field": req.field,
                "next_field": "address" if req.field == "problem" else "review",
                "assistant_message": VOICE_PROMPTS[assistant_prompt][1],
                "assistant_prompt": assistant_prompt,
                "language": req.language, "model": result.get("model"),
                "stt_latency_ms": result.get("latency_ms"), "total_latency_ms": total_latency,
                "audio_stored": False}

    @router.post("/speak")
    def speak(req: SpeechRequest):
        prompt = VOICE_PROMPTS.get(req.prompt)
        if prompt is None:
            raise HTTPException(422, "Неизвестная реплика голосового помощника")
        if not tts_url:
            raise HTTPException(503, "OmniVoice не подключён")
        language, text = prompt
        audio, model, latency = request_audio("/v1/speech", {"text": text, "language": language})
        return {"audio_data": "data:audio/wav;base64," + base64.b64encode(audio).decode(),
                "model": model, "tts_latency_ms": latency, "audio_stored": False}

    return router

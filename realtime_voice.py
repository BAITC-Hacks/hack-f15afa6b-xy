"""Server-side OpenAI Realtime transcription proxy."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field


class TranscriptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    language: Literal["auto", "ru", "kk", "mixed"] = "auto"
    hint_language: Literal["ru", "kk"] | None = None
    field: Literal["problem", "address"]


@dataclass(frozen=True)
class RealtimeConfig:
    provider: str
    model: str
    api_key: str
    timeout: float
    debounce_ms: int

    @classmethod
    def from_env(cls, timeout: float):
        provider = os.environ.get("P109_REALTIME_PROVIDER", "").strip().lower()
        if provider not in {"", "openai"}:
            raise ValueError("P109_REALTIME_PROVIDER must be openai or empty")
        try:
            debounce = int(os.environ.get("P109_LIVE_COPILOT_DEBOUNCE_MS", "2500"))
        except ValueError:
            raise ValueError("P109_LIVE_COPILOT_DEBOUNCE_MS must be an integer") from None
        if not 500 <= debounce <= 10000:
            raise ValueError("P109_LIVE_COPILOT_DEBOUNCE_MS must be between 500 and 10000")
        return cls(provider, os.environ.get("P109_OPENAI_REALTIME_MODEL", "").strip(),
                   os.environ.get("OPENAI_API_KEY", "").strip(), timeout, debounce)

    @property
    def enabled(self):
        return bool(self.provider == "openai" and self.model and self.api_key)

    def health(self):
        return {"status": "configured" if self.enabled else "disabled",
                "provider": "openai" if self.enabled else None,
                "model": self.model if self.enabled else None,
                "transport": "pulse_backend_proxy" if self.enabled else None,
                "live_copilot_debounce_ms": self.debounce_ms}


def attach_realtime_route(router: APIRouter, limit: Callable, config: RealtimeConfig):
    @router.websocket("/realtime")
    async def realtime(websocket: WebSocket):
        await websocket.accept()
        origin = websocket.headers.get("origin")
        if origin and urlparse(origin).netloc != websocket.headers.get("host"):
            await websocket.close(code=1008)
            return
        limit(websocket)
        if not config.enabled:
            await websocket.send_json({"type": "pulse.error", "detail": "Realtime недоступен"})
            await websocket.close(code=1013)
            return
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=config.api_key, timeout=config.timeout, max_retries=0)
            async with client.realtime.connect(model=config.model, max_retries=0) as upstream:
                await upstream.send_raw(json.dumps({"type": "session.update", "session": {
                    "type": "transcription", "audio": {"input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": config.model, "languages": ["ru", "kk"],
                                          "delay": "low"},
                        "turn_detection": None,
                    }},
                }}))
                await websocket.send_json({"type": "pulse.ready"})

                async def browser_to_openai():
                    total_audio_chars = 0
                    while True:
                        message = await websocket.receive_json()
                        event_type = message.get("type")
                        if event_type == "input_audio_buffer.append":
                            audio = message.get("audio")
                            if not isinstance(audio, str) or len(audio) > 1_500_000:
                                await websocket.close(code=1009)
                                return
                            total_audio_chars += len(audio)
                            if total_audio_chars > 2_000_000:
                                await websocket.close(code=1009)
                                return
                            await upstream.input_audio_buffer.append(audio=audio)
                        elif event_type == "input_audio_buffer.commit":
                            await upstream.input_audio_buffer.commit()

                async def openai_to_browser():
                    async for event in upstream:
                        if getattr(event, "type", "") in {
                            "conversation.item.input_audio_transcription.delta",
                            "conversation.item.input_audio_transcription.completed", "error",
                        }:
                            await websocket.send_json(event.model_dump(mode="json"))

                tasks = [asyncio.create_task(browser_to_openai()),
                         asyncio.create_task(openai_to_browser())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
        except WebSocketDisconnect:
            return
        except Exception:
            try:
                await websocket.send_json({"type": "pulse.error", "detail": "Realtime недоступен"})
                await websocket.close(code=1013)
            except Exception:
                pass


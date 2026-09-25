"""Small HTTP client for Laya's Jev-compatible decision endpoint."""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

class LayaError(RuntimeError):
    pass


class LayaUnavailable(LayaError):
    pass


class LayaResponseError(LayaError):
    pass


@dataclass(frozen=True)
class LayaResult:
    model: str
    answers: dict[str, dict[str, Any]]
    usage: dict[str, Any]
    routing: dict[str, Any]
    latency_ms: int


class LayaClient:
    def __init__(self, base_url: str, timeout: float = 8.0, model: str = "multilingual",
                 api_key: str | None = None):
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("P109_LAYA_BASE_URL must start with http:// or https://")
        if timeout <= 0:
            raise ValueError("P109_LAYA_TIMEOUT must be positive")
        self.base_url = base_url
        self.timeout = timeout
        self.model = model.strip() or "multilingual"
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _open(self, request: Request) -> tuple[bytes, int]:
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
            latency = round((time.perf_counter() - started) * 1000)
            logger.debug("Laya request completed", extra={"method": request.get_method(),
                         "latency_ms": latency})
            return raw, latency
        except HTTPError as error:
            logger.warning("Laya HTTP error", extra={"method": request.get_method(), "status": error.code})
            if error.code >= 500:
                raise LayaUnavailable(f"Laya returned HTTP {error.code}") from None
            raise LayaResponseError(f"Laya rejected the request with HTTP {error.code}") from None
        except (TimeoutError, socket.timeout, URLError, ConnectionError):
            logger.warning("Laya transport error", extra={"method": request.get_method()})
            raise LayaUnavailable("Laya is unavailable or timed out") from None

    @staticmethod
    def _json(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LayaResponseError("Laya returned invalid JSON") from None
        if not isinstance(data, dict):
            raise LayaResponseError("Laya response must be an object")
        return data

    def health(self) -> dict[str, Any]:
        try:
            raw, latency = self._open(Request(self.base_url + "/health", headers=self._headers()))
            data = self._json(raw)
            if data.get("status") != "ok":
                raise LayaResponseError("Laya health response is not ok")
            return {"status": "healthy", "latency_ms": latency, "loaded": data.get("loaded", []),
                    "revisions": data.get("revisions", {})}
        except LayaError as error:
            return {"status": "unavailable", "error": str(error)}

    def predict(self, state: str | dict | list, questions: dict[str, dict[str, Any]]) -> LayaResult:
        if not isinstance(state, (str, dict, list)):
            raise ValueError("Laya state must be text, an object, or a list")
        if not questions or not isinstance(questions, dict):
            raise ValueError("Laya questions must be a non-empty object")
        payload = json.dumps({"state": state, "questions": questions, "model": self.model},
                             ensure_ascii=False).encode("utf-8")
        headers = {**self._headers(), "Content-Type": "application/json"}
        raw, latency = self._open(Request(self.base_url + "/v1/systemone", data=payload,
                                          headers=headers, method="POST"))
        data = self._json(raw)
        answers = data.get("answers")
        if not isinstance(answers, dict) or any(qid not in answers for qid in questions):
            raise LayaResponseError("Laya response is missing typed answers")
        for qid, question in questions.items():
            answer = answers[qid]
            if not isinstance(answer, dict) or answer.get("type") != question.get("type"):
                raise LayaResponseError(f"Laya answer {qid!r} has an unexpected schema")
        return LayaResult(
            model=str(data.get("model") or self.model), answers=answers,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
            routing=data.get("routing") if isinstance(data.get("routing"), dict) else {},
            latency_ms=latency,
        )

"""Optional private Qwen implementation of the shared Copilot provider."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from copilot_contracts import (
    ACTIONS,
    CopilotAuthError,
    CopilotContext,
    CopilotOutput,
    CopilotRateLimitError,
    CopilotResponseError,
    CopilotResult,
    CopilotTimeout,
    CopilotUnavailable,
    CopilotUsage,
    make_result_id,
    provider_context,
    validate_copilot_output,
)


MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"


class QwenCopilot:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = MODEL,
        revision: str = MODEL_REVISION,
        timeout: float = 18.0,
    ):
        base_url = base_url.strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("P109_COPILOT_BASE_URL must be an HTTP URL")
        if not self._private_host(parsed.hostname):
            raise ValueError("P109_COPILOT_BASE_URL must use a private or loopback host")
        if not api_key:
            raise ValueError("P109_COPILOT_API_KEY is required for a configured Qwen service")
        if timeout <= 0:
            raise ValueError("P109_COPILOT_TIMEOUT must be positive")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            raise ValueError("P109_COPILOT_REVISION must be a pinned commit or model digest")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model.strip() or MODEL
        self.revision = revision
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "qwen"

    @staticmethod
    def _private_host(host: str) -> bool:
        if host in {"localhost", "host.docker.internal"} or host.endswith((".internal", ".local")):
            return True
        try:
            address = ipaddress.ip_address(host)
            return address.is_private or address.is_loopback
        except ValueError:
            return False

    @classmethod
    def from_env(cls):
        base_url = os.environ.get("P109_COPILOT_BASE_URL", "").strip()
        if not base_url:
            return None
        try:
            timeout = float(os.environ.get("P109_COPILOT_TIMEOUT", "18"))
        except ValueError:
            raise ValueError("P109_COPILOT_TIMEOUT must be a number") from None
        return cls(
            base_url,
            os.environ.get("P109_COPILOT_API_KEY", ""),
            os.environ.get("P109_COPILOT_MODEL", MODEL),
            os.environ.get("P109_COPILOT_REVISION", MODEL_REVISION),
            timeout,
        )

    def health(self) -> dict:
        return {
            "configured": True,
            "status": "configured",
            "mode": "self_hosted_qwen",
            "model": self.model,
            "model_revision": self.revision,
            "endpoint": "private",
        }

    def status(self) -> dict:
        return self.health()

    @staticmethod
    def _schema() -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "summary",
                "reasoning",
                "suggested_reply",
                "clarification_question",
                "recommended_action",
            ],
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                "reasoning": {"type": "string", "minLength": 1, "maxLength": 500},
                "suggested_reply": {"type": "string", "minLength": 1, "maxLength": 2000},
                "clarification_question": {"type": ["string", "null"], "maxLength": 300},
                "recommended_action": {"type": "string", "enum": sorted(ACTIONS)},
            },
        }

    @staticmethod
    def _language_instruction(language: str) -> str:
        if language == "kk":
            return "Use natural standard Kazakh. Do not translate the citizen into Russian."
        if language == "mixed":
            return "Match the citizen's dominant language and preserve natural RU/KK code-switching."
        return "Use clear natural Russian."

    def assist(self, context: CopilotContext) -> CopilotResult:
        schema = self._schema()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Pulse 109 operator copilot for Kazakhstan. Use only supplied verified facts. "
                    "Never promise a deadline, claim work was completed, invent a service action or cause, or "
                    "make the final decision for the operator. Use review_incident only when incident_candidate "
                    "is true. A clarification question requires action clarify, and clarify requires a question. "
                    "Keep the response concise. "
                    + self._language_instruction(context.language)
                    + " Return one JSON object and no markdown matching this schema exactly: "
                    + json.dumps(schema, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": json.dumps(provider_context(context), ensure_ascii=False)},
        ]
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 350,
                "reasoning_effort": "none",
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "pulse109_copilot", "strict": True, "schema": schema},
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise CopilotAuthError(f"Qwen returned HTTP {error.code}") from None
            if error.code == 429:
                raise CopilotRateLimitError("Qwen rate limit reached") from None
            if error.code >= 500:
                raise CopilotUnavailable(f"Qwen returned HTTP {error.code}") from None
            raise CopilotResponseError(f"Qwen rejected the request with HTTP {error.code}") from None
        except (TimeoutError, socket.timeout):
            raise CopilotTimeout("Qwen timed out") from None
        except (URLError, ConnectionError):
            raise CopilotUnavailable("Qwen is unavailable") from None
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            response = json.loads(raw)
            answer = json.loads(response["choices"][0]["message"]["content"])
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise CopilotResponseError("Qwen returned invalid structured output") from None
        output = validate_copilot_output(answer, context)
        model = str(response.get("model") or self.model)
        raw_usage = response.get("usage") or {}
        usage = CopilotUsage(
            input_tokens=max(0, int(raw_usage.get("prompt_tokens") or 0)),
            output_tokens=max(0, int(raw_usage.get("completion_tokens") or 0)),
            cached_tokens=max(0, int((raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)),
        )
        return CopilotResult(
            **output.model_dump(),
            provider=self.provider_name,
            model=model,
            model_revision=self.revision,
            latency_ms=latency_ms,
            result_id=make_result_id(output, self.provider_name, model, self.revision, context.request_id),
            request_id=context.request_id,
            mode="self_hosted_qwen",
            usage=usage,
        )

    @staticmethod
    def _validate(answer: dict, context: dict) -> dict:
        """Compatibility wrapper used by the existing safety check."""
        action = answer.get("recommended_action", "manual_review")
        dummy = CopilotContext(
            language=context.get("language", "ru"),
            complaint="fixture",
            incident_candidate=bool(context.get("incident_candidate")),
            confirmed_facts=context.get("confirmed_facts") or {},
            fallback_summary="fixture",
            fallback_reasoning="fixture",
            fallback_reply="fixture",
            fallback_question="fixture" if action == "clarify" else None,
            fallback_action=action if action in ACTIONS else "manual_review",
        )
        return validate_copilot_output(answer, dummy).model_dump()

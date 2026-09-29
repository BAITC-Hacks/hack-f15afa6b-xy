"""Hosted OpenAI Copilot using the Responses API and Structured Outputs."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import ValidationError

from copilot_contracts import (
    COPILOT_PROMPT_VERSION,
    CopilotAuthError,
    CopilotContext,
    CopilotOutput,
    CopilotQuotaError,
    CopilotRateLimitError,
    CopilotResponseError,
    CopilotResult,
    CopilotSchemaError,
    CopilotTimeout,
    CopilotUnavailable,
    CopilotUsage,
    make_result_id,
    provider_context,
    validate_copilot_output,
)
from copilot_router import CopilotModelRouter


class OpenAICopilot:
    def __init__(
        self,
        api_key: str,
        models: dict[str, str],
        timeout: float = 15.0,
        max_output_tokens: int = 500,
        model_router: CopilotModelRouter | None = None,
        input_usd_per_million: float = 0,
        output_usd_per_million: float = 0,
        client: Any = None,
    ):
        if not api_key and client is None:
            raise ValueError("OPENAI_API_KEY is required")
        if set(models) != {"fast", "primary", "complex"} or not all(models.values()):
            raise ValueError("All P109_OPENAI_MODEL_* values are required")
        if timeout <= 0 or max_output_tokens < 100:
            raise ValueError("OpenAI timeout and output token limit must be positive")
        self.models = models
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.model_router = model_router or CopilotModelRouter.from_env()
        self.input_usd_per_million = max(0, input_usd_per_million)
        self.output_usd_per_million = max(0, output_usd_per_million)
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise ValueError("The official openai Python package is not installed") from error
            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        self.client = client

    @property
    def provider_name(self) -> str:
        return "openai"

    @classmethod
    def from_env(cls):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        models = {
            "fast": os.environ.get("P109_OPENAI_MODEL_FAST", "").strip(),
            "primary": os.environ.get("P109_OPENAI_MODEL_PRIMARY", "").strip(),
            "complex": os.environ.get("P109_OPENAI_MODEL_COMPLEX", "").strip(),
        }
        if not all(models.values()):
            return None
        try:
            timeout = float(os.environ.get("P109_OPENAI_TIMEOUT", "15"))
            max_output_tokens = int(os.environ.get("P109_AI_MAX_OUTPUT_TOKENS", "500"))
        except ValueError:
            raise ValueError("OpenAI timeout and token limits must be numeric") from None
        try:
            input_price = float(os.environ.get("P109_OPENAI_INPUT_USD_PER_M", "0"))
            output_price = float(os.environ.get("P109_OPENAI_OUTPUT_USD_PER_M", "0"))
        except ValueError:
            raise ValueError("OpenAI token prices must be numeric") from None
        return cls(api_key, models, timeout, max_output_tokens, input_usd_per_million=input_price,
                   output_usd_per_million=output_price)

    def health(self) -> dict:
        return {
            "configured": True,
            "status": "configured",
            "api": "responses",
            "structured_outputs": True,
            "models": dict(self.models),
            "cost_tracking": bool(self.input_usd_per_million or self.output_usd_per_million),
        }

    @staticmethod
    def _language_instruction(language: str) -> str:
        if language == "kk":
            return "Use natural standard Kazakh. Do not translate the citizen into Russian."
        if language == "mixed":
            return "Reply mainly in the citizen's dominant language and preserve natural RU/KK mixing."
        return "Use clear natural Russian."

    def _instructions(self, context: CopilotContext) -> str:
        return (
            f"Pulse 109 operator Copilot. Prompt version: {COPILOT_PROMPT_VERSION}. "
            "Use only the supplied verified facts. If information is missing, say it is not confirmed. "
            "Never promise a deadline, claim that work is complete, invent a cause or service action, "
            "confirm an incident, increase severity, route a complaint, or close a complaint. The operator "
            "makes every important decision. Use review_incident only when incident_candidate is true. "
            "A clarification question requires recommended_action=clarify, and clarify requires a question. "
            + self._language_instruction(context.language)
            + (" The attached image is unverified supporting evidence. Populate visual_evidence with "
               "neutral visible observations only; do not confirm damage, causes, incidents, services, "
               "severity or resolution." if context.image_data_url else
               " Set visual_evidence to null because no approved image is attached.")
        )

    def _input(self, context: CopilotContext) -> list[dict]:
        payload = json.dumps(provider_context(context), ensure_ascii=False)
        content: list[dict] = [{"type": "input_text", "text": payload}]
        if context.image_data_url:
            content.append({"type": "input_image", "image_url": context.image_data_url, "detail": "low"})
        return [
            {"role": "system", "content": self._instructions(context)},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _raise_mapped(error: Exception) -> None:
        name = type(error).__name__
        status = getattr(error, "status_code", None)
        message = str(error).lower()
        if name in {"APITimeoutError", "TimeoutException"}:
            raise CopilotTimeout("OpenAI timed out") from None
        if name in {"AuthenticationError", "PermissionDeniedError"} or status in {401, 403}:
            raise CopilotAuthError("OpenAI authentication failed") from None
        if name == "RateLimitError" or status == 429:
            if "quota" in message or "billing" in message:
                raise CopilotQuotaError("OpenAI quota is unavailable") from None
            raise CopilotRateLimitError("OpenAI rate limit reached") from None
        if name in {"APIConnectionError", "InternalServerError"} or status and status >= 500:
            raise CopilotUnavailable("OpenAI is unavailable") from None
        if name in {
            "BadRequestError", "UnprocessableEntityError", "ContentFilterFinishReasonError",
            "LengthFinishReasonError",
        } or status in {400, 422}:
            raise CopilotResponseError("OpenAI rejected the request") from None
        raise CopilotUnavailable("OpenAI request failed") from None

    def _usage(self, response: Any) -> CopilotUsage:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        input_tokens = max(0, int(getattr(usage, "input_tokens", 0) or 0))
        output_tokens = max(0, int(getattr(usage, "output_tokens", 0) or 0))
        cost = ((input_tokens * self.input_usd_per_million +
                 output_tokens * self.output_usd_per_million) / 1_000_000)
        return CopilotUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                            cached_tokens=max(0, int(getattr(details, "cached_tokens", 0) or 0)),
                            estimated_cost_usd=round(cost, 8) if cost else None)

    def assist(self, context: CopilotContext) -> CopilotResult:
        tier = self.model_router.select(context)
        model = self.models[tier]
        started = time.perf_counter()
        try:
            response = self.client.responses.parse(
                model=model,
                input=self._input(context),
                text_format=CopilotOutput,
                max_output_tokens=self.max_output_tokens,
                reasoning={"effort": "low" if tier == "complex" else "none"},
                store=False,
            )
        except ValidationError:
            raise CopilotSchemaError("OpenAI returned invalid structured output") from None
        except Exception as error:
            self._raise_mapped(error)
            raise AssertionError("unreachable")
        latency_ms = round((time.perf_counter() - started) * 1000)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise CopilotSchemaError("OpenAI returned no parsed structured output")
        output = validate_copilot_output(parsed, context)
        actual_model = str(getattr(response, "model", None) or model)
        return CopilotResult(
            **output.model_dump(),
            provider=self.provider_name,
            model=actual_model,
            model_revision=None,
            latency_ms=latency_ms,
            result_id=make_result_id(output, self.provider_name, actual_model, None, context.request_id),
            request_id=context.request_id,
            model_tier=tier,
            mode="hosted_openai",
            usage=self._usage(response),
        )

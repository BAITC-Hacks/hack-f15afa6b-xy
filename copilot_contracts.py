"""Shared contracts and safety checks for every Pulse Copilot provider."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


ACTIONS = {"clarify", "prepare_reply", "review_incident", "manual_review"}
COPILOT_PROMPT_VERSION = "pulse-copilot-v2"


class CopilotError(RuntimeError):
    failure_type = "provider_unavailable"


class CopilotUnavailable(CopilotError):
    failure_type = "provider_unavailable"


class CopilotTimeout(CopilotUnavailable):
    failure_type = "timeout"


class CopilotRateLimitError(CopilotUnavailable):
    failure_type = "rate_limit"


class CopilotAuthError(CopilotError):
    failure_type = "authentication_error"


class CopilotQuotaError(CopilotError):
    failure_type = "quota_error"


class CopilotResponseError(CopilotError):
    failure_type = "invalid_response"


class CopilotSchemaError(CopilotResponseError):
    failure_type = "schema_error"


class CopilotContext(BaseModel):
    """Only these fields may leave Pulse for an external Copilot provider."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["ru", "kk", "mixed", "unknown"] = "unknown"
    complaint: str = Field(min_length=1, max_length=6000)
    city: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    confidence_band: Literal["high", "medium", "low"] | None = None
    priority_signal: str | None = Field(default=None, max_length=40)
    decision_status: str = Field(default="pending", max_length=40)
    incident_candidate: bool = False
    clarifications: list[str] = Field(default_factory=list, max_length=10)
    confirmed_facts: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    fallback_summary: str = Field(min_length=1, max_length=500)
    fallback_reasoning: str = Field(min_length=1, max_length=500)
    fallback_reply: str = Field(min_length=1, max_length=2000)
    fallback_question: str | None = Field(default=None, max_length=300)
    fallback_action: Literal["clarify", "prepare_reply", "review_incident", "manual_review"]
    complexity_signals: list[str] = Field(default_factory=list, max_length=10)
    request_type: Literal["operator", "live", "multimodal"] = "operator"
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    image_data_url: str | None = Field(default=None, max_length=8_000_000)


class VisualEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[str] = Field(min_length=1, max_length=5)
    suggested_category: str | None = Field(default=None, max_length=80)
    confidence: float = Field(ge=0, le=1)


class CopilotOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    reasoning: str = Field(min_length=1, max_length=500)
    suggested_reply: str = Field(min_length=1, max_length=2000)
    clarification_question: str | None = Field(default=None, max_length=300)
    recommended_action: Literal["clarify", "prepare_reply", "review_incident", "manual_review"]
    visual_evidence: VisualEvidence | None = None


class CopilotUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class CopilotResult(CopilotOutput):
    provider: str
    model: str | None = None
    model_revision: str | None = None
    latency_ms: int = Field(ge=0)
    result_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    prompt_version: str = COPILOT_PROMPT_VERSION
    model_tier: Literal["fast", "primary", "complex"] | None = None
    mode: str
    available: bool = True
    usage: CopilotUsage = Field(default_factory=CopilotUsage)
    fallback_reason: str | None = None
    shadow_comparison: dict[str, Any] | None = None


@runtime_checkable
class CopilotProvider(Protocol):
    def assist(self, context: CopilotContext) -> CopilotResult:
        ...

    def health(self) -> dict[str, Any]:
        ...

    @property
    def provider_name(self) -> str:
        ...


def sanitize_text(text: str, address: str | None = None, limit: int = 6000) -> str:
    """Remove direct identifiers before text enters a provider context."""
    value = str(text or "")[:10000]
    if address and address.strip():
        value = re.sub(re.escape(address.strip()), "[ADDRESS]", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[EMAIL]",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<!\d)(?:\d[\s-]?){12}(?!\d)", "[IIN]", value)
    value = re.sub(
        r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)",
        "[PHONE]",
        value,
    )
    return value.strip()[:limit]


def sanitize_context(context: CopilotContext | dict[str, Any]) -> CopilotContext:
    value = context if isinstance(context, CopilotContext) else CopilotContext.model_validate(context)
    data = value.model_dump()
    data["complaint"] = sanitize_text(value.complaint)
    data["clarifications"] = [sanitize_text(item, limit=1000) for item in value.clarifications]
    data["confirmed_facts"] = {
        str(key)[:80]: sanitize_text(item, limit=500) if isinstance(item, str) else item
        for key, item in value.confirmed_facts.items()
    }
    return CopilotContext.model_validate(data)


def provider_context(context: CopilotContext) -> dict[str, Any]:
    """Return the minimal provider payload; fallback templates stay inside Pulse."""
    return {
        "language": context.language,
        "complaint": context.complaint,
        "city": context.city,
        "district": context.district,
        "category": context.category,
        "confidence_band": context.confidence_band,
        "priority_signal": context.priority_signal,
        "decision_status": context.decision_status,
        "incident_candidate": context.incident_candidate,
        "clarifications": context.clarifications,
        "confirmed_facts": context.confirmed_facts,
    }


def validate_action(output: CopilotOutput, context: CopilotContext) -> None:
    has_question = bool(output.clarification_question and output.clarification_question.strip())
    if (output.recommended_action == "clarify") != has_question:
        raise CopilotSchemaError("Clarification action and question are inconsistent")
    if output.recommended_action == "review_incident" and not context.incident_candidate:
        raise CopilotSchemaError("Incident review is unavailable for this complaint")


def validate_deadline_claims(output: CopilotOutput, context: CopilotContext) -> None:
    combined = " ".join((output.summary, output.reasoning, output.suggested_reply))
    deadline = re.search(
        r"\b(?:до|через|в течение|ішінде|дейін|кейін)\s+\d+\s*"
        r"(?:минут|мин|час|часа|часов|дн|день|дня|рабоч|минутта|сағат|күн)",
        combined,
        re.IGNORECASE,
    )
    if deadline:
        facts = json.dumps(context.confirmed_facts, ensure_ascii=False).lower()
        if deadline.group(0).lower() not in facts:
            raise CopilotResponseError("Copilot suggested an unverified deadline")


def validate_incident_action(output: CopilotOutput, context: CopilotContext) -> None:
    if output.recommended_action == "review_incident" and not context.incident_candidate:
        raise CopilotResponseError("Copilot suggested an unavailable incident action")


def validate_copilot_output(
    output: CopilotOutput | dict[str, Any], context: CopilotContext
) -> CopilotOutput:
    try:
        checked = output if isinstance(output, CopilotOutput) else CopilotOutput.model_validate(output)
    except Exception as error:
        raise CopilotSchemaError("Copilot output does not match the shared schema") from error

    combined = " ".join((checked.summary, checked.reasoning, checked.suggested_reply))
    completion_pattern = (
        r"\b(?:уже\s+)?(?:устранили|исправили|отремонтировали|восстановили|проверили|направили)\b|"
        r"\b(?:данные|работа|обращение)\s+(?:проверен[аоы]?|направлен[аоы]?|выполнен[аоы]?)\b|"
        r"\b(?:проблема|неисправность)\s+(?:устранена|решена)\b|\bтеперь\s+работает\b|"
        r"\b(?:жөнделді|қалпына\s+келтірілді|тексерілді|жіберілді|орындалды|мәселе\s+шешілді)\b"
    )
    if re.search(completion_pattern, combined, re.IGNORECASE):
        facts = json.dumps(context.confirmed_facts, ensure_ascii=False)
        if not re.search(completion_pattern, facts, re.IGNORECASE):
            raise CopilotResponseError("Copilot claimed an unverified completed action")

    if checked.visual_evidence:
        observations = " ".join(checked.visual_evidence.observations)
        forbidden_visual_claim = (
            r"\b(?:подтвержден[аоы]?|точно|причин[аы]|авария|служба\s+назначена|"
            r"critical|confirmed|cause|жабылды|расталды|себеб[іи])\b"
        )
        if re.search(forbidden_visual_claim, observations, re.IGNORECASE):
            raise CopilotResponseError("Visual evidence exceeded the observation-only boundary")

    validate_action(checked, context)
    validate_deadline_claims(checked, context)
    validate_incident_action(checked, context)
    return checked


def make_result_id(
    output: CopilotOutput,
    provider: str,
    model: str | None,
    model_revision: str | None,
    request_id: str,
) -> str:
    canonical = json.dumps(
        {
            "output": output.model_dump(),
            "provider": provider,
            "model": model,
            "model_revision": model_revision,
            "request_id": request_id,
            "prompt_version": COPILOT_PROMPT_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

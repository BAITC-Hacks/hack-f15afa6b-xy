"""Provider-neutral operator Copilot routes and runtime composition."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from clarification import get_received_clarifications
from copilot_contracts import (
    ACTIONS,
    CopilotAuthError,
    CopilotContext,
    CopilotError,
    CopilotQuotaError,
    CopilotRateLimitError,
    CopilotResponseError,
    CopilotResult,
    CopilotSchemaError,
    CopilotTimeout,
    CopilotUnavailable,
    sanitize_text,
)
from copilot_router import CircuitBreaker, ProviderRouter
from deterministic_copilot import DeterministicCopilot
from openai_copilot import OpenAICopilot
from qwen_copilot import MODEL, MODEL_REVISION, QwenCopilot


FEEDBACK_REASONS = {
    "wrong_category", "bad_language", "hallucination", "not_useful", "too_long", "wrong_tone"
}


class CopilotFeedback(BaseModel):
    result_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    helpful: bool
    reason: Literal[
        "wrong_category", "bad_language", "hallucination", "not_useful", "too_long", "wrong_tone"
    ] | None = None


class CopilotRequest(BaseModel):
    include_image: bool = False


def _integer(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be numeric") from None


def build_copilot_router() -> ProviderRouter:
    openai = OpenAICopilot.from_env()
    qwen = QwenCopilot.from_env()
    providers = {"deterministic": DeterministicCopilot()}
    if openai:
        providers["openai"] = openai
    if qwen:
        providers["qwen"] = qwen

    configured_primary = os.environ.get("P109_COPILOT_PROVIDER", "").strip().lower()
    if configured_primary and configured_primary not in {"openai", "qwen", "deterministic"}:
        raise ValueError("P109_COPILOT_PROVIDER must be openai, qwen or deterministic")
    primary = configured_primary or ("openai" if openai else "qwen" if qwen else "deterministic")
    configured_fallbacks = os.environ.get("P109_COPILOT_FALLBACKS", "").strip()
    if configured_fallbacks:
        fallbacks = [item.strip().lower() for item in configured_fallbacks.split(",") if item.strip()]
        if any(item not in {"openai", "qwen", "deterministic"} for item in fallbacks):
            raise ValueError("P109_COPILOT_FALLBACKS contains an unknown provider")
    else:
        fallbacks = ["qwen", "deterministic"] if primary == "openai" and qwen else ["deterministic"]

    circuit = CircuitBreaker(
        _integer("P109_AI_CIRCUIT_FAILURES", 5),
        _number("P109_AI_CIRCUIT_COOLDOWN", 30),
    )
    return ProviderRouter(
        providers,
        [primary, *fallbacks],
        circuit,
        retries=_integer("P109_AI_RETRIES", 1),
        openai_traffic_percent=_integer("P109_OPENAI_TRAFFIC_PERCENT", 100),
        shadow_qwen=os.environ.get("P109_COPILOT_SHADOW_QWEN", "0") == "1",
    )


def _mixed_reply(c: dict, suggested_response) -> str:
    if c.get("language") != "mixed":
        return suggested_response(c)
    kazakh_letters = sum(c.get("text", "").lower().count(letter) for letter in "әғқңөұүһі")
    if kazakh_letters:
        return ("Өтінішіңіз тіркелді. Оператор ақпаратты тексеріп, жауапты қызметке бағыттайды. "
                "Орындалу мерзімі әлі расталған жоқ.")
    return suggested_response(c)


def _context(c: dict, detail: dict, suggested_response, clarifications: list[str],
             image_data_url: str | None = None, budget_signals: list[str] | None = None) -> CopilotContext:
    ai = detail["triage"]
    needs_clarification = ai.get("confidence_band") == "low"
    fallback_action = (
        "clarify" if needs_clarification else
        "review_incident" if detail["incident_candidate"] else
        "prepare_reply"
    )
    signals = list(budget_signals or [])
    sources = ai.get("source_decisions") or {}
    source_categories = {item.get("category") for item in sources.values() if isinstance(item, dict)}
    if len(source_categories) > 1:
        signals.append("model_disagreement")
    if detail["incident_candidate"] and needs_clarification:
        signals.append("ambiguous_incident")
    confirmed_facts = {"decision_status": c["decision_status"]}
    if c["decision_status"] == "confirmed":
        confirmed_facts.update(category=c.get("topic"), service_id=c.get("service_id"))
    if c.get("incident_id"):
        confirmed_facts["incident_linked_by_operator"] = True
    return CopilotContext(
        language=c.get("language") if c.get("language") in {"ru", "kk", "mixed"} else "unknown",
        complaint=sanitize_text(c["text"], c.get("address"), min(
            6000, max(1, _integer("P109_AI_REQUEST_MAX_CONTEXT_CHARS", 6000)))),
        city=c.get("city_name"),
        district=c.get("district"),
        category=ai.get("category"),
        confidence_band=ai.get("confidence_band"),
        priority_signal=ai.get("urgency"),
        decision_status=c["decision_status"],
        incident_candidate=bool(detail["incident_candidate"]),
        clarifications=[sanitize_text(value) for value in clarifications],
        confirmed_facts=confirmed_facts,
        fallback_summary=ai.get("summary") or c["text"][:500],
        fallback_reasoning=ai.get("reasoning_short") or "Требуется проверка оператора.",
        fallback_reply=_mixed_reply(c, suggested_response),
        fallback_question=ai.get("clarification_question") if needs_clarification else None,
        fallback_action=fallback_action,
        complexity_signals=signals,
        request_type="multimodal" if image_data_url else "operator",
        image_data_url=image_data_url,
    )


def attach_copilot_routes(router, get_connection, complaint, detail, suggested_response, event, copilot,
                          image_loader=None):
    copilot = copilot or build_copilot_router()

    @router.post("/complaints/{cid}/copilot")
    def copilot_assist(cid: str, req: CopilotRequest):
        with get_connection() as conn:
            c = complaint(conn, cid)
            d = detail(conn, c)
            today = datetime.now(timezone.utc).date().isoformat()
            spent = 0.0
            for row in conn.execute("""SELECT payload FROM audit_events
                    WHERE event_type = 'copilot_generated' AND occurred_at >= ?""", (today,)):
                try:
                    spent += float(json.loads(row["payload"]).get("estimated_cost_usd") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
            soft_limit = _number("P109_AI_DAILY_SOFT_LIMIT_USD", 0)
            budget_signals = (["budget_degraded"] if soft_limit and spent >= soft_limit else [])
            if soft_limit and spent >= soft_limit * 2:
                budget_signals.append("budget_hard_degrade")
            image_data_url = None
            if req.include_image:
                if not image_loader:
                    raise HTTPException(422, "Изображения для ИИ не поддерживаются")
                image_data_url = image_loader(conn, cid)
            context = _context(c, d, suggested_response, get_received_clarifications(conn, cid),
                               image_data_url, budget_signals)
            result = copilot.assist(context).model_dump()
            usage = result["usage"]
            event(conn, cid, "copilot_generated", {
                "result_id": result["result_id"],
                "request_id": result["request_id"],
                "provider": result["provider"],
                "model": result["model"],
                "model_revision": result["model_revision"],
                "model_tier": result["model_tier"],
                "prompt_version": result["prompt_version"],
                "latency_ms": result["latency_ms"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cached_tokens": usage["cached_tokens"],
                "estimated_cost_usd": usage["estimated_cost_usd"],
                "request_type": context.request_type,
                "success": True,
                "recommended_action": result["recommended_action"],
                "mode": result["mode"],
                "shadow_comparison": result["shadow_comparison"],
            })
            return result

    @router.post("/complaints/{cid}/copilot/feedback")
    def copilot_feedback(cid: str, req: CopilotFeedback):
        with get_connection() as conn:
            complaint(conn, cid)
            generated = conn.execute("""SELECT payload FROM audit_events
                WHERE complaint_id = ? AND event_type = 'copilot_generated'
                ORDER BY rowid DESC LIMIT 1""", (cid,)).fetchone()
            payload = json.loads(generated["payload"]) if generated else {}
            if payload.get("result_id") != req.result_id:
                raise HTTPException(409, "Сначала получите актуальную рекомендацию ИИ")
            event(conn, cid, "copilot_feedback", {
                "result_id": req.result_id,
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "helpful": req.helpful,
                "reason": req.reason,
            })
        return {"saved": True}

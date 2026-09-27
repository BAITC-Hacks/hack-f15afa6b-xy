"""Private Qwen assistant for operator-facing summaries and reply drafts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException
from pydantic import BaseModel, Field

from clarification import get_received_clarifications


MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
ACTIONS = {"clarify", "prepare_reply", "review_incident", "manual_review"}


class CopilotError(RuntimeError):
    pass


class CopilotUnavailable(CopilotError):
    pass


class CopilotResponseError(CopilotError):
    pass


@dataclass(frozen=True)
class CopilotResult:
    summary: str
    reasoning: str
    suggested_reply: str
    clarification_question: str | None
    recommended_action: str
    model: str
    model_revision: str
    latency_ms: int
    result_id: str
    mode: str = "self_hosted_qwen"
    available: bool = True

    def dict(self):
        return asdict(self)


class CopilotFeedback(BaseModel):
    result_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    helpful: bool


def sanitize_text(text: str, address: str | None = None) -> str:
    """Remove common direct identifiers before text leaves the app process."""
    value = str(text or "")[:10000]
    if address and address.strip():
        value = re.sub(re.escape(address.strip()), "[АДРЕС]", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", value,
                   flags=re.IGNORECASE)
    value = re.sub(r"(?<!\d)(?:\d[\s-]?){12}(?!\d)", "[ИИН]", value)
    value = re.sub(r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)",
                   "[ТЕЛЕФОН]", value)
    return value.strip()[:6000]


class QwenCopilot:
    def __init__(self, base_url: str, api_key: str, model: str = MODEL,
                 revision: str = MODEL_REVISION, timeout: float = 18.0):
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
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("P109_COPILOT_REVISION must be a pinned commit hash")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model.strip() or MODEL
        self.revision = revision
        self.timeout = timeout

    @staticmethod
    def _private_host(host: str) -> bool:
        if host in {"localhost", "host.docker.internal"} or host.endswith((".internal", ".local")):
            return True
        try:
            return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
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
        return cls(base_url, os.environ.get("P109_COPILOT_API_KEY", ""),
                   os.environ.get("P109_COPILOT_MODEL", MODEL),
                   os.environ.get("P109_COPILOT_REVISION", MODEL_REVISION), timeout)

    def status(self):
        return {"configured": True, "mode": "self_hosted_qwen", "model": self.model,
                "model_revision": self.revision, "endpoint": "private"}

    def assist(self, context: dict) -> CopilotResult:
        schema = {
            "summary": "one sentence, at most 200 characters",
            "reasoning": "facts from context only, at most 240 characters",
            "suggested_reply": "polite draft, at most 400 characters",
            "clarification_question": "one question under 180 characters or null",
            "recommended_action": "clarify|prepare_reply|review_incident|manual_review",
        }
        language_style = (
            "Use natural standard Kazakh. Safe style example: «Өтінішіңіз тіркелді. Оператор мәліметтерді "
            "тексеріп, жауапты қызметке бағыттайды. Орындалу мерзімі әлі расталған жоқ.»"
            if context.get("language") == "kk" else
            "Use clear natural Russian suitable for a municipal service operator."
        )
        messages = [
            {"role": "system", "content": (
                "You are the Pulse 109 operator copilot for Kazakhstan. Use only the supplied facts. "
                "Reply in the complaint language (Russian or Kazakh). Never promise a deadline, invent a service action, "
                "guess a cause, or make the decision for the operator. Keep the whole response under 1200 characters. "
                "Use review_incident only when incident_candidate is true. If clarification_question is not null, "
                "recommended_action must be clarify; otherwise clarify is forbidden. " + language_style + " "
                "Return one JSON object and no markdown, matching this schema exactly: "
                + json.dumps(schema, ensure_ascii=False)
            )},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        payload = json.dumps({
            "model": self.model, "messages": messages, "temperature": 0,
            "max_tokens": 350, "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + "/v1/chat/completions", data=payload, method="POST", headers={
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json", "Accept": "application/json",
        })
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code >= 500:
                raise CopilotUnavailable(f"Qwen returned HTTP {error.code}") from None
            raise CopilotResponseError(f"Qwen rejected the request with HTTP {error.code}") from None
        except (TimeoutError, socket.timeout, URLError, ConnectionError):
            raise CopilotUnavailable("Qwen is unavailable or timed out") from None
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            response = json.loads(raw)
            content = response["choices"][0]["message"]["content"]
            answer = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise CopilotResponseError("Qwen returned invalid structured output") from None
        result = self._validate(answer, context)
        model = str(response.get("model") or self.model)
        canonical = json.dumps({**result, "model": model, "model_revision": self.revision},
                               ensure_ascii=False, sort_keys=True).encode("utf-8")
        return CopilotResult(**result, model=model, model_revision=self.revision,
                             latency_ms=latency_ms, result_id=hashlib.sha256(canonical).hexdigest())

    @staticmethod
    def _validate(answer: dict, context: dict) -> dict:
        required = {"summary", "reasoning", "suggested_reply", "clarification_question",
                    "recommended_action"}
        if not isinstance(answer, dict) or set(answer) != required:
            raise CopilotResponseError("Qwen output has an unexpected schema")
        limits = {"summary": 500, "reasoning": 500, "suggested_reply": 2000}
        cleaned = {}
        for field, limit in limits.items():
            value = answer.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                raise CopilotResponseError(f"Qwen output field {field!r} is invalid")
            cleaned[field] = value.strip()
        question = answer.get("clarification_question")
        if question is not None and (not isinstance(question, str) or not question.strip() or len(question.strip()) > 300):
            raise CopilotResponseError("Qwen clarification question is invalid")
        cleaned["clarification_question"] = question.strip() if isinstance(question, str) else None
        action = answer.get("recommended_action")
        question = cleaned["clarification_question"]
        if action not in ACTIONS or (action == "clarify") != bool(question):
            raise CopilotResponseError("Qwen recommended an unsupported action")
        if action == "review_incident" and not context.get("incident_candidate"):
            raise CopilotResponseError("Qwen recommended an unavailable incident review")
        cleaned["recommended_action"] = action
        return cleaned


def attach_copilot_routes(router, get_connection, complaint, detail, suggested_response, event, copilot):
    @router.post("/complaints/{cid}/copilot")
    def copilot_assist(cid: str):
        with get_connection() as conn:
            c = complaint(conn, cid)
            d = detail(conn, c)
            ai = d["triage"]
            needs_clarification = ai.get("confidence_band") == "low"
            fallback = {
                "summary": ai.get("summary") or c["text"][:500],
                "reasoning": ai.get("reasoning_short") or "Требуется проверка оператора.",
                "suggested_reply": suggested_response(c),
                "clarification_question": ai.get("clarification_question") if needs_clarification else None,
                "recommended_action": "clarify" if needs_clarification else
                                      "review_incident" if d["incident_candidate"] else "prepare_reply",
                "model": None, "latency_ms": None, "result_id": None,
                "mode": "deterministic_fallback", "available": False,
            }
            if not copilot:
                return {**fallback, "fallback_reason": "Self-hosted Qwen не настроен"}
            context = {
                "language": c["language"],
                "complaint": sanitize_text(c["text"], c.get("address")),
                "city": c.get("city_name"), "district": c.get("district"),
                "category": ai.get("category"), "confidence_band": ai.get("confidence_band"),
                "priority_signal": ai.get("urgency"), "decision_status": c["decision_status"],
                "incident_candidate": bool(d["incident_candidate"]),
                "clarifications": [sanitize_text(value) for value in get_received_clarifications(conn, cid)],
            }
            try:
                result = copilot.assist(context).dict()
            except CopilotError:
                return {**fallback, "fallback_reason": "Qwen временно недоступен; показан безопасный шаблон"}
            event(conn, cid, "copilot_generated", {
                "result_id": result["result_id"], "model": result["model"],
                "model_revision": result["model_revision"],
                "latency_ms": result["latency_ms"],
                "recommended_action": result["recommended_action"], "mode": result["mode"],
            }, "self_hosted_qwen")
            return result

    @router.post("/complaints/{cid}/copilot/feedback")
    def copilot_feedback(cid: str, req: CopilotFeedback):
        with get_connection() as conn:
            complaint(conn, cid)
            generated = conn.execute("""SELECT payload FROM audit_events
                WHERE complaint_id = ? AND event_type = 'copilot_generated'
                ORDER BY rowid DESC LIMIT 1""", (cid,)).fetchone()
            if not generated or json.loads(generated["payload"]).get("result_id") != req.result_id:
                raise HTTPException(409, "Сначала получите актуальную рекомендацию Copilot")
            event(conn, cid, "copilot_feedback", {
                "result_id": req.result_id, "helpful": req.helpful,
            })
        return {"saved": True}

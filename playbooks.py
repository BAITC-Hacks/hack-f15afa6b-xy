"""Operator playbooks: preview a plan, confirm it, apply it atomically.

A preview never changes a complaint. Execution takes the write lock first, then
re-reads the complaint and refuses a stale preview (its state fingerprint
changed), so the state change and its audit events land in one transaction.
Executed tokens are stored, so replaying one returns the recorded result.
Guards, routing and incident checks arrive as `ctx` callables from
workspace_api: playbooks reuse the same functions as the legacy endpoints.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import current_actor
from triage import SERVICE_NAMES
DEMO_DELIVERY = "demo_only"
PREVIEW_TTL_MINUTES = 30

# ponytail: exact-phrase FAQ list. Widen only with a reviewed phrasing list, never by substring.
CLOSE_FAQ = "close_faq"
REOPEN_FAQ = "reopen_faq"
FAQ_TEMPLATE_ID = "faq-status-1"
FAQ_TEMPLATE = (
    "Проверить статус обращения можно по его номеру в разделе «Кабинет гражданина» "
    "или у оператора 109. Срок рассмотрения подтверждает оператор после проверки."
)
FAQ_QUESTIONS = {
    "как проверить статус обращения", "как узнать статус обращения", "как посмотреть статус обращения",
    "где посмотреть статус обращения", "где узнать статус обращения", "как проверить статус заявки",
    "как узнать статус заявки", "өтінімнің статусын қалай білуге болады",
    "өтінімнің мәртебесін қалай білуге болады", "өтінімнің күйін қалай тексеруге болады",
    "өтініштің статусын қалай білуге болады",
    "өтініштің мәртебесін қалай тексеруге болады",
}
FAQ_BLOCK_TERMS = ["авари", "срочно", "жарылыс", "искрит", "запах гар", "опасно", "пожар",
                   "прорыв", "нет воды", "нет света", "нет тепла", "не работает", "течёт", "течет",
                   "су жоқ", "жылу жоқ", "жарық жоқ", "қауіп", "апат", "отключ"]

TITLES = {
    "link_mass_incident": "Связать с массовым инцидентом и подтвердить решение",
    "route_service": "Подтвердить решение и направить в службу",
    "request_clarification": "Запросить уточнение у гражданина",
    "close_faq": "Закрыть как типовой вопрос о статусе",
    "quarantine": "Отправить обращение в карантин",
    "restore": "Вернуть обращение в обычную очередь",
    "unlink_incident": "Снять связь с инцидентом",
    "reopen_faq": "Вернуть закрытый типовой вопрос в работу",
}

REASON_LABELS = {
    "unknown_place": "Нужен точный адрес или место", "unclear_event": "Непонятно, что именно произошло",
    "insufficient_detail": "Слишком мало деталей", "multiple_problems": "В одном обращении несколько проблем",
    "other": "Другая причина",
}


class PreviewRequest(BaseModel):
    topic: str | None = None
    priority: Literal["urgent", "normal"] | None = None
    incident_id: str | None = None
    question: str | None = Field(default=None, max_length=1000)
    reason: str | None = None
    operator_id: str | None = None
    manual: bool = False


class ExecuteRequest(BaseModel):
    preview_token: str = Field(min_length=8, max_length=200)


def audit_id(token: str, kind: str) -> str:
    """Deterministic audit id: the same token can never insert one event twice."""
    return "evt-" + hashlib.sha256(f"{token}:{kind}".encode()).hexdigest()[:20]


def token_tag(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:10]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[?!.,;:]+", " ", (text or "").lower().replace("ё", "е"))).strip()


def faq_closed(conn, complaint_id: str) -> bool:
    """True when this complaint was closed by the FAQ playbook (audited closure)."""
    for row in conn.execute(
        "SELECT payload FROM audit_events WHERE complaint_id = ? AND event_type = 'case_resolved'",
        (complaint_id,),
    ):
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("playbook") == CLOSE_FAQ:
            return True
    return False


def _writable(ctx, c) -> str | None:
    try:
        ctx["writable"](c)
    except HTTPException as exc:
        return str(exc.detail)
    return None


def _record(conn, ctx, cid, pid, actions, token, actor, manual=None):
    """One audit row for the operator's confirmation of the whole plan."""
    payload = {"playbook": pid, "actions": [a["label"] for a in actions], "preview": token_tag(token)}
    if manual is not None:
        payload["manual_override"] = manual
    ctx["event"](conn, cid, "playbook_executed", payload, actor, audit_id(token, "playbook_executed"))


def _routing(ctx, conn, c, topic: str):
    """Routing for the topic the operator actually confirmed, not for the demo proposal."""
    return ctx["routing"](c, {"category": topic, "suggested_service": ctx["services"][topic]},
                          ctx["operators"](conn))


def _state(ctx, conn, c):
    ai = ctx["analysis"](conn, c)
    return ai, ctx["routing"](c, ai, ctx["operators"](conn))


def _fingerprint(c, ai, routing, extra) -> str:
    operator = routing.get("operator") or {}
    payload = {
        "complaint": {key: c[key] for key in sorted(c)},
        "category": ai.get("category"),
        "band": ai.get("confidence_band"),
        "urgency": ai.get("urgency"),
        "operator": [operator.get("id"), operator.get("current_load"), operator.get("capacity"), operator.get("workload"), operator.get("workload_capacity")],
        "extra": extra,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _decision(ctx, conn, c, req, link: bool):
    """Shared plan for route_service and link_mass_incident (same guards as legacy /decide)."""
    pid = "link_mass_incident" if link else "route_service"
    services = ctx["services"]
    names = ctx["topic_names"]
    if req.topic is not None and req.topic not in services:
        raise HTTPException(422, "Неизвестная категория")
    blocked = _writable(ctx, c)
    if blocked:
        return [], blocked, None, None
    if c["decision_status"] == "confirmed":
        return [], "Решение уже подтверждено", None, None
    manual = bool(req.manual) and req.topic in services and req.priority in {"urgent", "normal"}
    ai = ctx["analysis"](conn, c)
    topic = req.topic or c["topic"] or (ai["category"] if ai and ai["confidence_band"] != "low" else None)
    priority = req.priority or c["priority"] or (ai["urgency"] if ai else None)
    if topic is None:
        return [], "Выберите категорию обращения", None, None
    if topic not in services:
        raise HTTPException(422, "Неизвестная категория")
    if priority not in {"normal", "urgent"}:
        if req.priority is not None:
            raise HTTPException(422, "Неизвестный приоритет")
        return [], "Приоритет не определён — выберите обычный или срочный", None, None
    if ai and ai["confidence_band"] == "low" and not req.manual:
        return [], ("Низкая уверенность демо-анализа: запросите уточнение или подтвердите "
                    "выбранную категорию вручную"), None, None
    linked = conn.execute("SELECT category FROM incidents WHERE id = ?", (c["incident_id"],)).fetchone() if c["incident_id"] else None
    if linked and linked[0] != topic:
        return [], "Сначала снимите связь с инцидентом другой категории", None, None
    incident_id, related = c["incident_id"], c["related_to"]
    if link:
        incident_id = req.incident_id or (ctx["detail"](conn, c)["incident_candidate"] or {}).get("id")
        if not incident_id:
            return [], "Нет подтверждённого кандидата инцидента — выберите инцидент", None, None
        if incident_id != c["incident_id"]:
            try:
                related = ctx["checked_incident"](
                    conn, c, incident_id, {**(ai or {}), "category": topic, "suggested_service": services[topic]})
            except HTTPException as exc:
                return [], str(exc.detail), None, None
    routing = _routing(ctx, conn, {**c, "topic": topic, "priority": priority, "incident_id": incident_id}, topic)
    operators = ctx["operators"](conn)
    recommended = routing.get("operator")
    if req.operator_id:
        chosen = next((o for o in operators if o["id"] == req.operator_id), None)
        if not chosen or not recommended or chosen["id"] != recommended["id"]:
            return [], "Рекомендация изменилась или оператор занят. Обновите карточку", None, None
    else:
        chosen = recommended
    service_name = SERVICE_NAMES.get(services[topic], services[topic])
    topic_label = names.get(topic, topic)
    actions = [
        {"label": "Категория", "value": topic_label},
        {"label": "Ответственная служба", "value": f"{service_name} · демо-маршрут, внешняя служба не уведомляется"},
        {"label": "Приоритет", "value": "Срочный" if priority == "urgent" else "Обычный"},
        {"label": "Оператор", "value": (f"{chosen['name']} · нагрузка {chosen['current_load']}/{chosen['capacity']}"
                                       if chosen else
                                       "Общая очередь: обращение остаётся видимым в очереди службы, "
                                       "внешнее уведомление не отправлено")},
        {"label": "Статус", "value": "Ожидает службу"},
        {"label": "Ответ гражданину (демо)", "value": ctx["suggested_response"]({**c, "incident_id": incident_id})},
    ]
    if req.manual:
        actions.insert(0, {"label": "Ручное исправление",
                           "value": "Категорию и приоритет выбрал оператор — рекомендации демо-анализа недостаточно"})
    if link:
        actions.insert(4, {"label": "Инцидент",
                           "value": f"{incident_id} · обращение присоединяется к массовому инциденту"})
    fingerprint_ai = ai or {"category": c["proposed_topic"], "confidence_band": "manual", "urgency": priority}
    state = (c, fingerprint_ai, routing, {"link": incident_id if link else None, "manual": manual,
                                         "priority": priority})

    suggested = ai["category"] if ai else c["proposed_topic"]
    def apply(conn, actor, token):
        now = datetime.now(timezone.utc).isoformat()
        _record(conn, ctx, c["id"], pid, actions, token, actor, manual=manual)
        if link:
            conn.execute(
                """UPDATE complaints SET topic = ?, service_id = ?, priority = ?, decision_status = 'confirmed',
                   assigned_operator = ?, incident_id = ?, related_to = ?, incident_dismissed = 0,
                   first_response_at = COALESCE(first_response_at, ?) WHERE id = ?""",
                (topic, services[topic], priority, chosen["id"] if chosen else None,
                 incident_id, related, now, c["id"]))
            ctx["event"](conn, c["id"], "incident_linked",
                         {"incident_id": incident_id, "related_to": related, "playbook": pid},
                         actor, audit_id(token, "incident_linked"))
        else:
            conn.execute(
                """UPDATE complaints SET topic = ?, service_id = ?, priority = ?, decision_status = 'confirmed',
                   assigned_operator = ?, first_response_at = COALESCE(first_response_at, ?) WHERE id = ?""",
                (topic, services[topic], priority, chosen["id"] if chosen else None, now, c["id"]))
        ctx["event"](conn, c["id"], "operator_confirmed",
                     {"topic": topic, "service_id": services[topic], "priority": priority,
                      "operator_id": chosen["id"] if chosen else None, "manual_override": manual,
                      "playbook": pid, "proposed_topic": suggested, "suggested_value": suggested,
                      "confirmed_value": topic, "provider": (ai or {}).get("provider", "manual"),
                      "provider_version": (ai or {}).get("provider_version"), "operator_override": bool(suggested and suggested != topic),
                      "language": c["language"],
                      "incident_id": incident_id if link else c["incident_id"]},
                     actor, audit_id(token, "operator_confirmed"))
        updated = ctx["complaint"](conn, c["id"])
        ctx["event"](conn, c["id"], "reply_saved",
                     {"text": ctx["suggested_response"](updated), "delivery": DEMO_DELIVERY, "playbook": pid},
                     actor, audit_id(token, "reply_saved"))
        return {"playbook": pid, "topic": topic, "service_id": services[topic], "priority": priority,
                "operator_id": chosen["id"] if chosen else None, "queued": chosen is None,
                "incident_id": incident_id if link else c["incident_id"],
                "manual_override": manual, "reply_saved": True, "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


def _clarification(ctx, conn, c, req):
    reason = req.reason or "insufficient_detail"
    if reason not in ctx["clarification_reasons"]:
        raise HTTPException(422, f"Invalid clarification reason: {reason}")
    blocked = _writable(ctx, c)
    if blocked:
        return [], blocked, None, None
    if c["decision_status"] != "pending":
        return [], "Обращение уже имеет решение оператора — уточнение недоступно", None, None
    ai, routing = _state(ctx, conn, c)
    question = (req.question or ai["clarification_question"] or "").strip()
    if not question:
        return [], "Нет вопроса для гражданина — напишите его вручную", None, None
    actions = [{"label": "Статус обращения", "value": "Ожидает уточнения от гражданина · решение не подтверждается"},
               {"label": "Вопрос гражданину", "value": question},
               {"label": "Причина", "value": REASON_LABELS.get(reason, reason)}]
    state = (c, ai, routing, {"question": question, "reason": reason})

    def apply(conn, actor, token):
        _record(conn, ctx, c["id"], "request_clarification", actions, token, actor)
        conn.execute("UPDATE complaints SET decision_status = 'needs_clarification' WHERE id = ?", (c["id"],))
        ctx["event"](conn, c["id"], "clarification_requested",
                     {"reason": reason, "question": question, "playbook": "request_clarification"},
                     actor, audit_id(token, "clarification_requested"))
        return {"playbook": "request_clarification", "decision_status": "needs_clarification",
                "question": question, "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


def _quarantine(ctx, conn, c, req, on: bool):
    pid = "quarantine" if on else "restore"
    if c["resolved_at"]:
        return [], "Обращение завершено — карантин недоступен", None, None
    if on and c["quarantined"]:
        return [], "Обращение уже в карантине", None, None
    if not on and not c["quarantined"]:
        return [], "Обращение не находится в карантине", None, None
    ai, routing = _state(ctx, conn, c)
    pairs = (("Карантин", "Обращение скрывается из обычной очереди, текст сохраняется"),
             ("Проверка", "Отметка проверки снимается: обращение вернётся на проверку оператора")) if on else (
             ("Возврат в очередь", "Обращение снова видно операторам, текст не меняется"),
             ("Проверка", "Проверку подтвердил оператор"))
    actions = [{"label": label, "value": value} for label, value in pairs]
    kind = "quarantined" if on else "safety_cleared"
    state = (c, ai, routing, {"on": on})

    def apply(conn, actor, token):
        _record(conn, ctx, c["id"], pid, actions, token, actor)
        conn.execute("UPDATE complaints SET quarantined = ?, safety_reviewed = ? WHERE id = ?",
                     (int(on), int(not on), c["id"]))
        ctx["event"](conn, c["id"], kind, {"playbook": pid}, actor, audit_id(token, kind))
        return {"playbook": pid, "quarantined": on, "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


def _unlink(ctx, conn, c, req):
    candidate = None
    if not c["incident_id"] and not c["related_to"]:
        candidate = ctx["detail"](conn, c)["incident_candidate"]
        if not candidate:
            return [], "Нет связи с инцидентом или кандидата на отказ", None, None
    ai, routing = _state(ctx, conn, c)
    if c["incident_id"]:
        actions = [{"label": "Связь", "value": f"Снимается связь с {c['incident_id']}"},
                   {"label": "Обращение", "value": "Остаётся отдельной проблемой; оригинал и история сохраняются"}]
    else:
        actions = [{"label": "Кандидат", "value": f"{candidate['id']} отклоняется как отдельная проблема"},
                   {"label": "Обращение", "value": "Повторные кандидаты этого инцидента скрываются"}]
    state = (c, ai, routing, {"incident_id": c["incident_id"]})

    def apply(conn, actor, token):
        _record(conn, ctx, c["id"], "unlink_incident", actions, token, actor)
        conn.execute("""UPDATE complaints SET incident_id = NULL, related_to = NULL, incident_dismissed = 1
                        WHERE id = ?""", (c["id"],))
        ctx["event"](conn, c["id"], "incident_rejected",
                     {"previous_incident_id": c["incident_id"], "playbook": "unlink_incident"},
                     actor, audit_id(token, "incident_rejected"))
        return {"playbook": "unlink_incident", "incident_id": None, "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


def _close_faq(ctx, conn, c, req):
    blocked = _writable(ctx, c)
    if blocked:
        return [], blocked, None, None
    text = normalize(c["text"])
    if text not in FAQ_QUESTIONS:
        return [], "Только точная формулировка вопроса о статусе закрывается автоматически", None, None
    if any(term in text for term in FAQ_BLOCK_TERMS):
        return [], "В вопросе есть признак происшествия — закрытие недоступно", None, None
    detail = ctx["detail"](conn, c)
    ai = detail["triage"]
    if c["priority"] == "urgent" or c["proposed_priority"] == "urgent" or ai["urgency"] == "urgent":
        return [], "Обращение отмечено как срочное — закрытие недоступно", None, None
    if c["incident_id"] or c["duplicate_of"]:
        return [], "Обращение связано с инцидентом или дублем — закрытие недоступно", None, None
    if {"spam_suspected", "out_of_scope"} & set(detail["flags"]):
        return [], "Есть признаки спама или выхода за рамки 109 — проверьте вручную", None, None
    if c["decision_status"] != "pending":
        return [], "Обращение уже имеет решение оператора — закрытие недоступно", None, None
    actions = [{"label": "Типовой вопрос", "value": "Информационный вопрос о статусе; подтверждает оператор"},
               {"label": "Утверждённый шаблон", "value": f"«Как проверить статус обращения»: {FAQ_TEMPLATE}"},
               {"label": "Что меняется", "value": "Обращение завершается; категория, служба и приоритет не назначаются"}]
    state = (c, ai, detail["routing"], {"faq": True})

    def apply(conn, actor, token):
        now = datetime.now(timezone.utc).isoformat()
        _record(conn, ctx, c["id"], CLOSE_FAQ, actions, token, actor)
        conn.execute("UPDATE complaints SET resolved_at = ?, resolution_text = ? WHERE id = ?",
                     (now, FAQ_TEMPLATE, c["id"]))
        ctx["event"](conn, c["id"], "case_resolved",
                     {"playbook": CLOSE_FAQ, "template_id": FAQ_TEMPLATE_ID, "resolution_text": FAQ_TEMPLATE,
                      "resolved_at": now}, actor, audit_id(token, "case_resolved"))
        return {"playbook": CLOSE_FAQ, "status": "resolved", "template_id": FAQ_TEMPLATE_ID,
                "resolution_text": FAQ_TEMPLATE, "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


def _reopen_faq(ctx, conn, c, req):
    if not faq_closed(conn, c["id"]):
        return [], "Обращение закрыто вне сценария FAQ — автоматическое возвращение недоступно", None, None
    if not c["resolved_at"]:
        return [], "Обращение уже возвращено в работу", None, None
    ai, routing = _state(ctx, conn, c)
    actions = [{"label": "Возврат", "value": "Обращение снова в работе; шаблон не остаётся решением"},
               {"label": "Аудит", "value": f"Возврат записывается в историю · было закрыто {c['resolved_at']}"}]
    state = (c, ai, routing, {"faq_closed_at": c["resolved_at"]})

    def apply(conn, actor, token):
        _record(conn, ctx, c["id"], REOPEN_FAQ, actions, token, actor)
        conn.execute("UPDATE complaints SET resolved_at = NULL, resolution_text = NULL WHERE id = ?", (c["id"],))
        ctx["event"](conn, c["id"], "case_reopened",
                     {"playbook": REOPEN_FAQ, "previous_resolved_at": c["resolved_at"],
                      "decision_status": c["decision_status"], "reason": req.reason},
                     actor, audit_id(token, "case_reopened"))
        return {"playbook": REOPEN_FAQ, "resolved_at": None, "decision_status": c["decision_status"],
                "delivery": DEMO_DELIVERY}

    return actions, None, state, apply


PLANS = {
    "link_mass_incident": lambda ctx, conn, c, req: _decision(ctx, conn, c, req, link=True),
    "route_service": lambda ctx, conn, c, req: _decision(ctx, conn, c, req, link=False),
    "request_clarification": _clarification,
    "close_faq": _close_faq,
    "reopen_faq": _reopen_faq,
    "unlink_incident": _unlink,
    "quarantine": lambda ctx, conn, c, req: _quarantine(ctx, conn, c, req, on=True),
    "restore": lambda ctx, conn, c, req: _quarantine(ctx, conn, c, req, on=False),
}


def build(pid: str, ctx, conn, c, req: PreviewRequest) -> dict:
    """Current plan for one playbook. Reads only; the caller may store a preview token for it."""
    if pid not in PLANS:
        raise HTTPException(404, "Сценарий не найден")
    actions, blocked, state, apply = PLANS[pid](ctx, conn, c, req)
    return {"id": pid, "title": TITLES[pid], "actions": actions, "can_execute": not blocked,
            "blocked_reason": blocked, "fingerprint": _fingerprint(*state) if not blocked else "",
            "apply": apply}


def attach_playbook_routes(router: APIRouter, get_connection, ctx: dict) -> None:
    """Register the preview/execute endpoints on the existing workspace router."""

    @router.get("/complaints/{cid}/playbooks")
    def playbooks(cid: str):
        with get_connection() as conn:
            c = ctx["complaint"](conn, cid)
            plans = [build(pid, ctx, conn, c, PreviewRequest()) for pid in TITLES]
        return {"items": [{"id": p["id"], "title": p["title"], "can_execute": p["can_execute"],
                           "blocked_reason": p["blocked_reason"]} for p in plans],
                "delivery": DEMO_DELIVERY}

    @router.post("/complaints/{cid}/playbooks/{pid}/preview")
    def preview(cid: str, pid: str, req: PreviewRequest):
        if pid not in TITLES:
            raise HTTPException(404, "Сценарий не найден")
        with get_connection() as conn:
            c = ctx["complaint"](conn, cid)
            plan = build(pid, ctx, conn, c, req)
            token = None
            if plan["can_execute"]:
                token = "pb-" + uuid.uuid4().hex
                now = datetime.now(timezone.utc)
                conn.execute("DELETE FROM playbook_previews WHERE created_at < ?",
                             ((now - timedelta(days=7)).isoformat(),))
                conn.execute(
                    "INSERT INTO playbook_previews "
                    "(token, complaint_id, playbook_id, request, fingerprint, created_at, actor) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (token, cid, pid, req.model_dump_json(), plan["fingerprint"], now.isoformat(),
                     current_actor()),
                )
                conn.commit()
            return {"id": plan["id"], "title": plan["title"], "actions": plan["actions"],
                    "can_execute": plan["can_execute"], "blocked_reason": plan["blocked_reason"],
                    "complaint_id": cid, "preview_token": token, "manual": req.manual,
                    "delivery": DEMO_DELIVERY}

    @router.post("/complaints/{cid}/playbooks/{pid}/execute")
    def execute(cid: str, pid: str, req: ExecuteRequest):
        if pid not in TITLES:
            raise HTTPException(404, "Сценарий не найден")
        with get_connection() as conn:
            # The write lock comes first: state, token and capacity are re-read and the plan is
            # applied under one lock, so two tokens cannot both pass the checks and then write.
            conn.execute("BEGIN IMMEDIATE")
            try:
                c = ctx["complaint"](conn, cid)
                row = conn.execute("SELECT * FROM playbook_previews WHERE token = ?", (req.preview_token,)).fetchone()
                if not row:
                    raise HTTPException(409, "Preview не найден или устарел — обновите preview")
                if row["complaint_id"] != cid or row["playbook_id"] != pid:
                    raise HTTPException(409, "Preview относится к другому обращению или сценарию — обновите preview")
                if row["actor"] and row["actor"] != current_actor():
                    raise HTTPException(403, "Preview создан другим оператором — создайте новый preview")
                if row["executed_at"]:
                    conn.rollback()
                    return {"playbook": pid, "replayed": True, "result": json.loads(row["result"]),
                            "complaint": c, "delivery": DEMO_DELIVERY}
                created = datetime.fromisoformat(row["created_at"])
                if datetime.now(timezone.utc) - created > timedelta(minutes=PREVIEW_TTL_MINUTES):
                    raise HTTPException(409, "Preview устарел — обновите preview")
                plan = build(pid, ctx, conn, c, PreviewRequest(**json.loads(row["request"])))
                if not plan["can_execute"]:
                    raise HTTPException(409, f"{plan['blocked_reason']} — обновите preview")
                if plan["fingerprint"] != row["fingerprint"]:
                    raise HTTPException(409, "Состояние обращения изменилось после проверки — обновите preview")
                result = plan["apply"](conn, current_actor(), req.preview_token)
                conn.execute("UPDATE playbook_previews SET executed_at = ?, result = ? WHERE token = ?",
                             (datetime.now(timezone.utc).isoformat(),
                              json.dumps(result, ensure_ascii=False), req.preview_token))
                updated = ctx["complaint"](conn, cid)
                conn.commit()
            except HTTPException:
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                raise HTTPException(409, "Сценарий не выполнен целиком, изменения отменены — обновите preview") from None
            return {"playbook": pid, "replayed": False, "result": result,
                    "complaint": updated, "delivery": DEMO_DELIVERY}

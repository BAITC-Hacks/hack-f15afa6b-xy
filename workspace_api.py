"""Operator workspace on the existing complaint and audit tables."""
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clarification import get_received_clarifications
from demo_data import seed_workspace
from triage import (analyze, address_in, operators_with_load, queue_state, related_cases,
                    risk_for, route, SERVICE_NAMES, moment)


class Intake(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    region_id: str
    language: Literal["ru", "kk", "mixed", "unknown"] = "ru"
    address: str | None = Field(default=None, max_length=200)
    district: str | None = Field(default=None, max_length=100)
    channel: Literal["web", "phone", "telegram", "whatsapp"] = "web"
    sender_key: str | None = Field(default=None, max_length=100)


class Decision(BaseModel):
    topic: str
    priority: Literal["urgent", "normal"]
    operator_id: str | None = None
    incident_id: str | None = None


class Link(BaseModel):
    incident_id: str | None = None
    separate: bool = False


class Safety(BaseModel):
    quarantine: bool


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Subscription(BaseModel):
    subscriber_key: str = Field(min_length=8, max_length=100)


def event(conn, cid, kind, payload, actor="operator_demo"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("evt-" + uuid.uuid4().hex, cid, kind, now, now, actor, json.dumps(payload, ensure_ascii=False)))


def build_workspace_router(get_connection, classifier, topic_services, valid_regions):
    router = APIRouter(prefix="/api/workspace")

    def complaint(conn, cid):
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Обращение не найдено")
        return dict(row)

    def incident_list(conn):
        result = []
        for row in conn.execute("SELECT * FROM incidents ORDER BY started_at DESC"):
            item = dict(row)
            members = conn.execute("SELECT id, address, text FROM complaints WHERE incident_id = ?", (item["id"],)).fetchall()
            item.update(count=len(members), members=[dict(r) for r in members],
                        streets=len({(r["address"] or "").rsplit(" ", 1)[0] for r in members if r["address"]}),
                        minutes=round((datetime.now(timezone.utc) - moment(item["started_at"])).total_seconds() / 60),
                        service_name=SERVICE_NAMES.get(item["service_id"], item["service_id"]))
            result.append(item)
        return result

    def detail(conn, c, rows=None, operators=None):
        rows = rows if rows is not None else [dict(r) for r in conn.execute("SELECT * FROM complaints")]
        ai = analyze(c, classifier, topic_services, "\n".join(get_received_clarifications(conn, c["id"])))
        similar = related_cases(c, ai, rows, classifier, topic_services)
        incidents = incident_list(conn)
        candidate_ids = [r["incident_id"] for r in similar if r["incident_id"]]
        candidate = next((i for i in incidents if i["id"] in candidate_ids and i["status"] != "Завершён"), None)
        risk = risk_for(c, rows)
        ops = operators if operators is not None else operators_with_load(conn)
        routing = route(c, ai, ops)
        assigned = next((o for o in ops if o["id"] == c["assigned_operator"]), None)
        if assigned:
            routing = {"operator": assigned, "reason": "Назначение подтверждено оператором.", "level": None, "assigned": True}
        response = ("Ваше обращение зарегистрировано. Оно связано с инцидентом " + c["incident_id"] + ". Ответственная служба уведомлена в демо-системе. Срок устранения пока не подтверждён."
                    if c["incident_id"] else "Ваше обращение зарегистрировано. Оператор проверит информацию и направит её в ответственную службу. Срок устранения пока не подтверждён.")
        return {"complaint": c, "triage": ai, "similar": similar, "incident_candidate": candidate,
                "routing": routing, "risk": risk, "suggested_response": response,
                **queue_state(c, ai, risk, bool(similar) or bool(c["incident_id"]))}

    def writable(c):
        if c["quarantined"] or c["resolved_at"] or c["decision_status"] == "needs_clarification":
            raise HTTPException(409, "Верните обращение в обработку перед принятием решения")

    def checked_incident(conn, c, iid, ai):
        inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
        if not inc:
            raise HTTPException(404, "Инцидент не найден")
        d = detail(conn, c)
        if not d["incident_candidate"] or d["incident_candidate"]["id"] != iid or ai["category"] != inc["category"]:
            raise HTTPException(409, "Не совпадают категория, район, время или характер проблемы")
        return next((r["id"] for r in d["similar"] if r["incident_id"] == iid), None)

    @router.post("/seed")
    def seed():
        with get_connection() as conn:
            seed_workspace(conn, topic_services)
        return {"data_origin": "synthetic", "seed": "operator_demo_v1"}

    @router.get("/queue")
    def queue():
        with get_connection() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM complaints")]
            ops = operators_with_load(conn)
            items = [detail(conn, c, rows, ops) for c in rows]
        items.sort(key=lambda x: (-x["priority_score"], x["complaint"]["ingested_at"], x["complaint"]["id"]))
        return {"items": items, "data_origin": "synthetic", "score_method": "urgency + SLA risk + waiting + incident + review"}

    @router.get("/operators")
    def operators():
        with get_connection() as conn:
            return {"items": operators_with_load(conn)}

    @router.get("/incidents")
    def incidents():
        with get_connection() as conn:
            return {"items": incident_list(conn)}

    @router.post("/intake", status_code=201)
    def intake(req: Intake):
        if not req.text.strip() or req.region_id not in valid_regions:
            raise HTTPException(422, "Укажите текст обращения и известный регион")
        cid = "PULSE-" + uuid.uuid4().hex[:6].upper()
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute("""INSERT INTO complaints
                (id, data_origin, source_system, text, region_id, received_at, ingested_at,
                 language, address, district, channel, sender_key)
                VALUES (?, 'synthetic', 'operator_demo_intake', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, req.text.strip(), req.region_id, now, now, req.language,
                 req.address.strip() if req.address else address_in(req.text), req.district, req.channel, req.sender_key))
            event(conn, cid, "intake", {"channel": req.channel, "text_len": len(req.text)}, "citizen_demo")
        return {"id": cid, "data_origin": "synthetic", "decision_status": "pending"}

    @router.post("/complaints/{cid}/triage")
    def triage(cid: str):
        with get_connection() as conn:
            c = complaint(conn, cid)
            result = detail(conn, c)
            ai = result["triage"]
            conn.execute("UPDATE complaints SET proposed_topic = ?, proposed_service_id = ?, proposed_priority = ? WHERE id = ?",
                         (ai["category"], ai["suggested_service"], ai["urgency"], cid))
            event(conn, cid, "classification_proposed", {"topic": ai["category"], "confidence_kind": "synthetic_demo", "category_confidence": ai["category_confidence"]}, "rules_demo")
            return result

    @router.post("/complaints/{cid}/decide")
    def decide(cid: str, req: Decision):
        if req.topic not in topic_services:
            raise HTTPException(422, "Неизвестная категория")
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            c = complaint(conn, cid)
            writable(c)
            if c["decision_status"] == "confirmed":
                raise HTTPException(409, "Решение уже подтверждено")
            d = detail(conn, c)
            if d["triage"]["confidence_band"] == "low":
                raise HTTPException(409, "Сначала запросите уточнение и повторите анализ")
            if req.operator_id:
                selected_ai = {**d["triage"], "category": req.topic, "suggested_service": topic_services[req.topic]}
                ops = operators_with_load(conn)
                op = next((o for o in ops if o["id"] == req.operator_id), None)
                recommended = route(c, selected_ai, ops)["operator"]
                if not op or not recommended or op["id"] != recommended["id"]:
                    raise HTTPException(409, "Рекомендация изменилась или оператор занят. Обновите карточку")
            related = c["related_to"]
            if req.incident_id and req.incident_id != c["incident_id"]:
                related = checked_incident(conn, c, req.incident_id, {**d["triage"], "category": req.topic})
                event(conn, cid, "incident_linked", {"incident_id": req.incident_id, "related_to": related})
            if c["incident_id"]:
                inc = conn.execute("SELECT category FROM incidents WHERE id = ?", (c["incident_id"],)).fetchone()
                if inc and inc[0] != req.topic:
                    raise HTTPException(409, "Сначала снимите связь с инцидентом другой категории")
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""UPDATE complaints SET topic = ?, service_id = ?, priority = ?, decision_status = 'confirmed',
                assigned_operator = ?, incident_id = ?, related_to = ?, first_response_at = COALESCE(first_response_at, ?) WHERE id = ?""",
                (req.topic, topic_services[req.topic], req.priority, req.operator_id, req.incident_id or c["incident_id"], related, now, cid))
            event(conn, cid, "operator_confirmed", {**req.model_dump(), "proposed_topic": d["triage"]["category"]})
            return {"complaint": complaint(conn, cid)}

    @router.get("/complaints/{cid}/routing")
    def recommend(cid: str, topic: str):
        if topic not in topic_services:
            raise HTTPException(422, "Неизвестная категория")
        with get_connection() as conn:
            c = complaint(conn, cid)
            ai = {"category": topic, "suggested_service": topic_services[topic]}
            return {"routing": route(c, ai, operators_with_load(conn)),
                    "service_name": SERVICE_NAMES[topic_services[topic]] if c["region_id"] == "KZ-ALA" else "Региональная очередь — служба требует проверки"}

    @router.post("/complaints/{cid}/link")
    def link(cid: str, req: Link):
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            c = complaint(conn, cid)
            writable(c)
            if req.separate:
                conn.execute("UPDATE complaints SET incident_id = NULL, related_to = NULL, incident_dismissed = 1 WHERE id = ?", (cid,))
                event(conn, cid, "incident_rejected", {"previous_incident_id": c["incident_id"]})
            else:
                if not req.incident_id:
                    raise HTTPException(422, "Выберите инцидент")
                if c["incident_id"] != req.incident_id:
                    root = checked_incident(conn, c, req.incident_id, detail(conn, c)["triage"])
                    conn.execute("UPDATE complaints SET incident_id = ?, related_to = ?, incident_dismissed = 0 WHERE id = ?", (req.incident_id, root, cid))
                    event(conn, cid, "incident_linked", {"incident_id": req.incident_id, "related_to": root})
            return {"complaint": complaint(conn, cid)}

    @router.post("/complaints/{cid}/safety")
    def safety(cid: str, req: Safety):
        with get_connection() as conn:
            complaint(conn, cid)
            conn.execute("UPDATE complaints SET quarantined = ?, safety_reviewed = ? WHERE id = ?", (int(req.quarantine), int(not req.quarantine), cid))
            event(conn, cid, "quarantined" if req.quarantine else "safety_cleared", {})
            return {"complaint": complaint(conn, cid)}

    @router.post("/complaints/{cid}/reply")
    def reply(cid: str, req: Reply):
        if not req.text.strip():
            raise HTTPException(422, "Ответ не может быть пустым")
        with get_connection() as conn:
            c = complaint(conn, cid)
            if c["quarantined"]:
                raise HTTPException(409, "Обращение находится в карантине")
            event(conn, cid, "reply_saved", {"text": req.text.strip(), "delivery": "demo_only"})
        return {"saved": True, "delivery": "demo_only"}

    @router.post("/incidents/{iid}/subscribe")
    def subscribe(iid: str, req: Subscription):
        with get_connection() as conn:
            if not conn.execute("SELECT id FROM incidents WHERE id = ?", (iid,)).fetchone():
                raise HTTPException(404, "Инцидент не найден")
            conn.execute("INSERT OR IGNORE INTO incident_subscriptions VALUES (?, ?, ?)", (iid, req.subscriber_key, datetime.now(timezone.utc).isoformat()))
            count = conn.execute("SELECT COUNT(*) FROM incident_subscriptions WHERE incident_id = ?", (iid,)).fetchone()[0]
        return {"subscribed": True, "subscribers": count, "delivery": "demo_only"}

    @router.get("/metrics")
    def metrics():
        data = queue()["items"]
        today = datetime.now(timezone.utc).date()
        today_items = [x for x in data if moment(x["complaint"]["ingested_at"]).date() == today]
        response_times = [(moment(x["complaint"]["first_response_at"]) - moment(x["complaint"]["ingested_at"])).total_seconds()
                          for x in data if x["complaint"]["first_response_at"]]
        with get_connection() as conn:
            ops = [o for o in operators_with_load(conn) if o["status"] == "online"]
            incs = incident_list(conn)
            subscriptions = conn.execute("SELECT COUNT(*) FROM incident_subscriptions").fetchone()[0]
        linked = sum(bool(x["complaint"]["incident_id"]) for x in data)
        consolidated = sum(max(0, i["count"] - 1) for i in incs)
        return {"data_origin": "synthetic", "total": len(data), "today": len(today_items),
                "pending": sum(x["group"] in {"urgent", "attention", "normal"} for x in data),
                "active_incidents": sum(i["status"] != "Завершён" for i in incs), "linked": linked,
                "consolidated": consolidated, "quarantined": sum(x["group"] == "quarantine" for x in data),
                "sla_at_risk": sum(x["sla_remaining"] is not None and x["sla_remaining"] < 10 for x in data),
                "average_response_seconds": round(sum(response_times) / len(response_times)) if response_times else None,
                "operator_load": round(100 * sum(o["current_load"] for o in ops) / max(1, sum(o["capacity"] for o in ops))),
                "high_confidence_percent": round(100 * sum(x["triage"]["confidence_band"] == "high" for x in data) / max(1, len(data))),
                "categories": dict(Counter(x["triage"]["category"] or "unknown" for x in data)),
                "subscriptions": subscriptions, "time_saved_percent": None,
                "note": "Счётчики синтетической SQLite. Экономия времени не измерялась."}

    return router

"""Operator workspace on the existing complaint and audit tables."""
import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from auth import current_actor
from clarification import VALID_CLARIFICATION_REASONS, get_received_clarifications
from demo_data import seed_workspace
from playbooks import attach_playbook_routes
from triage import (analyze, address_in, operators_with_load, queue_state, related_cases,
                    risk_for, route, SERVICE_NAMES, moment)


class Intake(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    region_id: str
    language: Literal["ru", "kk", "mixed", "unknown"] = "ru"
    address: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    district: str | None = Field(default=None, max_length=100)
    channel: Literal["web", "phone", "telegram", "whatsapp"] = "web"
    sender_key: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def location_is_complete(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите широту и долготу вместе")
        if self.location_accuracy_m is not None and self.latitude is None:
            raise ValueError("Точность требует координаты")
        return self


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


GEOCODE_CITIES = {
    "KZ-ALA": {"name": "Алматы", "viewbox": "76.55,42.95,77.45,43.55", "bounds": (42.95, 43.55, 76.55, 77.45)},
    "KZ-AST": {"name": "Астана", "viewbox": "70.90,50.80,72.00,51.40", "bounds": (50.80, 51.40, 70.90, 72.00)},
    "KZ-SHY": {"name": "Шымкент", "viewbox": "69.20,42.10,70.00,42.60", "bounds": (42.10, 42.60, 69.20, 70.00)},
}


def normalized_address_query(value: str, city: str = "Алматы") -> str:
    clean = " ".join(value.strip().split())
    microdistrict = re.fullmatch(
        r"(?:(?:мкр|микрорайон)\.?\s*)?(.+?[-\s]\d+)(?:\s*,\s*|\s+)(?:(?:дом|д|үй)\.?\s*)?(\d+[A-Za-zА-Яа-я/-]*)",
        clean,
        re.IGNORECASE,
    )
    if microdistrict:
        clean = f"микрорайон {microdistrict.group(1)}, {microdistrict.group(2)}"
    return clean if city.casefold() in clean.casefold() else clean + ", " + city


def event(conn, cid, kind, payload, actor=None, event_id=None):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (event_id or "evt-" + uuid.uuid4().hex, cid, kind, now, now, actor or current_actor(),
                  json.dumps(payload, ensure_ascii=False)))


def suggested_response(c):
    return ("Ваше обращение зарегистрировано. Оно связано с инцидентом " + c["incident_id"] + ". Ответственная служба уведомлена в демо-системе. Срок устранения пока не подтверждён."
            if c["incident_id"] else "Ваше обращение зарегистрировано. Оператор проверит информацию и направит её в ответственную службу. Срок устранения пока не подтверждён.")


def build_workspace_router(get_connection, classifier, topic_services, valid_regions, topic_names=None,
                           decision_service=None):
    router = APIRouter(prefix="/api/workspace")
    geocode_cache = {}
    geocode_lock = threading.Lock()
    last_geocode_at = [0.0]

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

    def analysis(conn, c):
        """Read the latest stored triage; never call an external model while listing the queue."""
        extra = "\n".join(get_received_clarifications(conn, c["id"]))
        if decision_service:
            row = conn.execute("""SELECT payload FROM audit_events
                WHERE complaint_id = ? AND event_type = 'classification_proposed'
                ORDER BY rowid DESC LIMIT 1""", (c["id"],)).fetchone()
            if row:
                try:
                    contract = json.loads(row["payload"]).get("triage_contract")
                    if contract:
                        return decision_service.view_from_contract(c, extra, contract)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            return decision_service.existing_view(c, extra)
        return analyze(c, classifier, topic_services, extra)

    def routing_for(c, ai, ops):
        if ai.get("decision_mode") == "MODEL_DISAGREEMENT":
            return {"operator": None, "reason": "Сначала подтвердите категорию при расхождении моделей.",
                    "level": None, "route_type": "review", "queue": True, "candidates": []}
        routing = route(c, ai, ops)
        assigned = next((o for o in ops if o["id"] == c["assigned_operator"]), None)
        if assigned:
            routing = {"operator": assigned, "reason": "Назначение подтверждено оператором.", "level": None, "assigned": True}
        return routing

    def detail(conn, c, rows=None, operators=None):
        rows = rows if rows is not None else [dict(r) for r in conn.execute("SELECT * FROM complaints")]
        ai = analysis(conn, c)
        similar = related_cases(c, ai, rows, classifier, topic_services)
        incidents = incident_list(conn)
        candidate_ids = [r["incident_id"] for r in similar if r["incident_id"]]
        candidate = next((i for i in incidents if i["id"] in candidate_ids and i["status"] != "Завершён"), None)
        risk = risk_for(c, rows)
        spam = ai.get("spam_suspected") or {}
        if spam.get("value") and not c.get("safety_reviewed"):
            risk["reasons"].append(f"Laya: подозрение на спам {round(100 * spam['probability_true'])}%")
            risk["score"] = max(risk["score"], spam["probability_true"])
            risk["kind"] = "deterministic_plus_laya_signal"
        ops = operators if operators is not None else operators_with_load(conn)
        routing = routing_for(c, ai, ops)
        return {"complaint": c, "triage": ai, "similar": similar, "incident_candidate": candidate,
                "routing": routing, "risk": risk, "suggested_response": suggested_response(c),
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

    @router.get("/geocode")
    def geocode(q: str = Query(min_length=3, max_length=200), region_id: str = "KZ-ALA"):
        city = GEOCODE_CITIES.get(region_id)
        if not city:
            raise HTTPException(422, "Поиск по карте доступен для Алматы, Астаны и Шымкента")
        query = normalized_address_query(q, city["name"])
        key = region_id + ":" + query.casefold()
        if key in geocode_cache:
            return {"query": query, "items": geocode_cache[key], "cached": True}
        with geocode_lock:
            if key in geocode_cache:
                return {"query": query, "items": geocode_cache[key], "cached": True}
            delay = 1 - (time.monotonic() - last_geocode_at[0])
            if delay > 0:
                time.sleep(delay)
            params = urlencode({
                "q": query, "format": "jsonv2", "limit": 5, "countrycodes": "kz",
                "viewbox": city["viewbox"], "bounded": 1, "addressdetails": 1,
                "accept-language": "ru,kk",
            })
            base_url = os.environ.get("P109_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
            request = Request(base_url + "?" + params, headers={
                "User-Agent": "Pulse109/0.1 (+https://github.com/Eliasans02/pulse109)",
                "Accept": "application/json",
            })
            try:
                with urlopen(request, timeout=6) as response:
                    payload = json.load(response)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
                raise HTTPException(503, "Поиск адреса временно недоступен. Выберите точку вручную.") from error
            finally:
                last_geocode_at[0] = time.monotonic()
            items = []
            south, north, west, east = city["bounds"]
            for result in payload if isinstance(payload, list) else []:
                try:
                    latitude, longitude = float(result["lat"]), float(result["lon"])
                    bounds = [float(value) for value in result.get("boundingbox", [])]
                except (KeyError, TypeError, ValueError):
                    continue
                if south <= latitude <= north and west <= longitude <= east:
                    address = result.get("address") if isinstance(result.get("address"), dict) else {}
                    district = next((address.get(name) for name in ("city_district", "borough", "district")
                                     if address.get(name)), None)
                    if district:
                        district = re.sub(r"\s+район$", "", str(district), flags=re.IGNORECASE)
                    items.append({"label": str(result.get("display_name") or query)[:300],
                                  "latitude": latitude, "longitude": longitude,
                                  "district": district,
                                  "bounds": bounds if len(bounds) == 4 else None})
            geocode_cache[key] = items
        return {"query": query, "items": items, "cached": False}

    @router.get("/tracking/{cid}")
    def tracking(cid: str):
        with get_connection() as conn:
            cid = cid.strip()
            c = complaint(conn, cid.upper() if cid.upper().startswith("PULSE-") else cid)
            # ponytail: number-only lookup is synthetic-only; real records need owner authentication.
            if c["data_origin"] != "synthetic":
                raise HTTPException(404, "Обращение не найдено")
            status = "resolved" if c["resolved_at"] else "under_review" if c["quarantined"] else c["decision_status"]
            updates = []
            for row in conn.execute("""SELECT event_type, occurred_at, payload FROM audit_events
                    WHERE complaint_id = ? AND event_type IN ('reply_saved', 'clarification_requested', 'clarification_received')
                    ORDER BY occurred_at, recorded_at, rowid""", (c["id"],)):
                payload = json.loads(row["payload"])
                updates.append({"type": row["event_type"], "at": row["occurred_at"],
                                "text": payload.get("question") if row["event_type"] == "clarification_requested" else payload.get("text")})
            if status == "needs_clarification" and updates:
                clarification = [u for u in updates if u["type"].startswith("clarification_")]
                if clarification and clarification[-1]["type"] == "clarification_received":
                    status = "clarification_received"
            incident = conn.execute("SELECT id, title, status, next_update FROM incidents WHERE id = ?", (c["incident_id"],)).fetchone()
            service = SERVICE_NAMES.get(c["service_id"]) if c["decision_status"] == "confirmed" and c["region_id"] == "KZ-ALA" else None
            return {"id": c["id"], "registered_at": c["received_at"] or c["ingested_at"], "status": status,
                    "service_name": service, "incident": dict(incident) if incident else None, "updates": updates,
                    "location": ({"latitude": c["latitude"], "longitude": c["longitude"]}
                                 if c["latitude"] is not None and c["longitude"] is not None else None),
                    "resolved_at": c["resolved_at"], "resolution_text": c["resolution_text"] if c["resolved_at"] else None,
                    "data_origin": "synthetic", "delivery": "demo_only"}

    @router.post("/intake", status_code=201)
    def intake(req: Intake):
        if not req.text.strip() or req.region_id not in valid_regions:
            raise HTTPException(422, "Укажите текст обращения и известный регион")
        cid = "PULSE-" + uuid.uuid4().hex[:6].upper()
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute("""INSERT INTO complaints
                (id, data_origin, source_system, text, region_id, received_at, ingested_at,
                 language, address, latitude, longitude, location_accuracy_m, district, channel, sender_key)
                VALUES (?, 'synthetic', 'operator_demo_intake', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, req.text.strip(), req.region_id, now, now, req.language,
                 req.address.strip() if req.address else address_in(req.text),
                 round(req.latitude, 6) if req.latitude is not None else None,
                 round(req.longitude, 6) if req.longitude is not None else None,
                 round(req.location_accuracy_m, 1) if req.location_accuracy_m is not None else None,
                 req.district, req.channel, req.sender_key))
            event(conn, cid, "intake", {"channel": req.channel, "text_len": len(req.text),
                                        "has_location": req.latitude is not None}, "citizen_demo")
        return {"id": cid, "data_origin": "synthetic", "decision_status": "pending"}

    @router.post("/complaints/{cid}/triage")
    def triage(cid: str):
        with get_connection() as conn:
            c = complaint(conn, cid)
            extra = "\n".join(get_received_clarifications(conn, cid))
            ai = decision_service.classify(c, extra) if decision_service else analyze(c, classifier, topic_services, extra)
            conn.execute("UPDATE complaints SET proposed_topic = ?, proposed_service_id = ?, proposed_priority = ? WHERE id = ?",
                         (ai["category"], ai["suggested_service"], ai["urgency"], cid))
            payload = (decision_service.audit_payload(ai) if decision_service else
                       {"topic": ai["category"], "confidence_kind": "synthetic_demo",
                        "category_confidence": ai["category_confidence"]})
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
            event(conn, cid, "classification_proposed", payload, ai.get("provider", "rules_demo"))
            return detail(conn, complaint(conn, cid))

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
                recommended = route({**c, "topic": req.topic, "priority": req.priority, "incident_id": req.incident_id or c["incident_id"]}, selected_ai, ops)["operator"]
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
            suggested = d["triage"]["category"]
            event(conn, cid, "operator_confirmed", {
                **req.model_dump(), "proposed_topic": suggested, "suggested_value": suggested,
                "confirmed_value": req.topic, "provider": d["triage"].get("provider", "mock"),
                "provider_version": d["triage"].get("provider_version"),
                "operator_override": bool(suggested and suggested != req.topic),
                "language": c["language"],
            })
            return {"complaint": complaint(conn, cid)}

    @router.get("/complaints/{cid}/routing")
    def recommend(cid: str, topic: str, priority: Literal["normal", "urgent"] | None = None):
        if topic not in topic_services:
            raise HTTPException(422, "Неизвестная категория")
        with get_connection() as conn:
            c = complaint(conn, cid)
            ai = {"category": topic, "suggested_service": topic_services[topic]}
            return {"routing": route({**c, "topic": topic, "priority": priority or c["priority"]}, ai, operators_with_load(conn)),
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
            decisions = []
            for row in conn.execute("""SELECT a.payload, c.language FROM audit_events a
                    JOIN complaints c ON c.id = a.complaint_id
                    WHERE a.event_type = 'operator_confirmed'"""):
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    continue
                suggested = payload.get("suggested_value") or payload.get("proposed_topic")
                confirmed = payload.get("confirmed_value") or payload.get("topic")
                if suggested and confirmed:
                    decisions.append((suggested, confirmed, row["language"]))
        confirmed_same = sum(suggested == confirmed for suggested, confirmed, _ in decisions)
        overrides = len(decisions) - confirmed_same
        language_agreement = {}
        for language in ("ru", "kk"):
            rows = [(a, b) for a, b, lang in decisions if lang == language]
            language_agreement[language] = (round(100 * sum(a == b for a, b in rows) / len(rows))
                                                if rows else None)
        corrected = Counter(suggested for suggested, confirmed, _ in decisions if suggested != confirmed)
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
                "laya_quality": {"label": "Synthetic / demo metrics", "decisions": len(decisions),
                                 "confirmed_without_changes_percent": round(100 * confirmed_same / len(decisions)) if decisions else None,
                                 "operator_override_percent": round(100 * overrides / len(decisions)) if decisions else None,
                                 "language_agreement_percent": language_agreement,
                                 "most_corrected": corrected.most_common(1)[0][0] if corrected else None},
                "note": "Счётчики синтетической SQLite. Экономия времени не измерялась."}

    attach_playbook_routes(router, get_connection, {
        "complaint": complaint, "detail": detail, "analysis": analysis, "routing": routing_for,
        "operators": operators_with_load, "checked_incident": checked_incident, "writable": writable,
        "services": topic_services, "clarification_reasons": VALID_CLARIFICATION_REASONS,
        "topic_names": topic_names or {},
        "suggested_response": suggested_response, "event": event,
    })
    return router

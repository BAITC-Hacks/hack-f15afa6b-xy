"""Real-time operator assistance and deterministic operations planning."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from auth import current_actor
from demo_data import demo_mode
from operations_core import (HEX_SIZE_METRES, cell_neighbors, cell_polygon,
                             operational_forecast, operations_cell, simulate_queue,
                             topic_for)
from public_issues import public_location
from triage import address_in, moment, operators_with_load, symptom


LIVE_DEBOUNCE_SECONDS = float(os.environ.get("P109_LIVE_DEBOUNCE_SECONDS", "3"))
if not 2 <= LIVE_DEBOUNCE_SECONDS <= 10:
    raise ValueError("P109_LIVE_DEBOUNCE_SECONDS must be between 2 and 10")


def init_operations(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(complaints)")}
    if "ops_cell" not in columns:
        conn.execute("ALTER TABLE complaints ADD COLUMN ops_cell TEXT")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS live_sessions (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            status TEXT NOT NULL, region_id TEXT NOT NULL, language TEXT NOT NULL,
            channel TEXT NOT NULL, latitude REAL, longitude REAL, transcript TEXT NOT NULL,
            state TEXT NOT NULL, last_processed_at TEXT, fingerprint TEXT,
            applied_complaint_id TEXT, stt_latency_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS live_session_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            event_type TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_live_events_session
            ON live_session_events(session_id, id);
        CREATE TABLE IF NOT EXISTS simulation_runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL,
            data_origin TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL
        );
    """)
    for row in conn.execute(
        "SELECT id, latitude, longitude FROM complaints "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND ops_cell IS NULL"
    ):
        conn.execute(
            "UPDATE complaints SET ops_cell = ? WHERE id = ?",
            (operations_cell(row["latitude"], row["longitude"]), row["id"]),
        )


class LiveStart(BaseModel):
    region_id: str = "KZ-ALA"
    language: Literal["ru", "kk", "mixed"] = "ru"
    channel: Literal["phone", "web"] = "phone"
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def complete_location(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите широту и долготу вместе")
        return self


class TranscriptUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    event_type: Literal["transcript_partial", "transcript_final"] = "transcript_partial"
    speech_pause: bool = False
    stt_latency_ms: int | None = Field(default=None, ge=0, le=120000)


class LiveApply(BaseModel):
    link_incident: bool = True


class SimulationRequest(BaseModel):
    queue: str = Field(min_length=1, max_length=64)
    horizon_minutes: Literal[15, 30, 60, 120] = 60
    incoming_percent: int = Field(default=0, ge=0, le=200)
    operator_delta: int = Field(default=0, ge=-10, le=10)
    handle_time_percent: int = Field(default=0, ge=-30, le=50)
    active_incident: bool = True
    priority_percent: int = Field(default=20, ge=0, le=100)
    seed: int = Field(default=109, ge=0, le=2_147_483_647)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(conn, session_id: str, event_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO live_session_events (session_id, event_type, created_at, payload) VALUES (?, ?, ?, ?)",
        (session_id, event_type, _now(), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def _session(conn, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM live_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Live-сессия не найдена")
    item = dict(row)
    item["state"] = json.loads(item["state"])
    return item


def _semantic_fingerprint(text: str, classifier) -> str:
    topic, _, urgency = classifier(text)
    lower = text.lower()
    scope = "building" if any(word in lower for word in (
        "весь дом", "во всем доме", "во всём доме", "бүкіл үй", "подъезд"
    )) else None
    value = [topic, urgency, address_in(text), symptom(text), scope]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def _incident_candidate(conn, region_id: str, category: str | None) -> dict | None:
    if not category:
        return None
    row = conn.execute("""SELECT i.*, COUNT(c.id) AS complaint_count
        FROM incidents i LEFT JOIN complaints c ON c.incident_id = i.id
        WHERE i.region_id = ? AND i.category = ? AND i.status != 'Завершён'
        GROUP BY i.id ORDER BY complaint_count DESC, i.started_at DESC LIMIT 1""",
        (region_id, category)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "title": row["title"], "status": row["status"],
        "similar_complaints": row["complaint_count"], "district": row["district"],
        "reason": "Та же категория, регион и активное временное окно; связь подтверждает оператор.",
    }


def _live_state(conn, session: dict, text: str, decision_service) -> dict:
    address = address_in(text)
    probe = {
        "id": session["id"], "text": text, "region_id": session["region_id"],
        "language": session["language"], "address": address,
    }
    triage_started = time.perf_counter()
    view = decision_service.classify(probe)
    triage_latency = round((time.perf_counter() - triage_started) * 1000)
    laya = (view.get("source_decisions") or {}).get("laya") or {}
    source = laya if laya.get("category") else view
    category = source.get("category")
    confidence = source.get("confidence", source.get("category_confidence"))
    similarity_started = time.perf_counter()
    candidate = _incident_candidate(conn, session["region_id"], category)
    similarity_latency = round((time.perf_counter() - similarity_started) * 1000)
    scope = "Весь дом" if any(word in text.lower() for word in (
        "весь дом", "во всем доме", "во всём доме", "бүкіл үй"
    )) else "Не уточнён"
    suggestion = (
        "Спасибо. Я вижу похожие обращения и проверяю связь с текущим инцидентом. "
        "Категорию и дальнейшие действия подтвердит оператор."
        if candidate else
        "Спасибо. Я зафиксировал описание. Оператор проверит категорию и дальнейшие действия."
    )
    return {
        "transcript": text,
        "detected": {"address": address, "category": category, "confidence": confidence, "scope": scope},
        "triage": {
            "provider": "laya_shadow" if laya.get("category") else view.get("provider"),
            "decision_mode": view.get("decision_mode"), "human_confirmation_required": True,
            "fallback_reason": view.get("fallback_reason"),
        },
        "incident_candidate": candidate,
        "suggested_response": suggestion,
        "timings_ms": {
            "stt_latency": session.get("stt_latency_ms"),
            "live_triage_latency": triage_latency,
            "similarity_latency": similarity_latency,
        },
    }


def _save_live_changes(conn, session_id: str, old: dict, new: dict, transcript_type: str) -> None:
    _event(conn, session_id, transcript_type, {"characters": len(new["transcript"])})
    if old.get("detected") != new["detected"] or old.get("triage") != new["triage"]:
        _event(conn, session_id, "live_triage_updated", {
            "category": new["detected"]["category"], "confidence": new["detected"]["confidence"],
            "address": new["detected"]["address"], "human_confirmation_required": True,
        })
    if old.get("incident_candidate") != new["incident_candidate"]:
        _event(conn, session_id, "live_incident_candidate", {
            "incident_id": (new["incident_candidate"] or {}).get("id"),
            "similar_complaints": (new["incident_candidate"] or {}).get("similar_complaints", 0),
        })
    if old.get("suggested_response") != new["suggested_response"]:
        _event(conn, session_id, "live_copilot_suggestion", {"available": True, "operator_action_required": True})


def _location(row: dict) -> tuple[float, float, str] | None:
    if row.get("latitude") is not None and row.get("longitude") is not None:
        return float(row["latitude"]), float(row["longitude"]), "reported_coordinates"
    if row.get("data_origin") == "synthetic":
        latitude, longitude, _, source = public_location(row)
        return latitude, longitude, "synthetic_" + source
    return None


def _map_data(conn, classifier, end: datetime, minutes: int, topic_filter: str | None) -> dict:
    start = end - timedelta(minutes=minutes)
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM complaints WHERE quarantined = 0 AND COALESCE(received_at, ingested_at) BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    )]
    cells: dict[str, dict] = {}
    points = []
    for row in rows:
        topic = topic_for(row, classifier)
        if topic_filter and topic != topic_filter:
            continue
        location = _location(row)
        if not location:
            continue
        latitude, longitude, source = location
        cell_id = operations_cell(latitude, longitude)
        if row.get("latitude") is not None and row.get("ops_cell") != cell_id:
            conn.execute("UPDATE complaints SET ops_cell = ? WHERE id = ?", (cell_id, row["id"]))
        age = max(0, (end - moment(row["received_at"] or row["ingested_at"])).total_seconds() / 60)
        sla_limit = 60 if row.get("priority") == "urgent" else 240
        risk = row.get("resolved_at") is None and age >= sla_limit * .8
        cell = cells.setdefault(cell_id, {"count": 0, "sla_risk": 0, "topics": Counter(), "complaint_ids": []})
        cell["count"] += 1
        cell["sla_risk"] += int(risk)
        cell["topics"][topic or "unknown"] += 1
        cell["complaint_ids"].append(row["id"])
        points.append({
            "id": row["id"], "latitude": latitude, "longitude": longitude, "cell_id": cell_id,
            "topic": topic, "incident_id": row.get("incident_id"), "sla_risk": risk,
            "received_at": row["received_at"] or row["ingested_at"], "location_source": source,
            "data_origin": row["data_origin"], "text": row["text"][:160],
        })
    online = [operator for operator in operators_with_load(conn) if operator["status"] == "online"]
    cell_features = []
    for cell_id, cell in cells.items():
        main_topic, main_count = cell["topics"].most_common(1)[0]
        skilled = [operator for operator in online if main_topic in operator["skills"]]
        load = round(sum(operator["workload"] for operator in skilled) * 100 /
                     max(1, sum(operator["workload_capacity"] for operator in skilled)))
        cell_features.append({
            "type": "Feature", "id": cell_id,
            "geometry": {"type": "Polygon", "coordinates": [cell_polygon(cell_id)]},
            "properties": {"cell_id": cell_id, "count": cell["count"], "sla_risk": cell["sla_risk"],
                           "category": main_topic, "category_count": main_count, "operator_load": load,
                           "complaint_ids": cell["complaint_ids"]},
        })
    clusters = []
    for feature in cell_features:
        props = feature["properties"]
        nearby = [candidate for candidate in cell_features
                  if candidate["properties"]["cell_id"] in cell_neighbors(props["cell_id"])
                  and candidate["properties"]["category"] == props["category"]]
        count = props["count"] + sum(candidate["properties"]["count"] for candidate in nearby)
        if count >= 5:
            clusters.append({"cell_id": props["cell_id"], "neighbor_cells": len(nearby),
                             "category": props["category"], "count": count,
                             "method": "category + local hex/neighbours + time + symptom"})
    timeline = []
    for offset in (minutes, min(minutes, 120), min(minutes, 60), min(minutes, 30), 0):
        at = end - timedelta(minutes=offset)
        timeline.append({"at": at.isoformat(), "complaints": sum(moment(item["received_at"]) <= at for item in points)})
    return {
        "data_origin": "synthetic_demo" if points and all(item["data_origin"] == "synthetic"
                                                             for item in points) else "mixed",
        "cell_method": "local_hex_v1", "cell_size_metres": HEX_SIZE_METRES,
        "window": {"from": start.isoformat(), "to": end.isoformat(), "minutes": minutes},
        "cells": {"type": "FeatureCollection", "features": cell_features},
        "points": points, "clusters": clusters, "timeline": timeline,
    }


def build_operations_router(get_connection, classifier, decision_service, topic_services,
                            valid_regions) -> APIRouter:
    router = APIRouter(prefix="/api/operations")

    @router.post("/live/sessions", status_code=201)
    def start_live(req: LiveStart):
        if req.region_id not in valid_regions:
            raise HTTPException(422, "Неизвестный регион")
        session_id, now = "LIVE-" + uuid.uuid4().hex[:8].upper(), _now()
        with get_connection() as conn:
            conn.execute("""INSERT INTO live_sessions
                (id, started_at, updated_at, status, region_id, language, channel, latitude, longitude,
                 transcript, state) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, '', '{}')""",
                (session_id, now, now, req.region_id, req.language, req.channel, req.latitude, req.longitude))
        return {"id": session_id, "status": "active", "started_at": now, "state": {}}

    @router.get("/live/sessions/{session_id}")
    def get_live(session_id: str, after_event_id: int = Query(default=0, ge=0)):
        with get_connection() as conn:
            session = _session(conn, session_id)
            events = [dict(row) for row in conn.execute(
                "SELECT id, event_type, created_at, payload FROM live_session_events "
                "WHERE session_id = ? AND id > ? ORDER BY id", (session_id, after_event_id))]
        for item in events:
            item["payload"] = json.loads(item["payload"])
        session["events"] = events
        return session

    @router.post("/live/sessions/{session_id}/transcript")
    def update_live(session_id: str, req: TranscriptUpdate):
        text = " ".join(req.text.split())
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = _session(conn, session_id)
            if session["status"] != "active":
                raise HTTPException(409, "Live-сессия уже завершена")
            fingerprint = _semantic_fingerprint(text, classifier)
            elapsed = ((datetime.now(timezone.utc) - moment(session["last_processed_at"])).total_seconds()
                       if session["last_processed_at"] else LIVE_DEBOUNCE_SECONDS)
            process = (req.event_type == "transcript_final" or req.speech_pause or
                       fingerprint != session.get("fingerprint") or elapsed >= LIVE_DEBOUNCE_SECONDS)
            now = _now()
            conn.execute("UPDATE live_sessions SET transcript = ?, updated_at = ?, stt_latency_ms = ? WHERE id = ?",
                         (text, now, req.stt_latency_ms, session_id))
            if not process:
                return {"id": session_id, "status": "active", "processed": False,
                        "next_checkpoint_ms": max(0, round((LIVE_DEBOUNCE_SECONDS - elapsed) * 1000)),
                        "state": session["state"]}
            session["stt_latency_ms"] = req.stt_latency_ms
            new_state = _live_state(conn, session, text, decision_service)
            _save_live_changes(conn, session_id, session["state"], new_state, req.event_type)
            conn.execute("UPDATE live_sessions SET state = ?, last_processed_at = ?, fingerprint = ? WHERE id = ?",
                         (json.dumps(new_state, ensure_ascii=False), now, fingerprint, session_id))
        return {"id": session_id, "status": "active", "processed": True, "state": new_state}

    @router.post("/live/sessions/{session_id}/apply")
    def apply_live(session_id: str, req: LiveApply):
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = _session(conn, session_id)
            if session.get("applied_complaint_id"):
                return {"id": session["applied_complaint_id"], "replayed": True}
            if session["status"] != "active" or not session["transcript"].strip():
                raise HTTPException(409, "Нет завершённого live-транскрипта")
            state = session["state"]
            category = (state.get("detected") or {}).get("category")
            candidate = state.get("incident_candidate") if req.link_incident else None
            complaint_id, now = "PULSE-" + uuid.uuid4().hex[:6].upper(), _now()
            incident_id = candidate.get("id") if candidate else None
            related = None
            district = candidate.get("district") if candidate else None
            if incident_id:
                root = conn.execute("SELECT id FROM complaints WHERE incident_id = ? ORDER BY id LIMIT 1",
                                    (incident_id,)).fetchone()
                related = root["id"] if root else None
            origin = "synthetic" if demo_mode() else "citizen"
            cell_id = (operations_cell(session["latitude"], session["longitude"])
                       if session["latitude"] is not None else None)
            conn.execute("""INSERT INTO complaints
                (id, data_origin, source_system, text, region_id, received_at, ingested_at, language,
                 proposed_topic, proposed_service_id, proposed_priority, decision_status, incident_id,
                 related_to, address, district, channel, latitude, longitude, ops_cell,
                 public_consent, moderation_status)
                VALUES (?, ?, 'live_operator_v1', ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, 0, 'private')""",
                (complaint_id, origin, session["transcript"], session["region_id"], now, now,
                 session["language"], category, topic_services.get(category), None, incident_id, related,
                 (state.get("detected") or {}).get("address"), district, session["channel"],
                 session["latitude"], session["longitude"], cell_id))
            actor = current_actor()
            audit = [
                ("intake", {"channel": session["channel"], "text_len": len(session["transcript"]),
                             "live_session_id": session_id}),
                ("classification_proposed", {"topic": category,
                    "confidence": (state.get("detected") or {}).get("confidence"),
                    "provider": (state.get("triage") or {}).get("provider"),
                    "human_confirmation_required": True}),
                ("live_call_applied", {"live_session_id": session_id, "incident_id": incident_id}),
            ]
            if incident_id:
                audit.append(("incident_linked", {"incident_id": incident_id, "related_to": related,
                                                   "confirmed_by_operator": True}))
            for event_type, payload in audit:
                conn.execute("""INSERT INTO audit_events
                    (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("evt-" + uuid.uuid4().hex[:8], complaint_id, event_type, now, now, actor,
                     json.dumps(payload, ensure_ascii=False)))
            conn.execute("UPDATE live_sessions SET status = 'applied', updated_at = ?, applied_complaint_id = ? WHERE id = ?",
                         (now, complaint_id, session_id))
            _event(conn, session_id, "operator_applied", {"complaint_id": complaint_id,
                                                           "incident_id": incident_id})
        return {"id": complaint_id, "decision_status": "pending", "incident_id": incident_id,
                "category_confirmed": False, "replayed": False}

    @router.post("/live/sessions/{session_id}/ignore")
    def ignore_live(session_id: str):
        with get_connection() as conn:
            session = _session(conn, session_id)
            if session["status"] != "active":
                raise HTTPException(409, "Live-сессия уже завершена")
            conn.execute("UPDATE live_sessions SET status = 'ignored', updated_at = ? WHERE id = ?",
                         (_now(), session_id))
            _event(conn, session_id, "operator_ignored", {"reason": "operator_choice"})
        return {"id": session_id, "status": "ignored"}

    @router.get("/map")
    def operations_map(mode: Literal["complaints", "incidents", "heat", "sla_risk", "operator_load", "category"] = "heat",
                       minutes: int = Query(default=60, ge=15, le=1440),
                       at: datetime | None = None, topic: str | None = None):
        if topic is not None and topic not in topic_services:
            raise HTTPException(422, "Неизвестная категория")
        end = at or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        with get_connection() as conn:
            data = _map_data(conn, classifier, end.astimezone(timezone.utc), minutes, topic)
        data["mode"] = mode
        return data

    @router.get("/forecast")
    def forecast(horizon_minutes: int = Query(default=60, ge=15, le=120)):
        if horizon_minutes not in {15, 30, 60, 120}:
            raise HTTPException(422, "Horizon must be 15, 30, 60 or 120 minutes")
        with get_connection() as conn:
            return operational_forecast(conn, classifier, horizon_minutes)

    @router.post("/simulate")
    def simulate(req: SimulationRequest):
        started = time.perf_counter()
        with get_connection() as conn:
            forecast_data = operational_forecast(conn, classifier, req.horizon_minutes)
            queue = next((item for item in forecast_data["queues"] if item["queue"] == req.queue), None)
            if not queue:
                raise HTTPException(404, "Очередь не найдена")
            incoming_factor = (1 + req.incoming_percent / 100) * (1 if req.active_incident else .85)
            arrival_rate = queue["arrival_rate_per_minute"] * incoming_factor
            operators = max(0, queue["current_operators"] + req.operator_delta)
            handle = 8 * (1 + req.handle_time_percent / 100)
            current = simulate_queue(queue["current_backlog"], arrival_rate, operators, handle,
                                     req.horizon_minutes, req.priority_percent, req.seed)
            tested = []
            for added in range(5):
                result = simulate_queue(queue["current_backlog"], arrival_rate, operators + added, handle,
                                        req.horizon_minutes, req.priority_percent, req.seed)
                tested.append({"added_operators": added, **result})
            best = min(tested, key=lambda item: (item["sla_risk"], item["wait_seconds"] is None,
                                                 item["wait_seconds"] or 10**9, item["added_operators"]))
            run_id, now = "SIM-" + uuid.uuid4().hex[:8].upper(), _now()
            result = {"id": run_id, "label": "Synthetic simulation", "queue": req.queue,
                      "horizon_minutes": req.horizon_minutes, "current": current,
                      "best_tested_scenario": best, "tested_scenarios": tested,
                      "simulation_latency_ms": round((time.perf_counter() - started) * 1000),
                      "disclaimer": "Сценарий воспроизводим по seed и не является гарантией результата."}
            conn.execute("INSERT INTO simulation_runs VALUES (?, ?, ?, ?, ?, ?)",
                         (run_id, now, current_actor(), "synthetic_demo",
                          req.model_dump_json(), json.dumps(result, ensure_ascii=False)))
        return result

    @router.get("/simulations")
    def simulations(limit: int = Query(default=5, ge=1, le=20)):
        with get_connection() as conn:
            rows = conn.execute("SELECT id, created_at, actor, data_origin, result FROM simulation_runs "
                                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return {"items": [{**dict(row), "result": json.loads(row["result"])} for row in rows]}

    @router.get("/command-center")
    def command_center():
        with get_connection() as conn:
            forecast_data = operational_forecast(conn, classifier, 60)
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM complaints WHERE quarantined = 0 AND resolved_at IS NULL")]
            incidents = [dict(row) for row in conn.execute(
                "SELECT id, title, status, severity, next_update FROM incidents "
                "WHERE status != 'Завершён' ORDER BY severity DESC, started_at DESC")]
            online = [operator for operator in operators_with_load(conn) if operator["status"] == "online"]
        risk = sum(item["sla_risk_count"] for item in forecast_data["queues"])
        return {
            "label": forecast_data["label"],
            "cards": {"queue": len(rows), "critical": sum(row.get("priority") == "urgent" for row in rows),
                      "sla_risk": risk, "operators_online": len(online),
                      "active_incidents": len(incidents), "system_health": "Operational"},
            "hot_queues": forecast_data["queues"][:4], "active_incidents": incidents[:5],
            "staffing_recommendations": [item for item in forecast_data["queues"] if item["recommended_operators"]][:4],
            "forecast": forecast_data,
            "health": {"live_copilot": "operational", "operations_map": "operational",
                       "forecast": "operational", "simulation": "operational"},
        }

    return router


def operations_health() -> dict:
    return {"live_copilot": "operational", "operations_map": "operational",
            "forecast": "operational", "simulation": "operational"}

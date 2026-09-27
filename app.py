"""Pulse 109 — Synthetic Skeleton API and Operator Assistant."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from auth import current_actor, init_auth, install_auth
from data_coverage import CoverageUnavailable, load_coverage
from clarification import build_clarification_router, get_received_clarifications
from queue_api import build_queue_router
from demo_data import init_workspace
from playbooks import faq_closed
from workspace_api import build_workspace_router
from incidents import init_incidents
from radar import build_incident_router
from support_api import init_support, build_support_router
from decision import DecisionService
from voice_api import build_voice_router

BANNER_TEXT = "SYNTHETIC DEMO — MODELS NOT TRAINED"

REGIONS = [
    {"id": f"KZ-{k}", "name_ru": ru, "name_kk": kk}
    for k, ru, kk in [
        ("ABA", "область Абай", "Абай облысы"),
        ("AKM", "Акмолинская область", "Ақмола облысы"),
        ("AKT", "Актюбинская область", "Ақтөбе облысы"),
        ("ALM", "Алматинская область", "Алматы облысы"),
        ("ATY", "Атырауская область", "Атырау облысы"),
        ("VKO", "Восточно-Казахстанская область", "Шығыс Қазақстан облысы"),
        ("ZHA", "Жамбылская область", "Жамбыл облысы"),
        ("ZHE", "область Жетісу", "Жетісу облысы"),
        ("ZKO", "Западно-Казахстанская область", "Батыс Қазақстан облысы"),
        ("KAR", "Карагандинская область", "Қарағанды облысы"),
        ("KOS", "Костанайская область", "Қостанай облысы"),
        ("KZY", "Кызылординская область", "Қызылорда облысы"),
        ("MAN", "Мангистауская область", "Маңғыстау облысы"),
        ("PAV", "Павлодарская область", "Павлодар облысы"),
        ("SEV", "Северо-Казахстанская область", "Солтүстік Қазақстан облысы"),
        ("TUR", "Туркестанская область", "Түркістан облысы"),
        ("ULY", "область Ұлытау", "Ұлытау облысы"),
        ("AST", "город Астана", "Астана қаласы"),
        ("ALA", "город Алматы", "Алматы қаласы"),
        ("SHY", "город Шымкент", "Шымкент қаласы"),
    ]
]
VALID_REGION_IDS = {r["id"] for r in REGIONS}

TOPICS = [
    {"id": tid, "name_ru": ru, "name_kk": kk, "default_service": srv}
    for tid, ru, kk, srv in [
        ("heating", "Отопление", "Жылумен жабдықтау", "srv_teplo"),
        ("water_supply", "Водоснабжение", "Сумен жабдықтау", "srv_vodokanal"),
        ("electricity", "Электроснабжение", "Электрмен жабдықтау", "srv_energo"),
        ("roads", "Дороги", "Жол инфрақұрылымы", "srv_roads"),
        ("street_lighting", "Уличное освещение", "Көше жарығы", "srv_lighting"),
        ("waste_management", "Вывоз мусора и ТБО", "Қоқыс шығару", "srv_clean"),
        ("public_transport", "Общественный транспорт", "Қоғамдық көлік", "srv_transit"),
        ("housing_maintenance", "Обслуживание жилья и ЖКХ", "ТКШ және үйді күтіп ұстау", "srv_housing"),
        ("landscaping", "Благоустройство и парки", "Абаттандыру", "srv_parks"),
        ("sewerage", "Ливневая канализация", "Нөсер кәрізі", "srv_sewerage"),
    ]
]
VALID_TOPIC_IDS = {t["id"] for t in TOPICS}
TOPIC_SERVICE_MAP = {t["id"]: t["default_service"] for t in TOPICS}
# Human-confirmed decision values only; urgency detection is a proposal, never mixed with review.
VALID_PRIORITIES = {"normal", "urgent"}
def get_db_path() -> Path:
    env_path = os.environ.get("DATABASE_PATH")
    if env_path:
        return Path(env_path)
    default_path = Path(__file__).resolve().parent / "data" / "pulse109.db"
    default_path.parent.mkdir(parents=True, exist_ok=True)
    return default_path
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn
def init_db() -> None:
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS complaints (
                id TEXT PRIMARY KEY, data_origin TEXT NOT NULL, source_system TEXT,
                source_record_id TEXT, text TEXT NOT NULL, region_id TEXT NOT NULL,
                received_at TEXT, ingested_at TEXT NOT NULL, language TEXT NOT NULL,
                source_category TEXT, source_service TEXT, source_status TEXT,
                topic TEXT, service_id TEXT, priority TEXT,
                decision_status TEXT NOT NULL DEFAULT 'pending',
                incident_id TEXT, duplicate_of TEXT, resolution_text TEXT, resolved_at TEXT,
                proposed_topic TEXT, proposed_service_id TEXT, proposed_priority TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY, complaint_id TEXT NOT NULL, event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        fixture_file = Path(__file__).resolve().parent / "fixtures" / "demo.json"
        if fixture_file.exists():
            data = json.loads(fixture_file.read_text(encoding="utf-8"))
            for c in data.get("complaints", []):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO complaints (
                        id, data_origin, source_system, source_record_id, text, region_id,
                        received_at, ingested_at, language, source_category, source_service,
                        source_status, topic, service_id, priority, decision_status,
                        incident_id, duplicate_of, resolution_text, resolved_at
                    ) VALUES (
                        :id, :data_origin, :source_system, :source_record_id, :text, :region_id,
                        :received_at, :ingested_at, :language, :source_category, :source_service,
                        :source_status, :topic, :service_id, :priority, :decision_status,
                        :incident_id, :duplicate_of, :resolution_text, :resolved_at
                    )
                    """,
                    c,
                )

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with get_connection() as conn:
        init_auth(conn)
        init_workspace(conn)
        init_incidents(conn)
        init_support(conn)
    yield

app = FastAPI(title="Pulse 109 Synthetic Skeleton", lifespan=lifespan)
install_auth(app, get_connection)
cors_origins = [origin.strip() for origin in os.environ.get("P109_CORS_ORIGINS", "").split(",") if origin.strip()]
if "*" in cors_origins:
    raise RuntimeError("P109_CORS_ORIGINS must list explicit origins when authentication is enabled")
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
app.include_router(build_clarification_router(get_connection, BANNER_TEXT))
app.include_router(build_queue_router(get_connection, BANNER_TEXT, VALID_REGION_IDS))
app.include_router(build_voice_router())

class IntakeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    region_id: str
    language: Optional[str] = None

class ConfirmRequest(BaseModel):
    topic: str
    service_id: str
    priority: str

# ponytail: Mock keyword classifier used before multilingual E5 fine-tuning.
def mock_classify(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (topic, service, urgency proposal). No urgency evidence means None, never 'normal'."""
    lowered = text.lower()
    urgent_terms = ["срочно", "авария", "жарылыс", "щит", "замерзаем", "қауіп", "тоңып"]
    urgency = "urgent" if any(t in lowered for t in urgent_terms) else None
    patterns = [
        ("heating", ["отоплен", "батаре", "тепло", "жылу", "тоңып"]),
        ("water_supply", ["холодную воду", "горячую воду", "водопровод", "суық су", "ыстық су", "су тоқта"]),
        ("electricity", ["электр", "свет", "подстанци", "ток", "лифт"]),
        ("roads", ["дорог", "яма", "жол", "шұңқыр", "асфальт"]),
        ("street_lighting", ["освещен", "фонар", "жарық", "шам"]),
        ("waste_management", ["мусор", "тбо", "қоқыс", "жәшік", "контейнер"]),
        ("public_transport", ["автобус", "маршрут", "көлік", "аялдама"]),
        ("housing_maintenance", ["кск", "пик", "крыш", "төбе", "подъезд", "кіреберіс", "жкх", "ткш"]),
        ("landscaping", ["сквер", "парк", "саябақ", "скамейк", "орындық", "бұтақ"]),
        ("sewerage", ["канализац", "кәріз", "нөсер", "ливнев"]),
    ]
    for topic_id, keywords in patterns:
        if any((re.search(r"\bток\b", lowered) if kw == "ток" else kw in lowered) for kw in keywords):
            return topic_id, TOPIC_SERVICE_MAP[topic_id], urgency
    return None, None, urgency


decision_service = DecisionService(mock_classify, TOPIC_SERVICE_MAP, TOPICS)
@app.get("/api/health")
def health_check():
    model = decision_service.model_metadata()
    return {
        "status": "ok",
        "app": "Pulse 109",
        "mode": decision_service.mode,
        **model,
        "laya": decision_service.health(),
        "banner": BANNER_TEXT,
    }

@app.get("/api/regions")
def get_regions():
    return {"regions": REGIONS}
@app.get("/api/topics")
def get_topics():
    return {"topics": TOPICS}


@app.get("/api/data-coverage")
def get_data_coverage(region_id: Optional[str] = None):
    if region_id is not None and region_id not in VALID_REGION_IDS:
        raise HTTPException(status_code=422, detail="Unknown region_id")
    try:
        data = load_coverage(Path(__file__).resolve().parent / "planning", REGIONS)
    except CoverageUnavailable:
        raise HTTPException(status_code=503, detail={"error": "coverage_unavailable"}) from None
    if region_id is not None:
        data["regions"] = [r for r in data["regions"] if r["region_id"] == region_id]
    return data
@app.get("/api/stats")
def get_stats(region_id: Optional[str] = None):
    if region_id and region_id not in VALID_REGION_IDS:
        raise HTTPException(status_code=422, detail=f"Unknown region_id: {region_id}")
    with get_connection() as conn:
        base, params = "FROM complaints", []
        if region_id:
            base += " WHERE region_id = ?"
            params.append(region_id)
        where_pend = f"{base} {'AND' if region_id else 'WHERE'} decision_status = 'pending'"
        where_conf = f"{base} {'AND' if region_id else 'WHERE'} decision_status = 'confirmed'"
        where_clar = f"{base} {'AND' if region_id else 'WHERE'} decision_status = 'needs_clarification'"
        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        pending = conn.execute(f"SELECT COUNT(*) {where_pend}", params).fetchone()[0]
        confirmed = conn.execute(f"SELECT COUNT(*) {where_conf}", params).fetchone()[0]
        clarification = conn.execute(f"SELECT COUNT(*) {where_clar}", params).fetchone()[0]
        by_topic = {
            r[0]: r[1]
            for r in conn.execute(
                f"SELECT COALESCE(topic, 'unclassified'), COUNT(*) {base} GROUP BY topic", params
            ).fetchall()
        }
        by_prio = {
            r[0]: r[1]
            for r in conn.execute(
                f"SELECT COALESCE(priority, 'unassigned'), COUNT(*) {base} GROUP BY priority", params
            ).fetchall()
        }
        by_region = {
            r[0]: r[1]
            for r in conn.execute("SELECT region_id, COUNT(*) FROM complaints GROUP BY region_id").fetchall()
        }
    return {
        "banner": BANNER_TEXT,
        "total_complaints": total,
        "pending_count": pending,
        "confirmed_count": confirmed,
        "clarification_count": clarification,
        "by_topic": by_topic,
        "by_priority": by_prio,
        "by_region": by_region,
    }


@app.get("/api/complaints/{complaint_id}")
def get_complaint(complaint_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Complaint not found")
        events = conn.execute(
            "SELECT * FROM audit_events WHERE complaint_id = ? ORDER BY occurred_at ASC", (complaint_id,)
        ).fetchall()
    return {"complaint": dict(row), "events": [dict(e) for e in events]}


@app.post("/api/intake", status_code=status.HTTP_201_CREATED)
def intake_complaint(req: IntakeRequest):
    cleaned = req.text.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="Complaint text cannot be blank")
    if req.region_id not in VALID_REGION_IDS:
        raise HTTPException(status_code=422, detail=f"Unknown region_id: {req.region_id}")
    cid = f"cmp-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    lang = req.language if req.language in {"ru", "kk", "mixed"} else "unknown"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO complaints (id, data_origin, text, region_id, ingested_at, language, decision_status) "
            "VALUES (?, 'synthetic', ?, ?, ?, ?, 'pending')",
            (cid, cleaned, req.region_id, now_iso, lang),
        )
        conn.execute(
            "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
            "VALUES (?, ?, 'intake', ?, ?, 'citizen_intake', ?)",
            (
                f"evt-{uuid.uuid4().hex[:8]}",
                cid,
                now_iso,
                now_iso,
                json.dumps({"region_id": req.region_id, "text_len": len(cleaned)}, ensure_ascii=False),
            ),
        )
        conn.commit()
    return {"id": cid, "decision_status": "pending", "data_origin": "synthetic", "banner": BANNER_TEXT}


@app.post("/api/complaints/{complaint_id}/classify")
def classify_complaint(complaint_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Complaint not found")
        clarifications = get_received_clarifications(conn, complaint_id)
        extra_text = "\n".join(clarifications)
        ai = decision_service.classify(dict(row), extra_text)
        topic, service_id, prio = ai["category"], ai["suggested_service"], ai["urgency"]
        if ai["provider"] not in {"laya", "hybrid"}:
            topic, service_id, prio = mock_classify(row["text"] + (("\n" + extra_text) if extra_text else ""))
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE complaints SET proposed_topic = ?, proposed_service_id = ?, proposed_priority = ? WHERE id = ?",
            (topic, service_id, prio, complaint_id),
        )
        conn.execute(
            "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
            "VALUES (?, ?, 'classification_proposed', ?, ?, ?, ?)",
            (
                f"evt-{uuid.uuid4().hex[:8]}",
                complaint_id,
                now_iso,
                now_iso,
                ai["provider"],
                json.dumps({**decision_service.audit_payload(ai), "topic": topic,
                            "service_id": service_id, "priority": prio,
                            "clarification_count": len(clarifications), "created_at": now_iso},
                           ensure_ascii=False),
            ),
        )
        conn.commit()
    return {
        "mode": ai["provider"],
        "checkpoint_id": ai["checkpoint_id"],
        "training_status": ai["training_status"],
        "confidence": ai["category_confidence"] if ai["provider"] in {"laya", "hybrid"} else None,
        "decision": ai["decision_mode"],
        "triage": ai,
        "proposal": {"topic": topic, "service_id": service_id, "priority": prio},
    }


# ponytail: Mock retrieval uses SQL filtering before E5 semantic embedding search.
@app.get("/api/complaints/{complaint_id}/similar")
def find_similar(complaint_id: str, limit: int = Query(5, ge=1, le=20)):
    with get_connection() as conn:
        target = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Complaint not found")
        topic = target["topic"]
        if not topic:
            clarifications = get_received_clarifications(conn, complaint_id)
            if clarifications:
                topic, _, _ = mock_classify(target["text"] + "\n\n" + "\n".join(clarifications))
            topic = topic or target["proposed_topic"]
        if topic:
            rows = conn.execute(
                "SELECT id, data_origin, text, topic, decision_status, resolution_text "
                "FROM complaints WHERE id != ? AND (topic = ? OR proposed_topic = ?) "
                "ORDER BY ingested_at DESC LIMIT ?",
                (complaint_id, topic, topic, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, data_origin, text, topic, decision_status, resolution_text "
                "FROM complaints WHERE id != ? ORDER BY ingested_at DESC LIMIT ?",
                (complaint_id, limit),
            ).fetchall()

    candidates = [
        {
            "complaint_id": r["id"],
            "excerpt": r["text"][:140] + ("..." if len(r["text"]) > 140 else ""),
            "origin": r["data_origin"],
            "topic": r["topic"],
            "decision_status": r["decision_status"],
            "resolution_text": r["resolution_text"],
            "similarity": None,
        }
        for r in rows
    ]
    return {
        "mode": "mock",
        "checkpoint_id": None,
        "training_status": "not_trained",
        "candidates": candidates,
    }


@app.post("/api/complaints/{complaint_id}/confirm")
def confirm_complaint(complaint_id: str, req: ConfirmRequest):
    if req.topic not in VALID_TOPIC_IDS:
        raise HTTPException(status_code=422, detail=f"Invalid topic: {req.topic}")
    if req.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=422, detail=f"Invalid priority: {req.priority}")
    if not req.service_id.strip():
        raise HTTPException(status_code=422, detail="service_id cannot be blank")
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Complaint not found")
        if row["decision_status"] == "needs_clarification" or row["quarantined"]:
            raise HTTPException(status_code=409, detail="Confirm is blocked while clarification is pending")
        if row["resolved_at"] and faq_closed(conn, complaint_id):
            raise HTTPException(status_code=409, detail="Типовой вопрос закрыт сценарием FAQ; сначала верните его в работу")
        if row["incident_id"]:
            incident = conn.execute("SELECT category FROM incidents WHERE id = ?", (row["incident_id"],)).fetchone()
            if incident and incident[0] != req.topic:
                raise HTTPException(status_code=409, detail="Unlink the incident before changing its category")
        conn.execute(
            "UPDATE complaints SET topic = ?, service_id = ?, priority = ?, decision_status = 'confirmed' WHERE id = ?",
            (req.topic, req.service_id, req.priority, complaint_id),
        )
        conn.execute(
            "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
            "VALUES (?, ?, 'operator_confirmed', ?, ?, ?, ?)",
            (
                f"evt-{uuid.uuid4().hex[:8]}",
                complaint_id,
                now_iso,
                now_iso,
                current_actor(),
                json.dumps({
                    "topic": req.topic, "service_id": req.service_id, "priority": req.priority,
                    "suggested_value": row["proposed_topic"], "confirmed_value": req.topic,
                    "operator_override": bool(row["proposed_topic"] and row["proposed_topic"] != req.topic),
                    "language": row["language"],
                }, ensure_ascii=False),
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    return {"complaint": dict(updated), "banner": BANNER_TEXT}


@app.get("/api/alerts")
def get_alerts():
    raise HTTPException(
        status_code=501,
        detail={"error": "not_implemented", "message": "Alerts module will be implemented in future milestone (REQ-10)"},
    )


@app.get("/api/forecast")
def get_forecast(horizon_months: int = Query(1)):
    if horizon_months not in {1, 2, 3}:
        raise HTTPException(status_code=422, detail="horizon_months must be 1, 2, or 3")
    raise HTTPException(
        status_code=501,
        detail={
            "error": "not_implemented",
            "message": "Forecast module will be implemented in future milestone (REQ-11)",
            "horizon_months": horizon_months,
        },
    )


@app.post("/api/query")
def natural_query():
    raise HTTPException(
        status_code=501,
        detail={"error": "not_implemented", "message": "Natural-language query module will be implemented in future milestone (REQ-12)"},
    )


@app.get("/api/reports")
def get_reports(format: str = Query("pdf")):
    if format not in {"pdf", "xlsx"}:
        raise HTTPException(status_code=422, detail="format must be 'pdf' or 'xlsx'")
    raise HTTPException(
        status_code=501,
        detail={"error": "not_implemented", "message": "Reports module will be implemented in future milestone (REQ-13)", "format": format},
    )


static_dir = Path(__file__).resolve().parent / "static"
app.include_router(build_workspace_router(get_connection, mock_classify, TOPIC_SERVICE_MAP, VALID_REGION_IDS,
                                         {t["id"]: t["name_ru"] for t in TOPICS}, decision_service))
app.include_router(build_incident_router(get_connection, mock_classify, TOPIC_SERVICE_MAP))
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    idx = static_dir / "workspace.html"
    return FileResponse(idx) if idx.exists() else legacy()


@app.get("/map")
def public_map():
    return FileResponse(static_dir / "public-map.html")


@app.get("/legacy")
def legacy():
    idx = static_dir / "index.html"
    return FileResponse(idx) if idx.exists() else {"message": "Pulse 109 API running"}

app.include_router(build_support_router(get_connection, TOPIC_SERVICE_MAP, VALID_REGION_IDS))

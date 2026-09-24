"""Bounded Pulse109 support surface: routing-coverage health and lightweight operator presence.

Demo-only. Synthetic operators, no auth, no real service coverage claims. Presence is
advisory (20 s TTL) and never writes complaints or audit events, so polling cannot
invalidate a stored playbook preview.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from triage import DEMO_WORKLOAD_WEIGHTS, operators_with_load, route

PRESENCE_TTL_SECONDS = 20
MAX_HEALTH_ITEMS = 40
DEMO_LANGUAGES = ("ru", "kk", "mixed", "unknown")
DEMO_PRIORITIES = ("normal", "urgent")
PROPOSED_FIELDS = {"proposed_topic", "proposed_service_id", "proposed_priority"}
HEALTH_NOTE = ("Синтетическая демо-маршрутизация: 10 категорий × 20 регионов на демо-смене операторов. "
               "Профильные совпадения по службе есть только для KZ-ALA; остальные регионы уходят в общую "
               "очередь старшего оператора — это не заявление о реальном национальном покрытии служб. "
               "Полностью занятая смена считается fallback (обращение остаётся в общем пуле), "
               "uncovered — только отсутствие профиля и общей очереди вовсе.")

# The 7th synthetic operator exists to show a rejected alternative: full slots and an
# over-capacity workload while the demo water profile is otherwise available.
OP_OVERLOADED = ("op-overloaded", "Данияр О.", ["water_supply", "sewerage"], ["ru", "kk"],
                 "srv_vodokanal", "Алмалинский", "online", 7, 6)


def init_support(conn):
    """Idempotent: presence table plus the 7th demo operator. Run after demo_data.init_workspace."""
    conn.execute("""CREATE TABLE IF NOT EXISTS case_presence (
        complaint_id TEXT NOT NULL, session_id TEXT NOT NULL, operator_id TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('viewing', 'editing')), seen_at TEXT NOT NULL,
        PRIMARY KEY (complaint_id, session_id))""")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operators'").fetchone():
        conn.execute("INSERT OR IGNORE INTO operators VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (OP_OVERLOADED[0], OP_OVERLOADED[1], json.dumps(OP_OVERLOADED[2]),
                      json.dumps(OP_OVERLOADED[3]), *OP_OVERLOADED[4:]))


class PresenceHeartbeat(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    operator_id: str = Field(min_length=1, max_length=100)
    mode: Literal["viewing", "editing"]


class PresenceLeave(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)


def case_revision(conn, cid):
    """Deterministic hash of the saved complaint state plus the last meaningful audit event.

    Proposed/classification fields are excluded: an unconfirmed proposal is not a change the
    operator has to reconcile, so polling and re-triage never invalidate a preview.
    """
    row = conn.execute("SELECT * FROM complaints WHERE id = ?", (cid,)).fetchone()
    if row is None:
        return None, None
    saved = {key: row[key] for key in row.keys() if key not in PROPOSED_FIELDS}
    last = conn.execute("""SELECT event_type, occurred_at, actor, payload FROM audit_events
        WHERE complaint_id = ? AND event_type != 'classification_proposed'
        ORDER BY occurred_at DESC, recorded_at DESC, rowid DESC LIMIT 1""", (cid,)).fetchone()
    last_event = dict(last) if last else None
    digest = hashlib.sha256(json.dumps({"complaint": saved, "last_event": last_event},
                                       sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return digest[:32], (last_event["occurred_at"] if last_event else (row["received_at"] or row["ingested_at"]))


def build_support_router(get_connection, topic_services, valid_regions):
    router = APIRouter(prefix="/api/workspace")

    def known_complaint(conn, cid):
        if not conn.execute("SELECT 1 FROM complaints WHERE id = ?", (cid,)).fetchone():
            raise HTTPException(404, "Обращение не найдено")

    def peers(conn, cid, session_id, now):
        conn.execute("DELETE FROM case_presence WHERE complaint_id = ? AND seen_at < ?",
                     (cid, (now - timedelta(seconds=PRESENCE_TTL_SECONDS)).isoformat()))
        return [{"operator_id": r["operator_id"], "name": r["name"], "mode": r["mode"], "seen_at": r["seen_at"]}
                for r in conn.execute("""SELECT p.operator_id, o.name, p.mode, p.seen_at
                    FROM case_presence p JOIN operators o ON o.id = p.operator_id
                    WHERE p.complaint_id = ? AND p.session_id != ?
                    ORDER BY p.seen_at DESC, p.session_id""", (cid, session_id))]

    @router.post("/complaints/{cid}/presence")
    def presence(cid: str, req: PresenceHeartbeat):
        now = datetime.now(timezone.utc)
        with get_connection() as conn:
            known_complaint(conn, cid)
            if not conn.execute("SELECT 1 FROM operators WHERE id = ?", (req.operator_id,)).fetchone():
                raise HTTPException(422, "Неизвестный оператор демо-смены")
            conn.execute("""INSERT INTO case_presence VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(complaint_id, session_id) DO UPDATE SET operator_id = excluded.operator_id,
                mode = excluded.mode, seen_at = excluded.seen_at""",
                (cid, req.session_id, req.operator_id, req.mode, now.isoformat()))
            revision, last_change_at = case_revision(conn, cid)
            return {"peers": peers(conn, cid, req.session_id, now), "revision": revision,
                    "last_change_at": last_change_at, "presence_ttl_seconds": PRESENCE_TTL_SECONDS,
                    "delivery": "demo_only"}

    @router.post("/complaints/{cid}/presence/leave")
    def presence_leave(cid: str, req: PresenceLeave):
        now = datetime.now(timezone.utc)
        with get_connection() as conn:
            known_complaint(conn, cid)
            removed = conn.execute("DELETE FROM case_presence WHERE complaint_id = ? AND session_id = ?",
                                   (cid, req.session_id)).rowcount
            revision, last_change_at = case_revision(conn, cid)
            return {"removed": bool(removed), "peers": peers(conn, cid, req.session_id, now),
                    "revision": revision, "last_change_at": last_change_at, "delivery": "demo_only"}

    @router.get("/routing-health")
    def routing_health(region_id: str | None = None):
        if region_id is not None and region_id not in valid_regions:
            raise HTTPException(422, "Неизвестный регион")
        with get_connection() as conn:
            snapshot = operators_with_load(conn)  # one operator snapshot for all 1600 combos
        total = primary = fallback = uncovered = matched = 0
        items = []
        for topic in sorted(topic_services):
            for rid in sorted(valid_regions):
                for language in DEMO_LANGUAGES:
                    for priority in DEMO_PRIORITIES:
                        result = route({"region_id": rid, "language": language, "district": None,
                                        "priority": priority},
                                       {"category": topic, "suggested_service": topic_services[topic]}, snapshot)
                        total += 1
                        if result["route_type"] == "primary":
                            primary += 1
                        elif result["route_type"] == "fallback":
                            fallback += 1
                        elif any(candidate["stage"] for candidate in result["candidates"]):
                            # Everyone busy: the case is still owned by the matched team queue, not stuck.
                            fallback += 1
                        else:
                            uncovered += 1
                        if result["route_type"] == "primary" or (region_id is not None and rid != region_id):
                            continue
                        matched += 1
                        if len(items) < MAX_HEALTH_ITEMS:
                            operator = result["operator"]
                            items.append({"category": topic, "region_id": rid, "language": language,
                                          "priority": priority, "route_type": result["route_type"],
                                          "reason": result["reason"],
                                          "operator_name": operator["name"] if operator else None})
        return {"summary": {"total": total, "primary": primary, "fallback": fallback, "uncovered": uncovered,
                            "coverage_percent": round(100 * (primary + fallback) / total, 1) if total else 0.0,
                            "primary_percent": round(100 * primary / total, 1) if total else 0.0,
                            "data_origin": "synthetic", "weights": DEMO_WORKLOAD_WEIGHTS,
                            "presence_ttl_seconds": PRESENCE_TTL_SECONDS, "note": HEALTH_NOTE},
                "items": items, "item_count": matched, "region_id": region_id}

    return router

"""Pulse 109 — incidents: schema migration, human detail view and human update.

The base `incidents` table is created by `demo_data.init_workspace`; this module
adds the operator-owned columns and the two tables it needs (incident events and
radar dismissals). `init_incidents` must run after `init_workspace`.

`audit_events` keeps complaint actions, but its `complaint_id` is NOT NULL, so an
incident-only event cannot live there. Incident events get their own table and
both sources are merged into one timeline in `incident_detail`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from triage import SERVICE_NAMES, moment

VALID_INCIDENT_STATUSES = ["Проверяется", "Передано службе", "Работы ведутся", "Завершён"]
ACTIVE_INCIDENT_STATUSES = {s for s in VALID_INCIDENT_STATUSES if s != "Завершён"}
INCIDENT_COLUMNS = {
    "incident_owner_id": "TEXT",
    "created_at": "TEXT",
    "first_signal_at": "TEXT",
    "last_update_at": "TEXT",
    "revision": "INTEGER NOT NULL DEFAULT 1",
    "source_signal": "TEXT",
}
COMPLAINT_EVENT_LABELS = {
    "intake": "Обращение зарегистрировано",
    "classification_proposed": "Предложена категория",
    "operator_confirmed": "Решение подтверждено оператором",
    "incident_linked": "Обращение связано с инцидентом",
    "incident_rejected": "Связь с инцидентом отклонена",
    "clarification_requested": "Запрошено уточнение",
    "clarification_received": "Получено уточнение",
    "radar_ignored": "Сигнал радара отклонён оператором",
    "radar_restored": "Сигнал радара возвращён в работу",
}
SEEDED_TIMELINE_TEXT = (
    "Исходное состояние синтетического демо-инцидента задано seed-данными; "
    "события обнаружения не моделировались."
)


def init_incidents(conn) -> None:
    """Add the incident columns and tables. Idempotent; safe to call on every start."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'incidents'").fetchone():
        raise RuntimeError("init_incidents() must run after demo_data.init_workspace()")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    for name, definition in INCIDENT_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS incident_events (
            id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS radar_ignored (
            signal_id TEXT PRIMARY KEY, signature TEXT NOT NULL, ignored_at TEXT NOT NULL,
            actor TEXT NOT NULL, restored_at TEXT, restored_by TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_source_signal
            ON incidents(source_signal) WHERE source_signal IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id);
        CREATE INDEX IF NOT EXISTS idx_complaints_event_time
            ON complaints(COALESCE(received_at, ingested_at));
        """
    )
    conn.commit()


def incident_event(conn, incident_id: str, event_type: str, text: str, payload: dict | None = None,
                   actor: str = "operator_demo") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO incident_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("iev-" + uuid.uuid4().hex[:10], incident_id, event_type, now, now, actor,
         json.dumps({"text": text, **(payload or {})}, ensure_ascii=False)),
    )


def _payload(raw: Any) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def overdue_minutes(item: dict) -> int | None:
    """Minutes past the promised next update; None when nothing was promised or the case is closed."""
    if not item.get("next_update") or item["status"] not in ACTIVE_INCIDENT_STATUSES:
        return None
    return max(0, round((datetime.now(timezone.utc) - moment(item["next_update"])).total_seconds() / 60))


def incident_timeline(conn, iid: str, item: dict) -> list[dict]:
    entries = [
        {"type": row["event_type"], "at": row["occurred_at"],
         "text": (_payload(row["payload"]).get("text") or row["event_type"])
                 + (" " + _payload(row["payload"])["note"] if _payload(row["payload"]).get("note") else "")}
        for row in conn.execute(
            "SELECT event_type, occurred_at, actor, payload FROM incident_events WHERE incident_id = ?", (iid,))
    ]
    entries += [
        {"type": row["event_type"], "at": row["occurred_at"],
         "text": _payload(row["payload"]).get("text")
                 or _payload(row["payload"]).get("question")
                 or COMPLAINT_EVENT_LABELS.get(row["event_type"], row["event_type"])}
        for row in conn.execute(
            "SELECT a.event_type, a.occurred_at, a.payload FROM audit_events a "
            "JOIN complaints c ON c.id = a.complaint_id WHERE c.incident_id = ? "
            "AND a.event_type != 'classification_proposed'", (iid,))
    ]
    entries.sort(key=lambda e: (e["at"], e["type"], e["text"]))
    if not any(e["type"].startswith(("incident_", "radar_")) for e in entries):
        entries.insert(0, {"type": "seeded_initial_state", "at": item["started_at"], "text": SEEDED_TIMELINE_TEXT})
    return entries


def incident_detail(conn, iid: str) -> dict:
    """One incident with its related cases, owner and timeline. Raises 404 when unknown."""
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
    if not row:
        raise HTTPException(404, "Инцидент не найден")
    item = dict(row)
    members = [dict(r) for r in conn.execute(
        "SELECT id, text, address, topic, decision_status, related_to, received_at, ingested_at "
        "FROM complaints WHERE incident_id = ? ORDER BY COALESCE(received_at, ingested_at), id", (iid,))]
    times = [moment(m["received_at"] or m["ingested_at"]) for m in members]
    item["created_at"] = item.get("created_at") or item["started_at"]
    item["first_signal_at"] = item.get("first_signal_at") or item["started_at"]
    item["last_update_at"] = item.get("last_update_at") or item["created_at"]
    item["revision"] = item.get("revision") or 1
    item["count"] = len(members)
    item["streets"] = len({m["address"].rsplit(" ", 1)[0] for m in members if m["address"]})
    item["service_name"] = SERVICE_NAMES.get(item["service_id"], item["service_id"])
    item["members"] = members
    item["last_related_case_at"] = max(times).isoformat() if times else None
    item["timeline"] = incident_timeline(conn, iid, item)
    item["overdue_minutes"] = overdue_minutes(item)
    return item


class IncidentUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    status: str
    incident_owner_id: str | None = None
    severity: int = Field(ge=1, le=3)
    next_update: str | None = None
    note: str = Field(min_length=1, max_length=2000)
    actor: str = "operator_demo"


def attach_incident_routes(router: APIRouter, get_connection: Callable[[], Any]) -> None:
    @router.get("/incidents/{iid}")
    def get_incident(iid: str):
        with get_connection() as conn:
            return incident_detail(conn, iid)

    @router.post("/incidents/{iid}/update")
    def update_incident(iid: str, req: IncidentUpdate):
        if req.status not in VALID_INCIDENT_STATUSES:
            raise HTTPException(422, f"Недопустимый статус: {req.status}")
        next_update = (req.next_update or "").strip() or None
        if next_update:
            try:
                aware = moment(next_update).tzinfo is not None
            except ValueError:
                aware = False
            if not aware:
                raise HTTPException(
                    422, "next_update указывайте со смещением времени, например 2026-09-24T18:30:00+05:00")
        if req.status in ACTIVE_INCIDENT_STATUSES and not next_update:
            raise HTTPException(422, "Для активного статуса укажите срок следующего обновления")
        if req.status == "Завершён" and next_update:
            raise HTTPException(422, "У завершённого инцидента не может быть срока следующего обновления")
        note = req.note.strip()
        if not note:
            raise HTTPException(422, "Комментарий не может быть пустым")
        owner = (req.incident_owner_id or "").strip() or None
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if owner and not conn.execute("SELECT 1 FROM operators WHERE id = ?", (owner,)).fetchone():
                raise HTTPException(422, f"Неизвестный ответственный: {owner}")
            row = conn.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()
            if not row:
                raise HTTPException(404, "Инцидент не найден")
            if row["revision"] != req.expected_revision:
                raise HTTPException(
                    409, f"Инцидент уже изменён (revision {row['revision']}). Обновите карточку и повторите")
            changed = conn.execute(
                "UPDATE incidents SET status = ?, incident_owner_id = ?, severity = ?, next_update = ?, "
                "revision = revision + 1, last_update_at = ?, created_at = COALESCE(created_at, ?), "
                "first_signal_at = COALESCE(first_signal_at, started_at) "
                "WHERE id = ? AND revision = ?",
                (req.status, owner, req.severity, next_update, now, now, iid, req.expected_revision),
            )
            if changed.rowcount != 1:
                raise HTTPException(409, "Инцидент изменён другим оператором. Обновите карточку и повторите")
            incident_event(
                conn, iid, "incident_updated",
                f"Статус: {req.status}. Ответственный: {owner or 'не назначен'}. Серьёзность: {req.severity}.",
                {"previous_status": row["status"], "status": req.status, "severity": req.severity,
                 "incident_owner_id": owner, "next_update": next_update, "note": note},
                req.actor,
            )
            return {"incident": incident_detail(conn, iid)}

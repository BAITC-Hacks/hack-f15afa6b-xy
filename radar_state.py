"""Persistent operator decisions for Radar episodes; no citizen data is published."""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from auth import current_actor

OPEN_STATES = {"new", "reviewing", "snoozed"}
SOURCES = {"real": ("citizen", "organizer", "public"), "synthetic": ("synthetic",)}


def init_radar_state(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS radar_signals (
            id TEXT PRIMARY KEY, group_key TEXT NOT NULL, data_origin TEXT NOT NULL,
            detected_at TEXT NOT NULL, last_at TEXT NOT NULL, snapshot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new', revision INTEGER NOT NULL DEFAULT 1,
            owner_id TEXT, owner_name TEXT, check_at TEXT, snoozed_until TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_radar_group ON radar_signals(group_key, last_at);
        CREATE TABLE IF NOT EXISTS radar_events (
            id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_radar_events ON radar_events(signal_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_radar_event_time
            ON complaints(julianday(COALESCE(received_at, ingested_at)));
    """)


def event(conn, signal_id, action, payload, now):
    conn.execute("INSERT INTO radar_events VALUES (?, ?, ?, ?, ?, ?)",
                 (uuid.uuid4().hex, signal_id, now.isoformat(), current_actor(), action,
                  json.dumps(payload, ensure_ascii=False)))


def sync_signal(conn, key, signal, minimum, now):
    signature = json.dumps(key, ensure_ascii=False)
    row = conn.execute("SELECT * FROM radar_signals WHERE group_key = ? ORDER BY last_at DESC LIMIT 1",
                       (signature,)).fetchone()
    # A terminal episode can end after an hour without a matching complaint.
    if row and row["status"] not in OPEN_STATES:
        if datetime.fromisoformat(signal["first_at"]) - datetime.fromisoformat(row["last_at"]) > timedelta(hours=1):
            row = None
    if not row and signal["count"] < minimum:
        return None
    if not row:
        legacy_id = "rad-" + hashlib.sha256("|".join([*key[:4], *signal["case_ids"]]).encode()).hexdigest()[:10]
        legacy = conn.execute("SELECT * FROM radar_ignored WHERE signal_id = ?", (legacy_id,)).fetchone()
        signal_id = legacy_id if legacy and key[-1] == "synthetic" else "rad-" + uuid.uuid4().hex[:16]
        status = "dismissed" if legacy and not legacy["restored_at"] and key[-1] == "synthetic" else "new"
        conn.execute("INSERT INTO radar_signals (id, group_key, data_origin, detected_at, last_at, snapshot, status) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (signal_id, signature, key[-1], now.isoformat(), signal["last_at"], "{}", status))
        event(conn, signal_id, "detected", {"count": signal["count"]}, now)
        row = conn.execute("SELECT * FROM radar_signals WHERE id = ?", (signal_id,)).fetchone()
    signal_id = row["id"]
    old = json.loads(row["snapshot"])
    snapshot = json.dumps(signal, ensure_ascii=False, sort_keys=True)
    if snapshot != row["snapshot"]:
        changed = bool(old) and any(old.get(k) != signal[k] for k in ("case_ids", "incident_id", "unlinked_count"))
        status = row["status"]
        if status != "dismissed" and signal["incident_id"] and not signal["unlinked_count"]:
            status = "confirmed"
        if status == "confirmed" and signal["unlinked_count"]:
            status = "reviewing" if row["owner_id"] else "new"
        conn.execute("UPDATE radar_signals SET snapshot = ?, last_at = MAX(last_at, ?), "
                     "revision = revision + ?, status = ? WHERE id = ?",
                     (snapshot, signal["last_at"], int(changed), status, signal_id))
    return signal_id


def stored_signals(conn, origins, live_ids, now):
    marks = ",".join("?" for _ in origins)
    rows = conn.execute(f"SELECT * FROM radar_signals WHERE data_origin IN ({marks})", origins).fetchall()
    items = []
    for row in rows:
        if (row["id"] not in live_ids and row["status"] not in OPEN_STATES
                and datetime.fromisoformat(row["last_at"]) < now - timedelta(days=1)):
            continue
        item = json.loads(row["snapshot"])
        if not item:
            continue
        item.update({k: row[k] for k in ("id", "status", "revision", "owner_id", "owner_name", "check_at",
                                         "snoozed_until", "detected_at", "data_origin")})
        item["ignored"] = row["status"] == "dismissed"
        item["in_current_window"] = row["id"] in live_ids
        item["snoozed"] = bool(row["snoozed_until"] and datetime.fromisoformat(row["snoozed_until"]) > now)
        item["overdue"] = bool(row["check_at"] and row["status"] in OPEN_STATES
                               and datetime.fromisoformat(row["check_at"]) <= now)
        item["history"] = [dict(r) for r in conn.execute(
            "SELECT occurred_at, actor, action, payload FROM radar_events WHERE signal_id = ? "
            "ORDER BY occurred_at DESC, rowid DESC LIMIT 12", (row["id"],))]
        for entry in item["history"]:
            entry["payload"] = json.loads(entry["payload"])
        items.append(item)
    items.sort(key=lambda i: (not i["overdue"], i["snoozed"], i["ignored"], -i["count"], i["id"]))
    return items


class SignalAction(BaseModel):
    expected_revision: int = Field(ge=1)
    action: Literal["claim", "schedule", "snooze", "resume"]
    minutes: int = Field(default=30, ge=5, le=1440)
    note: str = Field(default="", max_length=1000)


def check_revision(signal, revision):
    if signal["revision"] != revision:
        raise HTTPException(409, "Сигнал изменился. Обновите карточку и повторите действие.")


def update_signal(conn, signal, req):
    check_revision(signal, req.expected_revision)
    if signal["status"] not in OPEN_STATES:
        raise HTTPException(409, "Сначала верните сигнал в работу.")
    actor = current_actor()
    if signal["owner_id"] and signal["owner_id"] != actor:
        raise HTTPException(409, "Сигнал уже проверяет другой оператор.")
    user = conn.execute("SELECT full_name FROM auth_users WHERE id = ? AND disabled = 0 "
                        "AND role IN ('admin', 'operator')", (actor,)).fetchone()
    name = user["full_name"] if user else "Демо-оператор" if actor == "operator_demo" else None
    if not name:
        raise HTTPException(403, "Нужна учётная запись оператора.")
    now = datetime.now(timezone.utc)
    check_at = (now + timedelta(minutes=req.minutes)).isoformat()
    snoozed = check_at if req.action == "snooze" else None
    status = "snoozed" if snoozed else "reviewing"
    conn.execute("UPDATE radar_signals SET status = ?, owner_id = ?, owner_name = ?, check_at = ?, "
                 "snoozed_until = ?, revision = revision + 1 WHERE id = ?",
                 (status, actor, name, check_at, snoozed, signal["id"]))
    event(conn, signal["id"], req.action, {"owner_name": name, "check_at": check_at,
                                          "note": req.note.strip()}, now)


def finish_signal(conn, signal, status):
    now = datetime.now(timezone.utc)
    conn.execute("UPDATE radar_signals SET status = ?, snoozed_until = NULL, revision = revision + 1 WHERE id = ?",
                 (status, signal["id"]))
    event(conn, signal["id"], status, {"count": signal["count"]}, now)

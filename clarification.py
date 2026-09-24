"""Pulse 109 — needs-clarification workflow routes.

Implements decision_status: pending -> needs_clarification -> pending.
State changes and audit events are written in one transaction; the original
complaint text is never overwritten.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from playbooks import faq_closed

VALID_CLARIFICATION_REASONS = {
    "unknown_place",
    "unclear_event",
    "insufficient_detail",
    "multiple_problems",
    "other",
}


class ClarificationRequest(BaseModel):
    reason: str
    question: str = Field(..., min_length=1, max_length=1000)
    actor: str = "operator_demo"


class ClarificationResponse(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    actor: str = "operator_demo"


def get_received_clarifications(conn, complaint_id: str) -> list[str]:
    """Return received clarifications in chronological order; the original text stays untouched."""
    rows = conn.execute(
        "SELECT payload FROM audit_events WHERE complaint_id = ? AND event_type = 'clarification_received' "
        "ORDER BY occurred_at ASC",
        (complaint_id,),
    ).fetchall()
    texts = []
    for row in rows:
        try:
            text = json.loads(row["payload"]).get("text")
        except (ValueError, AttributeError):
            text = None
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


def build_clarification_router(get_connection: Callable[[], Any], banner_text: str) -> APIRouter:
    router = APIRouter()

    def complaint_with_events(conn, complaint_id: str) -> dict:
        updated = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        events = conn.execute(
            "SELECT * FROM audit_events WHERE complaint_id = ? ORDER BY occurred_at ASC, recorded_at ASC",
            (complaint_id,),
        ).fetchall()
        return {"complaint": dict(updated), "events": [dict(e) for e in events], "banner": banner_text}

    @router.post("/api/complaints/{complaint_id}/clarification")
    def request_clarification(complaint_id: str, req: ClarificationRequest):
        if req.reason not in VALID_CLARIFICATION_REASONS:
            raise HTTPException(status_code=422, detail=f"Invalid clarification reason: {req.reason}")
        question = req.question.strip()
        if not question:
            raise HTTPException(status_code=422, detail="Question cannot be blank")
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Complaint not found")
            if row["decision_status"] != "pending":
                raise HTTPException(
                    status_code=409, detail=f"Cannot request clarification from status: {row['decision_status']}"
                )
            if row["resolved_at"] and faq_closed(conn, complaint_id):
                raise HTTPException(
                    status_code=409, detail="Типовой вопрос закрыт сценарием FAQ; сначала верните его в работу"
                )
            conn.execute("UPDATE complaints SET decision_status = 'needs_clarification' WHERE id = ?", (complaint_id,))
            conn.execute(
                "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
                "VALUES (?, ?, 'clarification_requested', ?, ?, ?, ?)",
                (
                    f"evt-{uuid.uuid4().hex[:8]}",
                    complaint_id,
                    now_iso,
                    now_iso,
                    req.actor,
                    json.dumps({"reason": req.reason, "question": question}, ensure_ascii=False),
                ),
            )
            conn.commit()
            result = complaint_with_events(conn, complaint_id)
        return result

    @router.post("/api/complaints/{complaint_id}/clarification-response")
    def record_clarification_response(complaint_id: str, req: ClarificationResponse):
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Clarification text cannot be blank")
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Complaint not found")
            if row["decision_status"] != "needs_clarification":
                raise HTTPException(
                    status_code=409, detail=f"Cannot record clarification from status: {row['decision_status']}"
                )
            conn.execute(
                "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
                "VALUES (?, ?, 'clarification_received', ?, ?, ?, ?)",
                (
                    f"evt-{uuid.uuid4().hex[:8]}",
                    complaint_id,
                    now_iso,
                    now_iso,
                    req.actor,
                    json.dumps({"text": text, "text_len": len(text)}, ensure_ascii=False),
                ),
            )
            conn.commit()
            result = complaint_with_events(conn, complaint_id)
        return result

    @router.post("/api/complaints/{complaint_id}/resume")
    def resume_complaint(complaint_id: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Complaint not found")
            if row["decision_status"] != "needs_clarification":
                raise HTTPException(status_code=409, detail=f"Cannot resume from status: {row['decision_status']}")
            received = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE complaint_id = ? AND event_type = 'clarification_received'",
                (complaint_id,),
            ).fetchone()[0]
            if not received:
                raise HTTPException(status_code=409, detail="No received clarification to resume from")
            conn.execute("UPDATE complaints SET decision_status = 'pending' WHERE id = ?", (complaint_id,))
            conn.execute(
                "INSERT INTO audit_events (id, complaint_id, event_type, occurred_at, recorded_at, actor, payload) "
                "VALUES (?, ?, 'clarification_resolved', ?, ?, 'operator_demo', ?)",
                (f"evt-{uuid.uuid4().hex[:8]}", complaint_id, now_iso, now_iso, json.dumps({})),
            )
            conn.commit()
            result = complaint_with_events(conn, complaint_id)
        return result

    return router

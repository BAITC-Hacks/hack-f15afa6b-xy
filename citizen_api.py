"""Account-owned complaints and their citizen-facing history."""
import json
from fastapi import HTTPException
from auth import current_actor
from triage import responsible_service_name


def attach_citizen_routes(router, get_connection, complaint, private_media):
    @router.get("/citizen/complaints")
    def my_complaints():
        with get_connection() as conn:
            rows = conn.execute("""SELECT id, text, decision_status AS status,
                COALESCE((SELECT MAX(occurred_at) FROM audit_events WHERE complaint_id = complaints.id),
                         resolved_at, ingested_at) AS last_updated, resolved_at
                FROM complaints WHERE owner_user_id = ? ORDER BY ingested_at DESC""",
                (current_actor(),)).fetchall()
        return {"items": [dict(row, status="resolved" if row["resolved_at"] else row["status"]) for row in rows]}

    @router.get("/citizen/complaints/{cid}/{kind}")
    def owner_media(cid: str, kind: str):
        if kind not in {"photo", "video"}:
            raise HTTPException(404, "Вложение не найдено")
        with get_connection() as conn:
            row = conn.execute("SELECT 1 FROM complaints WHERE id = ? AND owner_user_id = ?",
                               (cid, current_actor())).fetchone()
        if not row:
            raise HTTPException(404, "Вложение не найдено")
        return private_media(cid, "complaint_photos" if kind == "photo" else "complaint_videos")

    @router.get("/tracking/{cid}")
    def tracking(cid: str):
        with get_connection() as conn:
            cid = cid.strip()
            c = complaint(conn, cid.upper() if cid.upper().startswith("PULSE-") else cid)
            owned = c.get("owner_user_id") == current_actor()
            if c["data_origin"] != "synthetic" and not owned:
                raise HTTPException(404, "Обращение не найдено")
            status = "resolved" if c["resolved_at"] else "under_review" if c["quarantined"] else c["decision_status"]
            rows = list(conn.execute("""SELECT event_type, occurred_at, payload FROM audit_events
                    WHERE complaint_id = ? ORDER BY occurred_at, recorded_at, rowid""", (c["id"],)))
            updates = []
            for row in rows:
                if row["event_type"] not in {"reply_saved", "clarification_requested", "clarification_received"}:
                    continue
                payload = json.loads(row["payload"])
                updates.append({"type": row["event_type"], "at": row["occurred_at"],
                                "text": payload.get("question") if row["event_type"] == "clarification_requested" else payload.get("text")})
            if status == "needs_clarification" and updates:
                clarification = [u for u in updates if u["type"].startswith("clarification_")]
                if clarification and clarification[-1]["type"] == "clarification_received":
                    status = "clarification_received"
            incident = conn.execute("SELECT id, title, status, next_update FROM incidents WHERE id = ?", (c["incident_id"],)).fetchone()
            service = (responsible_service_name(c["region_id"], c["service_id"])
                       if c["decision_status"] == "confirmed" else None)
            registered_at = c["received_at"] or c["ingested_at"]
            timeline = [{"type": "registered", "at": registered_at,
                         "title": "Обращение зарегистрировано", "text": "Номер обращения сохранён."}]
            event_titles = {
                "operator_confirmed": ("Принято в работу", "Оператор подтвердил категорию и ответственную службу."),
                "incident_linked": ("Связано с массовым инцидентом", "Обращение добавлено к общей проблеме."),
                "incident_rejected": ("Проверено отдельно", "Оператор исключил связь с массовым инцидентом."),
                "clarification_requested": ("Нужно уточнение", None),
                "clarification_received": ("Уточнение получено", None),
                "reply_saved": ("Ответ оператора", None),
                "case_resolved": ("Обращение завершено", c["resolution_text"]),
                "case_reopened": ("Обращение возвращено в работу", "Оператор продолжил обработку."),
                "quarantined": ("Дополнительная проверка", "Обращение проверяется оператором."),
                "safety_cleared": ("Проверка завершена", "Обращение возвращено в обычную очередь."),
            }
            for row in rows:
                if row["event_type"] not in event_titles:
                    continue
                payload = json.loads(row["payload"])
                title, default_text = event_titles[row["event_type"]]
                text = payload.get("question") or payload.get("text") or default_text
                timeline.append({"type": row["event_type"], "at": row["occurred_at"],
                                 "title": title, "text": text})
            return {"id": c["id"], "registered_at": c["received_at"] or c["ingested_at"], "status": status,
                    "service_name": service, "incident": dict(incident) if incident else None, "updates": updates,
                    "timeline": timeline,
                    "city": c.get("city_name"), "has_photo": c["has_photo"], "has_video": c["has_video"],
                    "location": ({"latitude": c["latitude"], "longitude": c["longitude"]}
                                 if c["latitude"] is not None and c["longitude"] is not None else None),
                    "resolved_at": c["resolved_at"], "resolution_text": c["resolution_text"] if c["resolved_at"] else None,
                    "data_origin": c["data_origin"], "owned": owned,
                    "delivery": "demo_only" if c["data_origin"] == "synthetic" else "in_app"}

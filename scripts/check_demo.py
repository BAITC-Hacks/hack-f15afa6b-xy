"""Exercise the operator demo through real HTTP against an isolated SQLite DB."""

import json
import sqlite3
import tempfile
from pathlib import Path

from check_clarification import start_server, stop_server
from smoke import find_free_port, http_request


def run():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        proc, url = start_server(root, db, find_free_port())

        def call(path, body=None, expected=200):
            code, data = http_request(url + path, "POST" if body is not None else "GET", body)
            assert code == expected, (path, code, data)
            return data

        def intake(text, **extra):
            return call("/api/workspace/intake", {
                "text": text, "region_id": "KZ-ALA", "language": "ru",
                "district": "Алмалинский", "channel": "web", **extra,
            }, 201)["id"]

        try:
            call("/api/workspace/seed", {})
            call("/api/workspace/seed", {})
            queue = call("/api/workspace/queue")
            assert len(queue["items"]) == 50, "Seed must be idempotent and retain 20 originals"
            print("PASS 1: 50 synthetic complaints; idempotent seed preserves original records")

            cid = intake("Добрый день, на Абая 44 с утра нет воды, весь дом без воды, когда включат?")
            detail = call(f"/api/workspace/complaints/{cid}/triage", {})
            ai = detail["triage"]
            assert ai["category"] == "water_supply" and ai["category_confidence"] == .94
            assert ai["confidence_kind"] == "synthetic_demo" and ai["checkpoint_id"] is None
            assert ai["extracted_address"] == "Абая 44" and ai["confidence_band"] == "high"
            assert detail["incident_candidate"]["id"] == "INC-204"
            assert len(detail["similar"]) == 17
            assert detail["routing"]["operator"]["id"] == "op-aidana"
            assert detail["routing"]["operator"]["current_load"] == 2
            assert detail["complaint"]["decision_status"] == "pending"
            assert detail["complaint"]["incident_id"] is None
            tracked = call(f"/api/workspace/tracking/{cid.lower()}")
            assert tracked["id"] == cid and tracked["status"] == "pending"
            assert tracked["service_name"] is None and tracked["incident"] is None and tracked["updates"] == []
            print("PASS 2: RU intake → 94% demo confidence → 17 candidates → Aidana 2/5; no auto-decision")

            decision = {"topic": "water_supply", "priority": "normal", "operator_id": "op-aidana", "incident_id": "INC-204"}
            call(f"/api/workspace/complaints/{cid}/decide", decision)
            call(f"/api/workspace/complaints/{cid}/decide", decision, 409)
            result = call(f"/api/complaints/{cid}")
            assert result["complaint"]["incident_id"] == "INC-204"
            assert result["complaint"]["assigned_operator"] == "op-aidana"
            assert result["complaint"]["related_to"] and result["complaint"]["duplicate_of"] is None
            assert result["complaint"]["text"].startswith("Добрый день")
            assert "operator_confirmed" in [e["event_type"] for e in result["events"]]
            metrics = call("/api/workspace/metrics")
            assert metrics["linked"] == 18 and metrics["total"] == 51
            assert next(x for x in call("/api/workspace/operators")["items"] if x["id"] == "op-aidana")["current_load"] == 3
            call(f"/api/complaints/{cid}/confirm", {"topic": "roads", "service_id": "srv_roads", "priority": "normal"}, 409)
            print("PASS 3: atomic confirm + link + assign; counters/load update, double-submit rejected")

            kk = intake("Абай 46 үйде су жоқ, бүкіл үй сусыз қалды", language="kk")
            assert call(f"/api/workspace/complaints/{kk}/triage", {})["triage"]["category"] == "water_supply"
            medium = intake("На Абая 44 течёт вода из трубы или люка, не понимаю откуда")
            m = call(f"/api/workspace/complaints/{medium}/triage", {})
            assert m["triage"]["confidence_band"] == "medium" and len(m["triage"]["alternatives"]) == 2
            assert m["incident_candidate"] is None, "Leak must not merge with water outage"
            low = intake("Здесь опять эта проблема, помогите")
            low_detail = call(f"/api/workspace/complaints/{low}/triage", {})
            assert low_detail["triage"]["confidence_band"] == "low" and low_detail["triage"]["clarification_question"]
            call(f"/api/workspace/complaints/{low}/decide", {"topic": "water_supply", "priority": "normal"}, 409)
            call(f"/api/complaints/{low}/clarification", {"reason": "unclear_event", "question": "Что произошло?"})
            tracked = call(f"/api/workspace/tracking/{low}")
            assert tracked["status"] == "needs_clarification" and tracked["updates"][-1]["text"] == "Что произошло?"
            call(f"/api/complaints/{low}/clarification-response", {"text": "На Абая 44 нет холодной воды"})
            assert call(f"/api/workspace/tracking/{low}")["status"] == "clarification_received"
            call(f"/api/complaints/{low}/resume", {})
            assert call(f"/api/workspace/tracking/{low}")["status"] == "pending"
            call(f"/api/complaints/{low}/clarification", {"reason": "unknown_place", "question": "Какой подъезд?"})
            assert call(f"/api/workspace/tracking/{low}")["status"] == "needs_clarification", "Old answers must not hide a new question"
            call(f"/api/complaints/{low}/clarification-response", {"text": "Первый"})
            call(f"/api/complaints/{low}/resume", {})
            assert call(f"/api/workspace/complaints/{low}/triage", {})["triage"]["category"] == "water_supply"
            print("PASS 4: KK, medium alternatives, low-confidence clarification and reanalysis")

            unrelated = intake("На Абая 44 нет воды", district="Бостандыкский")
            assert call(f"/api/workspace/complaints/{unrelated}/triage", {})["incident_candidate"] is None
            call(f"/api/workspace/complaints/{unrelated}/link", {"incident_id": "INC-204"}, 409)
            call(f"/api/workspace/complaints/{kk}/link", {"incident_id": "INC-204", "separate": True})
            assert call(f"/api/workspace/complaints/{kk}/triage", {})["incident_candidate"] is None
            call(f"/api/workspace/complaints/{medium}/link", {"incident_id": "INC-204"}, 409)
            print("PASS 5: district/symptom constraints and operator rejection prevent false incident links")

            spam = call("/api/workspace/complaints/PULSE-2420/triage", {})
            assert spam["triage"]["category"] is None, "Substring 'ток' in 'заработок' is not electricity"
            assert "spam_suspected" in spam["flags"] and spam["risk"]["reasons"]
            call("/api/workspace/complaints/PULSE-2420/safety", {"quarantine": True})
            assert call("/api/workspace/tracking/PULSE-2420")["status"] == "under_review"
            assert call("/api/workspace/metrics")["quarantined"] == 1
            assert call("/api/complaints/PULSE-2420")["complaint"]["text"] == spam["complaint"]["text"]
            call("/api/workspace/complaints/PULSE-2420/decide", {"topic": "roads", "priority": "normal"}, 409)
            call("/api/workspace/complaints/PULSE-2420/safety", {"quarantine": False})
            assert call("/api/workspace/tracking/PULSE-2420")["status"] == "pending"
            assert "spam_suspected" not in call("/api/workspace/complaints/PULSE-2420/triage", {})["flags"]
            print("PASS 6: reversible human quarantine retains text, blocks assignment, supports dismissal")

            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE operators SET base_load = capacity WHERE id = 'op-aidana'")
            assigned = call(f"/api/workspace/complaints/{cid}/triage", {})["routing"]
            assert assigned["assigned"] and assigned["operator"]["id"] == "op-aidana"
            fallback = call(f"/api/workspace/complaints/{kk}/triage", {})["routing"]
            assert fallback["operator"] is None or fallback["operator"]["id"] != "op-aidana"
            call(f"/api/workspace/complaints/{kk}/decide", decision, 409)
            assert call(f"/api/complaints/{kk}")["complaint"]["decision_status"] == "pending"
            print("PASS 7: capacity is rechecked on write; failed assignment rolls back")

            call("/api/workspace/intake", {"text": " ", "region_id": "KZ-ALA"}, 422)
            call("/api/workspace/intake", {"text": "тест", "region_id": "invalid"}, 422)
            call("/api/workspace/complaints/missing/triage", {}, 404)
            call(f"/api/workspace/complaints/{kk}/decide", {"topic": "invalid", "priority": "normal"}, 422)
            call("/api/workspace/incidents/INC-204/subscribe", {"subscriber_key": "synthetic-browser-1"})
            sub = call("/api/workspace/incidents/INC-204/subscribe", {"subscriber_key": "synthetic-browser-1"})
            assert sub["subscribers"] == 1
            call(f"/api/workspace/complaints/{cid}/reply", {"text": "Ваше обращение зарегистрировано."})
            assert any(e["event_type"] == "reply_saved" for e in call(f"/api/complaints/{cid}")["events"])
            print("PASS 8: boundaries, idempotent subscription and explicitly saved reply")

            stop_server(proc)
            proc, url = start_server(root, db, find_free_port())
            assert call(f"/api/complaints/{cid}")["complaint"]["incident_id"] == "INC-204"
            assert call("/api/workspace/metrics")["linked"] == 18
            print("PASS 9: decisions, relationships and analytics survive restart")

            before = call(f"/api/complaints/{cid}")
            tracked = call(f"/api/workspace/tracking/{cid}")
            assert tracked["status"] == "confirmed" and tracked["service_name"] == "Алматинский Су"
            assert tracked["incident"]["id"] == "INC-204" and "members" not in tracked["incident"]
            assert tracked["updates"][-1]["text"] == "Ваше обращение зарегистрировано."
            assert tracked["delivery"] == "demo_only" and tracked["data_origin"] == "synthetic"
            assert not {"text", "sender_key", "assigned_operator", "risk", "triage"} & tracked.keys()
            assert call(f"/api/complaints/{cid}") == before, "Tracking must not write proposals or audit events"
            resolved = call("/api/workspace/tracking/syn-001")
            assert resolved["status"] == "resolved" and resolved["resolution_text"] and resolved["resolved_at"]
            call("/api/workspace/tracking/missing", expected=404)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE complaints SET data_origin = 'organizer' WHERE id = ?", (kk,))
            call(f"/api/workspace/tracking/{kk}", expected=404)
            print("PASS 10: read-only citizen tracking, clarification cycles, saved replies, resolution and synthetic-only boundary")
        finally:
            stop_server(proc)
    print("ALL 10 OPERATOR DEMO CHECKS PASSED")


if __name__ == "__main__":
    run()

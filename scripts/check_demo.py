"""Exercise the operator demo through real HTTP against an isolated SQLite DB."""

import json
import sqlite3
import tempfile
import urllib.request
from pathlib import Path

from check_clarification import start_server, stop_server
from cities import CITIES
from smoke import find_free_port, http_request
from workspace_api import normalized_address_query


def run():
    assert len(CITIES) == 90 and len({city["code"] for city in CITIES}) == 90
    assert normalized_address_query("жетысу 1, дом 26") == "микрорайон жетысу 1, 26, Алматы"
    assert normalized_address_query("Кабанбай батыра 10", "Астана") == "Кабанбай батыра 10, Астана"
    assert normalized_address_query("Тауке хана 5", "Шымкент") == "Тауке хана 5, Шымкент"
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
            cities = call("/api/workspace/cities")
            assert cities["count"] == 90 and cities["updated_at"] == "2026-09-18"
            call("/api/workspace/intake", {"text": "Проверка города", "region_id": "KZ-ALA",
                                           "city_code": "710000000"}, 422)
            queue = call("/api/workspace/queue")
            assert len(queue["items"]) == 50, "Seed must be idempotent and retain 20 originals"
            print("PASS 1: 50 synthetic complaints; idempotent seed and 90-city KATO directory")

            cid = intake("Добрый день, на Абая 44 с утра нет воды, весь дом без воды, когда включат?",
                         city_code="750000000", latitude=43.2389494, longitude=76.9451234,
                         location_accuracy_m=9.44,
                         photo_data="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X9ZxAAAAAElFTkSuQmCC")
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
            assert detail["complaint"]["latitude"] == 43.238949
            assert detail["complaint"]["longitude"] == 76.945123
            assert detail["complaint"]["location_accuracy_m"] == 9.4
            tracked = call(f"/api/workspace/tracking/{cid.lower()}")
            assert tracked["id"] == cid and tracked["status"] == "pending"
            assert tracked["location"] == {"latitude": 43.238949, "longitude": 76.945123}
            assert tracked["has_photo"] and tracked["service_name"] is None and tracked["incident"] is None and tracked["updates"] == []
            public = call("/api/workspace/public/complaints")
            public_case = next(item for item in public["items"] if item["id"] == cid)
            assert public_case["has_photo"] and public_case["city"] == "Алматы"
            assert public_case["topic"] == "water_supply" and public_case["topic_name"]
            assert public_case["latitude"] == 43.239 and public_case["longitude"] == 76.945
            assert public_case["location_precision_m"] == 100 and public_case["location_source"] == "user_selected"
            assert not {"address", "sender_key", "assigned_operator", "service_id"} & public_case.keys()
            approximate = next(item for item in public["items"] if item["id"] == "PULSE-2400")
            assert approximate["location_source"] == "district_approximate" and approximate["location_precision_m"] == 2500
            assert not any(item["id"] == "PULSE-2420" for item in public["items"]), "Suspected spam must stay private"
            similar = call("/api/workspace/public/similar", {
                "text": "На Абая 44 нет воды во всём доме", "region_id": "KZ-ALA",
                "city_code": "750000000", "district": "Алмалинский", "address": "Абая 44",
                "latitude": 43.23895, "longitude": 76.94512,
            })
            assert similar["items"][0]["id"] == cid and similar["items"][0]["distance_m"] <= 1
            subscribed = call(f"/api/workspace/public/complaints/{cid}/subscribe", {"subscriber_key": "demo-user-123"})
            subscribed_again = call(f"/api/workspace/public/complaints/{cid}/subscribe", {"subscriber_key": "demo-user-123"})
            assert subscribed["subscribers"] == subscribed_again["subscribers"] == 1
            subscriptions = call("/api/workspace/public/subscriptions/demo-user-123")
            assert subscriptions["delivery"] == "in_app" and subscriptions["items"][0]["id"] == cid
            with urllib.request.urlopen(url + f"/api/workspace/public/complaints/{cid}/photo") as response:
                assert response.headers.get_content_type() == "image/png" and response.read().startswith(b"\x89PNG")
            print("PASS 2: safe public location, duplicate suggestion, idempotent subscription and photo")

            decision = {"topic": "water_supply", "priority": "normal", "operator_id": "op-aidana", "incident_id": "INC-204"}
            call(f"/api/workspace/complaints/{cid}/decide", decision)
            call(f"/api/workspace/complaints/{cid}/decide", decision, 409)
            result = call(f"/api/complaints/{cid}")
            assert result["complaint"]["incident_id"] == "INC-204"
            assert result["complaint"]["assigned_operator"] == "op-aidana"
            assert result["complaint"]["city_code"] == "750000000"
            assert result["complaint"]["related_to"] and result["complaint"]["duplicate_of"] is None
            assert result["complaint"]["text"].startswith("Добрый день")
            assert "operator_confirmed" in [e["event_type"] for e in result["events"]]
            metrics = call("/api/workspace/metrics")
            assert metrics["linked"] == 18 and metrics["total"] == 51
            evidence = metrics["ai_evidence"]
            assert evidence["stage"] == "shadow" and evidence["hardware"]["gpu_count"] == 2
            assert evidence["category_accuracy"] == {"baseline": .375, "trained": .703125}
            assert len(evidence["evidence_sha256"]["laya"]) == 64
            assert evidence["similarity"]["test"]["trained"]["ndcg_at_10"] == .9662
            assert len(evidence["evidence_sha256"]["similarity"]) == 64
            forecast = call("/api/forecast?horizon_months=3")
            assert forecast["method"] == "last_value" and forecast["evaluation"]["smape_percent"] < 25
            assert forecast["excluded_partial_month"] == {"month": "2025-11", "count": 594}
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
            call("/api/workspace/intake", {"text": "тест", "region_id": "KZ-ALA", "latitude": 43.2}, 422)
            call("/api/workspace/intake", {"text": "тест", "region_id": "KZ-ALA",
                                           "photo_data": "data:image/png;base64,bm90LWEtcG5n"}, 422)
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
            timeline_types = [item["type"] for item in tracked["timeline"]]
            assert timeline_types[0] == "registered" and "operator_confirmed" in timeline_types
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

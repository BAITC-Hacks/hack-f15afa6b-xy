"""P0 live-call, operations map, intraday forecast and simulation check."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clarification import start_server, stop_server  # noqa: E402
from operations_core import cell_neighbors, operations_cell, simulate_queue  # noqa: E402
from smoke import find_free_port, http_request  # noqa: E402


def run():
    old_env = {name: os.environ.get(name) for name in (
        "P109_DECISION_PROVIDER", "P109_LAYA_BASE_URL", "P109_LAYA_TIMEOUT"
    )}
    os.environ.update({
        "P109_DECISION_PROVIDER": "shadow",
        "P109_LAYA_BASE_URL": "http://127.0.0.1:9",
        "P109_LAYA_TIMEOUT": ".05",
    })
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "operations.db"
        process, base = start_server(ROOT, db, find_free_port())

        def call(path, body=None, expected=200):
            status, data = http_request(base + path, "POST" if body is not None else "GET", body)
            assert status == expected, (path, status, data)
            return data

        try:
            call("/api/workspace/seed", {})
            session = call("/api/operations/live/sessions", {
                "region_id": "KZ-ALA", "language": "ru", "channel": "phone",
                "latitude": 43.238949, "longitude": 76.917806,
            }, 201)
            session_id = session["id"]
            first = call(f"/api/operations/live/sessions/{session_id}/transcript", {
                "text": "На Абая 44", "event_type": "transcript_partial", "speech_pause": True,
                "stt_latency_ms": 420,
            })
            skipped = call(f"/api/operations/live/sessions/{session_id}/transcript", {
                "text": "На Абая 44", "event_type": "transcript_partial", "speech_pause": False,
            })
            assert first["processed"] and not skipped["processed"] and skipped["next_checkpoint_ms"] > 0
            print("PASS 1: partial transcript uses semantic checkpoints and debounce")

            final = call(f"/api/operations/live/sessions/{session_id}/transcript", {
                "text": "На Абая 44 с утра нет холодной воды во всём доме.",
                "event_type": "transcript_final", "speech_pause": True, "stt_latency_ms": 390,
            })
            state = final["state"]
            assert state["detected"]["category"] == "water_supply"
            assert state["detected"]["address"] == "Абая 44"
            assert state["triage"]["human_confirmation_required"] is True
            assert state["triage"]["fallback_reason"]
            assert state["incident_candidate"]["id"] == "INC-204"
            assert state["incident_candidate"]["similar_complaints"] == 17
            assert state["timings_ms"]["live_triage_latency"] >= 0
            print("PASS 2: unavailable Laya falls back; live triage still finds INC-204")

            stored = call(f"/api/operations/live/sessions/{session_id}")
            event_types = [item["event_type"] for item in stored["events"]]
            required = {"transcript_partial", "transcript_final", "live_triage_updated",
                        "live_incident_candidate", "live_copilot_suggestion"}
            assert required <= set(event_types) and event_types.count("transcript_partial") == 1
            print("PASS 3: only meaningful live state changes are persisted as named events")

            applied = call(f"/api/operations/live/sessions/{session_id}/apply", {"link_incident": True})
            assert applied["decision_status"] == "pending" and not applied["category_confirmed"]
            assert applied["incident_id"] == "INC-204"
            replay = call(f"/api/operations/live/sessions/{session_id}/apply", {"link_incident": True})
            assert replay["id"] == applied["id"] and replay["replayed"]
            detail = call(f"/api/complaints/{applied['id']}")
            assert detail["complaint"]["decision_status"] == "pending"
            assert detail["complaint"]["proposed_topic"] == "water_supply"
            assert {item["event_type"] for item in detail["events"]} >= {
                "intake", "classification_proposed", "incident_linked", "live_call_applied"
            }
            print("PASS 4: Apply is idempotent, links the incident, and leaves category unconfirmed")

            map_data = call("/api/operations/map?mode=heat&minutes=60")
            assert map_data["cell_method"] == "local_hex_v1" and map_data["cells"]["features"]
            assert map_data["clusters"] and any(item["category"] == "water_supply" for item in map_data["clusters"])
            with sqlite3.connect(db) as conn:
                cell = conn.execute("SELECT ops_cell FROM complaints WHERE id = ?", (applied["id"],)).fetchone()[0]
            assert cell == operations_cell(43.238949, 76.917806)
            nearby = operations_cell(43.247, 76.918)
            assert nearby == cell or nearby in cell_neighbors(cell)
            print("PASS 5: exact coordinates persist one local hex cell and neighbours form a heat cluster")

            forecast = call("/api/operations/forecast?horizon_minutes=60")
            water = next(item for item in forecast["queues"] if item["queue"] == "water_supply")
            assert forecast["label"] == "Synthetic simulation"
            assert water["predicted_incoming"] > 0 and water["predicted_backlog"] >= water["current_backlog"]
            assert water["recommended_operators"] > 0 and water["estimated_wait_seconds"] is not None
            incident_rate = water["arrival_rate_per_minute"]
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE incidents SET status = 'Завершён' WHERE id = 'INC-204'")
                conn.commit()
            without_incident = call("/api/operations/forecast?horizon_minutes=60")
            quiet_water = next(item for item in without_incident["queues"] if item["queue"] == "water_supply")
            assert quiet_water["arrival_rate_per_minute"] < incident_rate
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE incidents SET status = 'Работы ведутся' WHERE id = 'INC-204'")
                conn.execute("UPDATE operators SET status = 'away'")
                conn.commit()
            no_capacity = call("/api/operations/forecast?horizon_minutes=30")
            zero_water = next(item for item in no_capacity["queues"] if item["queue"] == "water_supply")
            assert zero_water["current_operators"] == 0 and zero_water["estimated_wait_seconds"] is None
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE operators SET status = 'online' WHERE id != 'op-away'")
                conn.commit()
            print("PASS 6: intraday forecast covers rising load, incident spike and zero capacity")

            request = {"queue": "water_supply", "horizon_minutes": 60, "incoming_percent": 80,
                       "operator_delta": 0, "handle_time_percent": 0, "active_incident": True,
                       "priority_percent": 20, "seed": 109}
            simulation = call("/api/operations/simulate", request)
            repeated = call("/api/operations/simulate", request)
            assert simulation["current"] == repeated["current"]
            assert simulation["tested_scenarios"] == repeated["tested_scenarios"]
            assert simulation["best_tested_scenario"]["wait_seconds"] <= simulation["current"]["wait_seconds"]
            assert simulate_queue(10, 1, 0, 8, 60, 20, 109)["wait_seconds"] is None
            assert simulate_queue(10, 3, 1, 8, 60, 20, 109)["queue"] > 0
            print("PASS 7: discrete-event simulation is seeded, overload-safe and improved by operators")

            command = call("/api/operations/command-center")
            assert set(command["cards"]) == {"queue", "critical", "sla_risk", "operators_online",
                                             "active_incidents", "system_health"}
            assert command["hot_queues"] and command["staffing_recommendations"]
            print("PASS 8: supervisor command center returns compact live cards and staffing actions")

            stop_server(process)
            process, base = start_server(ROOT, db, find_free_port())
            restored = call(f"/api/operations/live/sessions/{session_id}")
            runs = call("/api/operations/simulations")
            assert restored["status"] == "applied" and restored["applied_complaint_id"] == applied["id"]
            assert len(runs["items"]) == 2
            print("PASS 9: live session, linked case and simulation runs survive restart")
            print("ALL 9 OPERATIONS CHECKS PASSED")
        finally:
            stop_server(process)
    for name, value in old_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    run()

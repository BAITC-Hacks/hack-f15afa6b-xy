"""Pulse 109 — radar and incident control center through real HTTP.

Starts the real application on an isolated synthetic SQLite database and checks:
signal detection and false positives, count/baseline honesty, the demo batch, stale
previews, creation/link/replay, human dismiss/restore, ambiguity refusal, revision
conflicts, completion without a complaint cascade, audit trails and restart
persistence. Nothing here asserts a trained-model claim.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clarification import start_server, stop_server  # noqa: E402
from smoke import find_free_port, http_request  # noqa: E402

SIGNAL_FIELDS = {
    "id", "title", "category", "region_id", "district", "count", "similar_count", "window_minutes",
    "first_at", "last_at", "baseline_count", "growth", "case_ids", "members", "incident_id",
    "unlinked_count", "ignored", "reason",
}
OUTAGE_TEXT = "Медеуский район, дом {n}: с утра нет холодной воды, весь дом без воды, просим проверить."


def at(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def run():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "incidents.db"
        proc, url = start_server(ROOT, db, find_free_port())
        print(f"[*] Radar/incident check server: {url}\n[*] Isolated temporary database: {db}\n")

        def call(path, body=None, expected=200):
            code, data = http_request(url + path, "POST" if body is not None else "GET", body)
            assert code == expected, (path, code, data)
            return data

        def incidents():
            return {item["id"]: item for item in call("/api/workspace/incidents")["items"]}

        def radar():
            return call("/api/workspace/radar")

        def intake(text, **extra):
            payload = {"text": text, "region_id": "KZ-ALA", "language": "ru", "district": "Медеуский",
                       "channel": "web", **extra}
            return call("/api/workspace/intake", payload, 201)["id"]

        def signal_for(items, district, symptom_word):
            found = [i for i in items if i["district"] == district and symptom_word in i["title"]]
            return found[0] if found else None

        try:
            call("/api/workspace/seed", {})
            with sqlite3.connect(db) as raw:
                plan = raw.execute("EXPLAIN QUERY PLAN SELECT id FROM complaints WHERE COALESCE(received_at, ingested_at) >= ?", (at(75),)).fetchall()
                assert any("idx_complaints_event_time" in str(row) for row in plan), plan
            seeded_total = call("/api/stats")["total_complaints"]
            assert seeded_total == 50, f"Expected 50 seeded fixtures, found {seeded_total}"
            print(f"PASS 1: seed leaves the 50 default synthetic fixtures in place ({seeded_total})")

            # 2. detection contract on the seeded Алмалинский outage group, already linked to INC-204
            first = radar()
            assert set(first) == {"items", "min_cases", "window_minutes", "data_origin"}, set(first)
            assert (first["min_cases"], first["window_minutes"], first["data_origin"]) == (5, 15, "synthetic")
            almaly = signal_for(first["items"], "Алмалинский", "отключение")
            assert almaly, "The seeded Алмалинский outage group must be detected"
            assert set(almaly) == SIGNAL_FIELDS, set(almaly) ^ SIGNAL_FIELDS
            assert almaly["count"] == 5 and almaly["similar_count"] == 5, almaly["count"]
            assert almaly["case_ids"] == sorted(almaly["case_ids"]) and len(almaly["case_ids"]) == 5
            assert all(m["id"] and m["text"] for m in almaly["members"]) and len(almaly["members"]) == 5
            assert almaly["incident_id"] == "INC-204", "Exactly one linked active incident must be exposed"
            assert almaly["unlinked_count"] == 0 and almaly["ignored"] is False
            assert "Правило" in almaly["reason"] and "подтверждена оператором" in almaly["reason"]
            assert not [m for m in almaly["case_ids"] if m.startswith("syn-")], "Old fixtures are outside the window"
            print("PASS 2: GET /api/workspace/radar detects the seeded outage signal, links INC-204, no duplicate")

            # 3. baseline honesty: 12 historical cases spanning only 22 minutes are a partial sample
            assert almaly["baseline_count"] is None and almaly["growth"] is None
            assert "частичная выборка" in almaly["reason"], almaly["reason"]
            print("PASS 3: partial 60-minute history yields baseline_count=null and growth=null, never a level")

            # 4. false positives: one sender repeating one text, unknown category, leak kept apart
            for _ in range(6):
                intake("Купите рекламу! Быстрый заработок: promo.example", district="Алмалинский",
                       sender_key="spam-bot-1")
            spam = radar()
            assert len(spam["items"]) == len(first["items"]), (len(spam["items"]), len(first["items"]))
            assert "promo" not in json.dumps(spam["items"], ensure_ascii=False).lower()
            assert signal_for(spam["items"], "Алмалинский", "утечка") is None, "A one-case leak is not a signal"
            assert all(item["count"] >= spam["min_cases"] for item in spam["items"])
            print("PASS 4: spam dedup, unclassified text and the single leak case raise no signal")

            # 4b. an unconfirmed ambiguous category (0.63, water or sewer) is not a signal; a confirmed one is
            ambiguous_ids = [intake("Ауэзовский район: течёт вода из трубы или люка, не понимаю откуда",
                                    district="Ауэзовский", address=f"Ауэзов {n}", sender_key=f"auezov-{n}")
                             for n in range(1, 6)]
            assert not [i for i in radar()["items"] if i["district"] == "Ауэзовский"]
            for cid in ambiguous_ids:
                call(f"/api/complaints/{cid}/confirm",
                     {"topic": "water_supply", "service_id": "srv_vodokanal", "priority": "normal", "actor": "check"})
            human = [i for i in radar()["items"] if i["district"] == "Ауэзовский"]
            assert len(human) == 1 and human[0]["count"] == 5 and "утечка" in human[0]["title"]
            print("PASS 4b: ambiguous 0.63 cases form no signal until a human confirms the category")

            # 5. demo batch: fresh ids per click, actual inserted count, outage kept in its own district
            before_demo = call("/api/stats")["total_complaints"]
            demo = call("/api/workspace/radar/demo", {})
            assert demo["count"] == 6 and len(demo["ids"]) == 6 and len(set(demo["ids"])) == 6
            assert all(cid.startswith("PULSE-RADAR-") for cid in demo["ids"])
            assert call("/api/stats")["total_complaints"] == before_demo + 6
            bostan = signal_for(radar()["items"], "Бостандыкский", "отключение")
            assert bostan and bostan["count"] == 6 and bostan["similar_count"] == 6
            assert bostan["incident_id"] is None and bostan["unlinked_count"] == 6
            assert set(bostan["case_ids"]) == set(demo["ids"]) and len(bostan["members"]) == 6
            assert bostan["baseline_count"] is None and bostan["growth"] is None
            assert "Базовая линия недоступна" in bostan["reason"]
            assert bostan["id"] != almaly["id"], "Districts stay separate"
            assert [e["event_type"] for e in call(f"/api/complaints/{demo['ids'][0]}")["events"]] == ["intake"]
            print("PASS 5: POST /radar/demo inserted 6 audited cases; Бостандыкский is a separate signal")

            # 6. a second click adds a fresh batch; the repeated sender/text does not inflate the count
            again = call("/api/workspace/radar/demo", {})
            assert again["count"] == 6 and not set(again["ids"]) & set(demo["ids"])
            assert call("/api/stats")["total_complaints"] == before_demo + 12
            batch = signal_for(radar()["items"], "Бостандыкский", "отключение")
            assert batch["id"] == bostan["id"] and batch["count"] == 6 and batch["similar_count"] == 12
            print("PASS 6: a second demo click returns 6 new ids and keeps count=6 (similar_count=12)")

            # 7. sufficient history: 5 fresh + 3 old Медеуский cases spanning 30 minutes get a real baseline
            medeu = [intake(OUTAGE_TEXT.format(n=index), address=f"Дом {index}", sender_key=f"medeu-{index}")
                     for index in range(1, 9)]
            with sqlite3.connect(db) as conn:
                for cid, minutes in zip(medeu[:3], (70, 55, 40)):
                    conn.execute("UPDATE complaints SET received_at = ?, ingested_at = ? WHERE id = ?",
                                 (at(minutes), at(minutes), cid))
            medeu_signal = signal_for(radar()["items"], "Медеуский", "отключение")
            assert medeu_signal["count"] == 5 and medeu_signal["similar_count"] == 5
            assert medeu_signal["baseline_count"] == 0.75 and medeu_signal["growth"] == 6.67
            assert "В истории 3 обращений за предыдущие 60 мин" in medeu_signal["reason"] and medeu_signal["case_ids"] == sorted(medeu[3:])
            print("PASS 7: 3 cases in the previous 60 min normalize to 0.75 per 15 min; rate ratio=6.67")

            # 8. stale preview, unknown signal and a mismatched incident are refused
            call(f"/api/workspace/radar/{bostan['id']}/confirm",
                 {"case_ids": bostan["case_ids"][:-1]}, 409)
            call("/api/workspace/radar/rad-0000000000/confirm", {"case_ids": bostan["case_ids"]}, 404)
            call(f"/api/workspace/radar/{almaly['id']}/confirm",
                 {"case_ids": bostan["case_ids"]}, 409)
            call(f"/api/workspace/radar/{bostan['id']}/confirm",
                 {"case_ids": bostan["case_ids"], "incident_id": "INC-204"}, 409)
            assert len(incidents()) == 1, "A refused confirm must not create anything"
            print("PASS 8: stale preview 409, unknown signal 404, mismatched incident 409, nothing created")

            # 9. human confirmation creates one incident, keeps the originals untouched and audits every link
            confirmed = call(f"/api/workspace/radar/{bostan['id']}/confirm", {"case_ids": bostan["case_ids"]})
            created = confirmed["incident"]
            assert confirmed["replayed"] is False
            assert created["status"] == "Проверяется" and created["category"] == "water_supply"
            assert created["title"].startswith("Возможное отключение воды — Бостандыкский")
            assert created["revision"] == 1 and created["count"] == 6
            assert created["id"].startswith("INC-") and created["id"] != "INC-204"
            assert created["service_id"] == "srv_vodokanal" and created["streets"] >= 3
            assert created["overdue_minutes"] is None
            assert created["incident_owner_id"] is None and created["created_at"] and created["first_signal_at"]
            assert created["last_related_case_at"] and created["last_update_at"] and created["next_update"] is None
            assert [i["type"] for i in created["timeline"]].count("incident_linked") == 6
            assert "incident_created" in [i["type"] for i in created["timeline"]]
            linked = call(f"/api/complaints/{bostan['case_ids'][0]}")
            assert linked["complaint"]["incident_id"] == created["id"]
            assert linked["complaint"]["decision_status"] == "pending", "Original decision stays untouched"
            assert linked["complaint"]["priority"] is None and linked["complaint"]["topic"] is None
            assert [e["event_type"] for e in linked["events"]] == ["intake", "incident_linked"]
            with sqlite3.connect(db) as conn:
                self_refs = conn.execute("SELECT COUNT(*) FROM complaints WHERE incident_id = ? AND related_to = id",
                                         (created["id"],)).fetchone()[0]
                dangling = conn.execute(
                    "SELECT COUNT(*) FROM complaints WHERE incident_id = ? AND related_to IS NOT NULL "
                    "AND related_to NOT IN (SELECT id FROM complaints WHERE incident_id = ?)",
                    (created["id"], created["id"])).fetchone()[0]
            assert self_refs == 0 and dangling == 0, (self_refs, dangling)
            assert call(f"/api/complaints/{bostan['case_ids'][0]}")["complaint"]["related_to"] is None
            assert call(f"/api/complaints/{bostan['case_ids'][1]}")["complaint"]["related_to"] == bostan["case_ids"][0]
            watched = signal_for(radar()["items"], "Бостандыкский", "отключение")
            assert watched["incident_id"] == created["id"] and watched["unlinked_count"] == 0
            print(f"PASS 9: confirm created {created['id']} (Проверяется, 6 cases) without touching originals")

            # 10. replay of the same confirmed signal is idempotent
            with sqlite3.connect(db) as conn:
                before_events = conn.execute("SELECT COUNT(*) FROM incident_events WHERE incident_id = ?",
                                             (created["id"],)).fetchone()[0]
            replay = call(f"/api/workspace/radar/{bostan['id']}/confirm", {"case_ids": bostan["case_ids"]})
            assert replay["replayed"] is True and replay["incident"]["id"] == created["id"]
            with sqlite3.connect(db) as conn:
                assert conn.execute("SELECT COUNT(*) FROM incident_events WHERE incident_id = ?",
                                    (created["id"],)).fetchone()[0] == before_events
            assert len(incidents()) == 2, incidents().keys()
            print("PASS 10: replay returns the same incident, adds no event and creates no duplicate")

            # 11. new members of a known signal join the existing incident instead of opening a second one
            newcomers = [intake(f"Бостандыкский район, дом {index}: нет воды с утра, весь дом без воды.",
                                district="Бостандыкский", address=f"Новый дом {index}",
                                sender_key=f"bostan-new-{index}") for index in range(1, 7)]
            grown = signal_for(radar()["items"], "Бостандыкский", "отключение")
            assert grown["id"] != bostan["id"] and grown["count"] == 12 and grown["unlinked_count"] == 6
            assert grown["incident_id"] == created["id"], grown["incident_id"]
            joined = call(f"/api/workspace/radar/{grown['id']}/confirm", {"case_ids": grown["case_ids"]})
            assert joined["replayed"] is False and joined["incident"]["id"] == created["id"]
            assert joined["incident"]["count"] == 12 and len(incidents()) == 2
            assert all(call(f"/api/complaints/{cid}")["complaint"]["incident_id"] == created["id"]
                       for cid in newcomers)
            print("PASS 11: six new members joined the existing incident; no duplicate incident was created")

            # 12. two active incidents for one group must not silently merge
            fake = {}
            with sqlite3.connect(db) as conn:
                for name in ("INC-TESTA", "INC-TESTB"):
                    conn.execute(
                        "INSERT INTO incidents (id, title, category, region_id, district, service_id, status, "
                        "started_at, next_update, severity, data_origin, created_at, first_signal_at, last_update_at, "
                        "revision, source_signal) VALUES (?, ?, 'water_supply', 'KZ-ALA', 'Медеуский', "
                        "'srv_vodokanal', 'Проверяется', ?, NULL, 2, 'synthetic', ?, ?, ?, 1, NULL)",
                        (name, f"Тестовый инцидент {name}", at(30), at(30), at(30), at(30)))
                for cid, name in [(c, n) for c, n in zip(medeu[3:], ("INC-TESTA",) * 3 + ("INC-TESTB",) * 2)]:
                    conn.execute("UPDATE complaints SET incident_id = ?, related_to = ? WHERE id = ?",
                                 (name, medeu[3], cid))
            ambiguous = signal_for(radar()["items"], "Медеуский", "отключение")
            assert ambiguous["incident_id"] is None and ambiguous["unlinked_count"] == 0
            assert "несколькими активными инцидентами" in ambiguous["reason"], ambiguous["reason"]
            call(f"/api/workspace/radar/{ambiguous['id']}/confirm", {"case_ids": ambiguous["case_ids"]}, 409)
            print("PASS 12: two active incidents on one group are reported and refused, never merged silently")

            # 13. dismiss and restore one signal; complaints survive and the decision is audited
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE complaints SET incident_id = NULL, related_to = NULL WHERE id IN (%s)"
                             % ",".join("?" * 5), medeu[3:])
                conn.execute("DELETE FROM incidents WHERE id IN ('INC-TESTA', 'INC-TESTB')")
            clean = signal_for(radar()["items"], "Медеуский", "отключение")
            assert clean["id"] == ambiguous["id"] and clean["unlinked_count"] == 5
            dismissed = call(f"/api/workspace/radar/{clean['id']}/ignore",
                             {"case_ids": clean["case_ids"], "ignored": True})
            assert dismissed["ignored"] is True
            shown = signal_for(radar()["items"], "Медеуский", "отключение")
            assert shown["ignored"] is True and shown["id"] == clean["id"]
            kept = call(f"/api/complaints/{clean['case_ids'][0]}")
            assert kept["complaint"]["id"] == clean["case_ids"][0], "Dismissal never deletes a complaint"
            assert [e["event_type"] for e in kept["events"]][-1] == "radar_ignored"
            call(f"/api/workspace/radar/{clean['id']}/ignore", {"case_ids": clean["case_ids"], "ignored": False})
            assert signal_for(radar()["items"], "Медеуский", "отключение")["ignored"] is False
            print("PASS 13: dismiss and restore persist, audit the human action and keep every complaint")

            # 14. a new member produces a fresh signal id, so a dismissal is not permanent silence
            call(f"/api/workspace/radar/{clean['id']}/ignore", {"case_ids": clean["case_ids"], "ignored": True})
            intake(OUTAGE_TEXT.format(n=99), address="Дом 99", sender_key="medeu-99")
            refreshed = signal_for(radar()["items"], "Медеуский", "отключение")
            assert refreshed["id"] != clean["id"] and refreshed["ignored"] is False
            assert refreshed["count"] == 6 and clean["id"] not in [i["id"] for i in radar()["items"]]
            print("PASS 14: a new member hides nothing: the group gets a fresh, visible signal id")

            # 15. incident update: validation, revision conflict, owner, overdue time, no cascade on close
            iid = created["id"]
            detail = call(f"/api/workspace/incidents/{iid}")
            for field in ("incident_owner_id", "created_at", "first_signal_at", "last_related_case_at",
                          "last_update_at", "next_update", "revision", "count", "streets", "service_name",
                          "members", "timeline", "overdue_minutes"):
                assert field in detail, field
            base = {"expected_revision": detail["revision"], "status": "Передано службе",
                    "incident_owner_id": "op-timur", "severity": 3, "next_update": at(-45), "note": "Передано службе."}
            call(f"/api/workspace/incidents/{iid}/update", {**base, "status": "Неизвестный"}, 422)
            call(f"/api/workspace/incidents/{iid}/update", {**base, "incident_owner_id": "op-nobody"}, 422)
            call(f"/api/workspace/incidents/{iid}/update", {**base, "next_update": None}, 422)
            call(f"/api/workspace/incidents/{iid}/update",
                 {**base, "status": "Завершён", "next_update": at(-45)}, 422)
            call(f"/api/workspace/incidents/{iid}/update", {**base, "next_update": "2026-09-25T09:00"}, 422)
            call(f"/api/workspace/incidents/{iid}/update", {**base, "note": "   "}, 422)
            call(f"/api/workspace/incidents/{iid}/update", {k: v for k, v in base.items() if k != "note"}, 422)
            updated = call(f"/api/workspace/incidents/{iid}/update", base)["incident"]
            assert updated["status"] == "Передано службе" and updated["revision"] == detail["revision"] + 1
            assert updated["incident_owner_id"] == "op-timur" and updated["severity"] == 3
            assert updated["last_update_at"] >= detail["last_update_at"]
            assert updated["overdue_minutes"] == 0, "A future next_update is not overdue"
            assert updated["timeline"][-1]["type"] == "incident_updated"
            call(f"/api/workspace/incidents/{iid}/update", {**base, "expected_revision": detail["revision"]}, 409)
            print("PASS 15: invalid update 422, stale revision 409, owner/severity/next_update persisted")

            # 16. closing an incident never closes its complaints; a human update may reopen it
            closed = call(f"/api/workspace/incidents/{iid}/update",
                          {"expected_revision": updated["revision"], "status": "Завершён",
                           "incident_owner_id": "op-timur", "severity": 3, "next_update": None,
                           "note": "Работы завершены, вода подана."})["incident"]
            assert closed["status"] == "Завершён" and closed["revision"] == updated["revision"] + 1
            assert closed["overdue_minutes"] is None and closed["next_update"] is None
            member = call(f"/api/complaints/{bostan['case_ids'][0]}")["complaint"]
            assert member["incident_id"] == iid and member["resolved_at"] is None
            assert member["decision_status"] == "pending", "Closing an incident must not close complaints"
            reopened = call(f"/api/workspace/incidents/{iid}/update",
                            {"expected_revision": closed["revision"], "status": "Работы ведутся",
                             "incident_owner_id": "op-timur", "severity": 3, "next_update": at(30),
                             "note": "Поступило повторное обращение, работы возобновлены."})["incident"]
            assert reopened["status"] == "Работы ведутся" and reopened["revision"] == closed["revision"] + 1
            assert 25 <= reopened["overdue_minutes"] <= 35, reopened["overdue_minutes"]
            print("PASS 16: closure does not cascade to complaints; a human update reopens the incident")

            # 17. the seeded incident is preserved and honestly labelled
            seeded = call("/api/workspace/incidents/INC-204")
            assert seeded["status"] == "Работы ведутся" and seeded["count"] == 17 and seeded["revision"] == 1
            assert seeded["timeline"][0]["type"] == "seeded_initial_state"
            assert "не моделировались" in seeded["timeline"][0]["text"]
            assert seeded["incident_owner_id"] is None and seeded["district"] == "Алмалинский"
            print("PASS 17: INC-204 is untouched and its timeline says the initial state came from seed data")

            # 18. restart persistence for links, dismissals and updates
            before_restart = call("/api/stats")["total_complaints"]
            assert before_restart == seeded_total + 38, before_restart
            stop_server(proc)
            proc, url = start_server(ROOT, db, find_free_port())
            after = radar()
            assert signal_for(after["items"], "Бостандыкский", "отключение")["incident_id"] == iid
            assert signal_for(after["items"], "Алмалинский", "отключение")["incident_id"] == "INC-204"
            assert call(f"/api/workspace/incidents/{iid}")["revision"] == reopened["revision"]
            assert call(f"/api/workspace/incidents/{iid}")["status"] == "Работы ведутся"
            assert call("/api/stats")["total_complaints"] == before_restart
            print("PASS 18: links, dismissal, owner, revision and counts survive a server restart")

            print("\n========================================================")
            print("ALL 18 RADAR / INCIDENT CHECKS PASSED SUCCESSFULLY!")
            print("========================================================")
        finally:
            print("[*] Terminating test server process...")
            stop_server(proc)
            print("[*] Temporary test resources cleaned up.")


if __name__ == "__main__":
    run()

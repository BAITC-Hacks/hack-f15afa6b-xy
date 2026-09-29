"""Regression checks on generated fixtures in an isolated database, never real citizen data."""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clarification import start_server, stop_server
from smoke import find_free_port, http_request


def run():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "radar.db"
        proc, url = start_server(ROOT, db, find_free_port())

        def call(path, body=None, expected=200):
            code, data = http_request(url + path, "POST" if body is not None else "GET", body)
            assert code == expected, (path, code, data)
            return data

        def now(minutes=0):
            return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()

        def add(prefix, origin="citizen", count=5, minutes=-2, **fields):
            ids = []
            with sqlite3.connect(db) as conn:
                for index in range(count):
                    cid = f"TEST-{prefix}-{index}"
                    row = dict(id=cid, data_origin=origin, source_system="generated_test_fixture",
                               text=f"Тестовый район: нет воды в доме {prefix} {index}, весь дом без воды.",
                               region_id="KZ-ALA", received_at=now(minutes), ingested_at=now(minutes),
                               language="ru", district="Тестовый", address=f"Тестовая {index}",
                               topic="water_supply", decision_status="confirmed", sender_key=cid)
                    row.update(fields)
                    conn.execute(f"INSERT INTO complaints ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                                 list(row.values()))
                    ids.append(cid)
            return ids

        def radar(source="real"):
            return call("/api/workspace/radar?source=" + source)

        def signal(sid=None, source="real"):
            items = radar(source)["items"]
            return next(i for i in items if i["id"] == sid) if sid else items[0]

        def action(item, name, **extra):
            return call(f"/api/workspace/radar/{item['id']}/action",
                        {"expected_revision": item["revision"], "action": name, **extra})["signal"]

        def decision(item, name, **extra):
            return call(f"/api/workspace/radar/{item['id']}/{name}",
                        {"expected_revision": item["revision"], "case_ids": item["case_ids"], **extra})

        try:
            assert call("/api/workspace/radar")["data_origin"] == "real"
            assert not radar()["items"]
            real_ids = add("citizen")
            add("demo", "synthetic")
            add("organizer", "organizer")
            add("public", "public")
            assert {i["data_origin"] for i in radar()["items"]} == {"citizen", "organizer", "public"}
            assert len(radar()["items"]) == 3
            assert {i["data_origin"] for i in radar("synthetic")["items"]} == {"synthetic"}
            current = next(i for i in radar()["items"] if i["data_origin"] == "citizen")
            sid = current["id"]
            assert current["count"] == 5
            print("PASS 1: real by default; citizen, organizer, public and synthetic groups never mix")

            add("unknown-place", count=1, district=None)
            add("unknown-topic", count=1, text="непонятно", topic=None, decision_status="pending")
            add("future", count=5, minutes=60)
            add("date-only", "organizer", received_at=datetime.now(timezone.utc).date().isoformat())
            add("missing-time", "organizer", received_at=None)
            add("quarantined", count=5, quarantined=1)
            assert signal(sid)["count"] == 5
            coverage = radar()["coverage"]
            assert coverage == {"recent_count": 17, "eligible_count": 15, "excluded_count": 2}, coverage
            call("/api/workspace/radar?source=garbage", expected=422)
            print("PASS 2: unknown place/category/time, future and quarantine excluded; source validated")

            claimed = action(current, "claim", minutes=15, note="Проверяем у службы")
            assert claimed["id"] == sid and claimed["owner_id"] == "operator_demo"
            assert claimed["owner_name"] == "Демо-оператор" and claimed["check_at"]
            assert claimed["status"] == "reviewing" and not claimed["overdue"]
            call(f"/api/workspace/radar/{sid}/action",
                 {"expected_revision": current["revision"], "action": "claim"}, 409)
            call(f"/api/workspace/radar/{sid}/action",
                 {"expected_revision": claimed["revision"], "action": "schedule", "minutes": 0}, 422)
            print("PASS 3: authenticated actor owns signal; stale revision and invalid deadline refused")

            add("newcomer", count=1)
            grown = signal(sid)
            assert grown["count"] == 6 and grown["owner_id"] == claimed["owner_id"]
            assert grown["check_at"] == claimed["check_at"] and grown["revision"] > claimed["revision"]
            call(f"/api/workspace/radar/{sid}/confirm",
                 {"expected_revision": claimed["revision"], "case_ids": claimed["case_ids"]}, 409)
            print("PASS 4: incoming complaint keeps ID, owner and deadline; stale confirmation refused")

            paused = action(grown, "snooze", minutes=30)
            assert paused["snoozed"] and paused["status"] == "snoozed"
            call(f"/api/workspace/radar/{sid}/confirm",
                 {"expected_revision": paused["revision"], "case_ids": paused["case_ids"]}, 409)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE radar_signals SET check_at = ?, snoozed_until = ? WHERE id = ?",
                             (now(-1), now(-1), sid))
            due = signal(sid)
            assert due["overdue"] and not due["snoozed"]
            resumed = action(due, "resume", minutes=60)
            assert not resumed["overdue"] and not resumed["snoozed"] and resumed["status"] == "reviewing"
            print("PASS 5: snooze blocks confirmation, expiry returns attention, resume sets a new deadline")

            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE radar_signals SET owner_id = 'another-operator' WHERE id = ?", (sid,))
            call(f"/api/workspace/radar/{sid}/action",
                 {"expected_revision": resumed["revision"], "action": "claim"}, 409)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE radar_signals SET owner_id = 'operator_demo' WHERE id = ?", (sid,))
            print("PASS 6: another operator cannot silently take over a claimed signal")

            current = signal(sid)
            decision(current, "ignore", ignored=True)
            add("after-dismissal", count=1)
            dismissed = signal(sid)
            assert dismissed["ignored"] and dismissed["count"] == 7
            decision(dismissed, "ignore", ignored=False)
            restored = signal(sid)
            assert not restored["ignored"] and restored["owner_id"] == "operator_demo"
            print("PASS 7: dismissal survives membership growth; explicit restore preserves ownership")

            created = decision(restored, "confirm")["incident"]
            assert created["data_origin"] == "citizen" and created["incident_owner_id"] == "operator_demo"
            assert created["next_update"] == restored["check_at"] and created["count"] == 7
            assert signal(sid)["status"] == "confirmed"
            assert decision(signal(sid), "confirm")["replayed"]
            add("after-confirmation", count=1)
            reopened = signal(sid)
            assert reopened["status"] == "reviewing" and reopened["unlinked_count"] == 1
            assert decision(reopened, "confirm")["incident"]["id"] == created["id"]
            print("PASS 8: human confirmation carries real origin, owner, deadline; new case reuses incident")

            demo = signal(source="synthetic")
            call(f"/api/workspace/radar/{demo['id']}/confirm",
                 {"expected_revision": demo["revision"], "case_ids": demo["case_ids"],
                  "incident_id": created["id"]}, 409)
            print("PASS 9: synthetic group cannot link to a citizen incident")

            pending = action(demo, "claim")
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE complaints SET received_at = ?, ingested_at = ? WHERE id LIKE 'TEST-demo-%'",
                             (now(-120), now(-120)))
            saved = signal(demo["id"], "synthetic")
            assert saved["owner_id"] == pending["owner_id"] and not saved["in_current_window"]
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE complaints SET topic = 'electricity' WHERE id = ?", (demo["case_ids"][0],))
            call(f"/api/workspace/radar/{saved['id']}/confirm",
                 {"expected_revision": saved["revision"], "case_ids": saved["case_ids"]}, 409)
            print("PASS 10: pending work survives time window; changed archived cases cannot be confirmed")

            terminal = next(i for i in radar()["items"] if i["data_origin"] == "public")
            decision(terminal, "ignore", ignored=True)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE radar_signals SET last_at = ? WHERE id = ?", (now(-150), terminal["id"]))
                conn.execute("UPDATE complaints SET received_at = ?, ingested_at = ? WHERE data_origin = 'public'",
                             (now(-150), now(-150)))
            add("later-public", "public")
            later = next(i for i in radar()["items"] if i["data_origin"] == "public" and not i["ignored"])
            assert later["id"] != terminal["id"]
            print("PASS 11: a later episode after a quiet hour is not permanently silenced")

            count = len(radar()["items"])
            stop_server(proc)
            proc, url = start_server(ROOT, db, find_free_port())
            assert len(radar()["items"]) == count
            persistent = signal(demo["id"], "synthetic")
            assert persistent["owner_id"] == pending["owner_id"]
            assert persistent["check_at"] == pending["check_at"]
            assert any(e["action"] == "claim" for e in persistent["history"])
            with sqlite3.connect(db) as conn:
                assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                assert conn.execute("SELECT COUNT(*) FROM complaints WHERE id IN (%s)" %
                                    ",".join("?" for _ in real_ids), real_ids).fetchone()[0] == 5
            print("PASS 12: restart preserves IDs, ownership, deadlines and audit; original cases intact")
            print("ALL 12 RADAR WORKFLOW CHECKS PASSED")
        finally:
            stop_server(proc)


if __name__ == "__main__":
    run()

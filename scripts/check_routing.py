"""Bounded routing + presence checks: direct route() calls on a temp DB, then real HTTP.

Covers workload-weighted ranking inside one routing stage, projected-overload and
offline rejection, language/region fallback, the explicit dispatch queue when every
operator is full, the 1600-combo routing health snapshot, an invalid region filter,
operator presence (peers, editing, leave, TTL), revision semantics (heartbeats and
proposals ignored, saves counted) and restart persistence. Run:

    .venv/bin/python scripts/check_routing.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clarification import start_server, stop_server  # noqa: E402
from smoke import find_free_port, http_request  # noqa: E402


def direct_checks(tmp_dir: Path):
    os.environ["DATABASE_PATH"] = str(Path(tmp_dir) / "routing-direct.db")
    import app as app_module
    from demo_data import init_workspace, seed_workspace
    from support_api import init_support
    from triage import (DEMO_WORKLOAD_WEIGHTS as W, REGION_NAMES, operators_with_load,
                        responsible_service_name, route)

    app_module.init_db()
    services = dict(app_module.TOPIC_SERVICE_MAP)
    with app_module.get_connection() as conn:
        init_workspace(conn)
        seed_workspace(conn, services)
        init_support(conn)
        init_support(conn)  # idempotent: one operator row, one presence table

    def snapshot():
        with app_module.get_connection() as conn:
            return {o["id"]: o for o in operators_with_load(conn)}

    def routed(c, category):
        return route(c, {"category": category, "suggested_service": services[category]}, list(snapshot().values()))

    assert len(REGION_NAMES) == 20
    for region_id in REGION_NAMES:
        for service_id in services.values():
            name = responsible_service_name(region_id, service_id)
            assert name and "Региональная очередь" not in name
            if region_id != "KZ-ALA":
                assert "подтверждает оператор" in name

    ops = snapshot()
    assert len(ops) == 7, sorted(ops)
    assert ops["op-aidana"]["current_load"] == 2 and ops["op-aidana"]["capacity"] == 5
    assert ops["op-overloaded"]["current_load"] == 7 and ops["op-overloaded"]["capacity"] == 6
    assert ops["op-overloaded"]["workload"] == 7 * W["baseline"]
    assert ops["op-overloaded"]["workload"] > ops["op-overloaded"]["workload_capacity"]
    result = routed({"region_id": "KZ-ALA", "language": "ru", "district": "Алмалинский", "priority": "normal"},
                    "water_supply")
    assert result["operator"]["id"] == "op-aidana" and result["level"] == 1
    candidates = {c["id"]: c for c in result["candidates"]}
    assert len(candidates) == 7
    assert candidates["op-overloaded"]["eligible"] is False
    assert candidates["op-overloaded"]["reason"] == "Слоты заняты: 7/6"
    assert len(candidates["op-overloaded"]["match_reasons"]) == 3, candidates["op-overloaded"]["match_reasons"]
    assert candidates["op-away"]["eligible"] is False and candidates["op-away"]["reason"] == "Статус: away"
    assert candidates["op-aidana"]["reason"].startswith("Выбран:")
    print("PASS 1: 7 operators, Aidana 2/5 intact, overloaded/away shown as rejected candidates")

    with app_module.get_connection() as conn:
        conn.execute("UPDATE operators SET base_load = 0 WHERE id = 'op-aidana'")
        conn.execute("UPDATE operators SET base_load = 3 WHERE id = 'op-timur'")
        conn.execute("""INSERT INTO complaints
            (id, data_origin, text, region_id, ingested_at, language, topic, service_id, priority,
             decision_status, assigned_operator, channel, quarantined)
            VALUES ('SYN-phone-1', 'synthetic', 'демо звонок', 'KZ-ALA', ?, 'ru', 'water_supply',
                    'srv_vodokanal', 'normal', 'confirmed', 'op-aidana', 'phone', 0)""",
            (datetime.now(timezone.utc).isoformat(),))
    ops = snapshot()
    assert ops["op-aidana"]["current_load"] == 1 and ops["op-aidana"]["workload"] == W["phone"]
    assert ops["op-aidana"]["workload_ratio"] > ops["op-timur"]["workload_ratio"]
    result = routed({"region_id": "KZ-ALA", "language": "ru", "district": None, "priority": "normal"}, "water_supply")
    assert result["operator"]["id"] == "op-timur" and result["level"] == 2, result["operator"]["id"]
    print("PASS 2: same stage ranks by projected workload ratio, not slot count")

    with app_module.get_connection() as conn:
        conn.execute("UPDATE operators SET base_load = 0 WHERE id = 'op-timur'")
        for i in range(2):
            conn.execute("""INSERT INTO complaints
                (id, data_origin, text, region_id, ingested_at, language, topic, service_id, priority,
                 decision_status, assigned_operator, channel, quarantined)
                VALUES (?, 'synthetic', 'демо звонок', 'KZ-ALA', ?, 'ru', 'water_supply',
                        'srv_vodokanal', 'normal', 'confirmed', 'op-timur', 'phone', 0)""",
                (f"SYN-phone-{i + 2}", datetime.now(timezone.utc).isoformat()))
    ops = snapshot()
    assert ops["op-timur"]["current_load"] == 2 < ops["op-timur"]["capacity"]
    assert ops["op-timur"]["workload"] >= ops["op-timur"]["workload_capacity"]
    result = routed({"region_id": "KZ-ALA", "language": "ru", "district": None, "priority": "normal"}, "water_supply")
    timur = {c["id"]: c for c in result["candidates"]}["op-timur"]
    projected = timur["workload"] + result["incoming_weight"]
    assert timur["eligible"] is False and timur["reason"] == f"Нагрузка: {projected}/{timur['workload_capacity']}", timur
    assert result["operator"]["id"] == "op-aidana"
    print("PASS 3: free slots but projected workload (assigned + incoming) over capacity is rejected")

    assert routed({"region_id": "KZ-ALA", "language": "mixed", "district": None, "priority": "normal"},
                  "roads")["level"] == 3
    fallback = routed({"region_id": "KZ-PAV", "language": "ru", "district": None, "priority": "normal"}, "water_supply")
    assert fallback["level"] == 4 and fallback["route_type"] == "fallback"
    assert fallback["operator"]["id"] == "op-senior"
    assert routed({"region_id": "KZ-ALA", "language": "ru", "district": "Алмалинский", "priority": "normal"},
                  "water_supply")["level"] == 1
    print("PASS 4: district > language > department > general-queue fallback preserved")

    # The cost of the case being routed comes from the effective topic/priority/channel.
    water = {"region_id": "KZ-ALA", "language": "ru", "district": None, "priority": "normal"}
    assert routed(water, "roads")["incoming_weight"] == W["normal"]
    assert routed({**water, "priority": "urgent"}, "roads")["incoming_weight"] == W["urgent_complex"]
    assert routed(water, "water_supply")["incoming_weight"] == W["urgent_complex"], "complex topic"
    assert routed({**water, "channel": "phone"}, "roads")["incoming_weight"] == W["phone"]
    assert routed({**water, "channel": "telegram"}, "roads")["incoming_weight"] == W["telegram_whatsapp"]
    confirmed_wins = route(water, {"category": "roads", "suggested_service": services["roads"],
                                   "urgency": "urgent"}, list(snapshot().values()))
    assert confirmed_wins["incoming_weight"] == W["normal"], "confirmed priority beats the demo proposal"
    undecided = {key: value for key, value in water.items() if key != "priority"}
    selected = route(undecided, {"category": "roads", "suggested_service": services["roads"],
                                 "priority": "urgent"}, list(snapshot().values()))
    assert selected["incoming_weight"] == W["urgent_complex"], "operator-selected priority is costed too"
    with app_module.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO operators VALUES ('op-junior', 'Демо Ж.', ?, ?, 'general', NULL, "
                     "'online', 0, 6)", (json.dumps(["roads"]), json.dumps(["ru"])))
    web = routed({"region_id": "KZ-PAV", "language": "ru", "district": None, "priority": "normal"}, "roads")
    assert web["operator"]["id"] == "op-junior", web["operator"]["id"]
    phone = routed({"region_id": "KZ-PAV", "language": "ru", "district": None, "priority": "normal",
                    "channel": "phone"}, "roads")
    assert phone["operator"]["id"] == "op-senior", phone["operator"]["id"]
    with app_module.get_connection() as conn:
        conn.execute("DELETE FROM operators WHERE id = 'op-junior'")
    print("PASS 5: incoming case cost follows effective topic/priority/channel and decides the ranking")

    with app_module.get_connection() as conn:
        conn.execute("UPDATE operators SET base_load = capacity")
    queue = routed({"region_id": "KZ-ALA", "language": "ru", "district": "Алмалинский", "priority": "urgent"},
                   "water_supply")
    assert queue["operator"] is None and queue["level"] == 4
    assert queue["queue"] is True and queue["route_type"] == "queue"
    assert "очеред" in queue["reason"] and all(not c["eligible"] for c in queue["candidates"])
    print("PASS 6: every operator full keeps the case in the explicit dispatch queue")


def run():
    with tempfile.TemporaryDirectory() as tmp:
        direct_checks(Path(tmp))

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "routing-http.db"
        proc, url = start_server(ROOT, db, find_free_port())

        def call(path, body=None, expected=200):
            code, data = http_request(url + path, "POST" if body is not None else "GET", body)
            assert code == expected, (path, code, data)
            return data

        def presence(cid, body, expected=200):
            return call(f"/api/workspace/complaints/{cid}/presence", body, expected)

        try:
            call("/api/workspace/seed", {})
            health = call("/api/workspace/routing-health")
            summary = health["summary"]
            # Demo departments: vodokanal (water_supply), energo (electricity), roads (roads).
            primary_topics = {"water_supply", "electricity", "roads"}
            assert summary["total"] == 10 * 20 * 4 * 2 == 1600, summary
            assert summary["uncovered"] == 0, summary
            assert summary["primary"] == len(primary_topics) * 4 * 2 == 24, summary
            assert summary["fallback"] == 1600 - 24 == 1576, summary
            assert summary["coverage_percent"] == 100.0 and summary["primary_percent"] == 1.5
            assert summary["data_origin"] == "synthetic" and summary["weights"]["phone"] == 100
            assert health["region_id"] is None and health["item_count"] > 40 and len(health["items"]) == 40
            assert all(i["route_type"] != "primary" and i["operator_name"] for i in health["items"])
            kz = call("/api/workspace/routing-health?region_id=KZ-ALA")
            assert kz["summary"]["total"] == 1600, "counts stay global when a region filters the examples"
            assert kz["item_count"] == (10 - len(primary_topics)) * 4 * 2 == 56 and len(kz["items"]) == 40
            assert all(i["region_id"] == "KZ-ALA" for i in kz["items"])
            call("/api/workspace/routing-health?region_id=KZ-NOPE", expected=422)
            print("PASS 7: 1600 combos, 0 uncovered, region filter only trims examples; bad filter 422")

            with sqlite3.connect(db) as raw:
                saved_bases = raw.execute("SELECT id, base_load FROM operators").fetchall()
                raw.execute("UPDATE operators SET base_load = capacity")
            busy = call("/api/workspace/routing-health")
            assert busy["summary"]["uncovered"] == 0 and busy["summary"]["fallback"] == 1600, busy["summary"]
            assert busy["summary"]["primary"] == 0 and busy["summary"]["coverage_percent"] == 100.0
            assert busy["item_count"] == 1600 and {i["route_type"] for i in busy["items"]} == {"queue"}
            assert all(i["operator_name"] is None and "очеред" in i["reason"] for i in busy["items"])
            with sqlite3.connect(db) as raw:
                raw.executemany("UPDATE operators SET base_load = ? WHERE id = ?",
                                [(base, op_id) for op_id, base in saved_bases])
            assert call("/api/workspace/routing-health")["summary"] == summary
            print("PASS 8: a fully busy roster counts as fallback coverage, not uncovered")

            cid = call("/api/workspace/intake", {"text": "Демо обращение по воде", "region_id": "KZ-ALA",
                                                 "language": "ru", "district": "Алмалинский"}, 201)["id"]
            s1, s2 = "sess-1111-aaaa", "sess-2222-bbbb"
            first = presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})
            assert first["peers"] == [] and first["presence_ttl_seconds"] == 20
            second = presence(cid, {"session_id": s2, "operator_id": "op-timur", "mode": "editing"})
            assert [(p["operator_id"], p["name"], p["mode"]) for p in second["peers"]] == [("op-aidana", "Айдана К.", "viewing")]
            back = presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})
            assert [p["operator_id"] for p in back["peers"]] == ["op-timur"] and back["peers"][0]["mode"] == "editing"
            audit_before = [e["id"] for e in call(f"/api/complaints/{cid}")["events"]]
            presence(cid, {"session_id": s2, "operator_id": "op-timur", "mode": "editing"})
            assert [e["id"] for e in call(f"/api/complaints/{cid}")["events"]] == audit_before, \
                "presence must not write audit events"
            with sqlite3.connect(db) as raw:
                assert raw.execute("SELECT COUNT(*) FROM case_presence WHERE complaint_id = ?", (cid,)).fetchone()[0] == 2
            presence("PULSE-missing", {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"}, 404)
            presence(cid, {"session_id": s1, "operator_id": "op-ghost", "mode": "viewing"}, 422)
            presence(cid, {"session_id": "short", "operator_id": "op-aidana", "mode": "viewing"}, 422)
            presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "watching"}, 422)
            print("PASS 9: heartbeat upserts one row per session, peers exclude caller; bad cid/operator/body rejected")

            with sqlite3.connect(db) as raw:
                raw.execute("UPDATE case_presence SET seen_at = ? WHERE complaint_id = ? AND session_id = ?",
                            ((datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(), cid, s2))
            assert presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})["peers"] == []
            with sqlite3.connect(db) as raw:
                assert raw.execute("SELECT COUNT(*) FROM case_presence WHERE complaint_id = ?", (cid,)).fetchone()[0] == 1
            presence(cid, {"session_id": s2, "operator_id": "op-timur", "mode": "editing"})
            left = call(f"/api/workspace/complaints/{cid}/presence/leave", {"session_id": s1})
            assert left["removed"] is True and [p["operator_id"] for p in left["peers"]] == ["op-timur"]
            again = call(f"/api/workspace/complaints/{cid}/presence/leave", {"session_id": s1})
            assert again["removed"] is False
            with sqlite3.connect(db) as raw:
                rows = raw.execute("SELECT session_id FROM case_presence WHERE complaint_id = ?", (cid,)).fetchall()
            assert [r[0] for r in rows] == [s2], "leave removes only the caller's session"
            print("PASS 10: stale presence pruned after TTL; leave removes only this session's row")

            revision = presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})["revision"]
            assert len(revision) == 32 and all(c in "0123456789abcdef" for c in revision)
            assert presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})["revision"] == revision
            call(f"/api/workspace/complaints/{cid}/triage", {})
            proposed = presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})
            assert proposed["revision"] == revision, "heartbeat and classification_proposed must not move revision"
            call(f"/api/workspace/complaints/{cid}/reply", {"text": "Демо-ответ гражданину"})
            saved = presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})
            assert saved["revision"] != revision and saved["last_change_at"] > proposed["last_change_at"]
            print("PASS 11: revision ignores heartbeats/proposals and moves on a saved reply")

            preview_cid = call("/api/workspace/intake", {"text": "На Абая 44 нет воды, весь дом без холодной воды",
                                                         "region_id": "KZ-ALA", "language": "ru",
                                                         "district": "Алмалинский"}, 201)["id"]
            call(f"/api/workspace/complaints/{preview_cid}/triage", {})
            plan = call(f"/api/workspace/complaints/{preview_cid}/playbooks/route_service/preview",
                        {"topic": "water_supply", "priority": "normal"})
            assert plan["can_execute"] and plan["preview_token"], plan["blocked_reason"]
            presence(preview_cid, {"session_id": s2, "operator_id": "op-timur", "mode": "editing"})
            presence(preview_cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})
            executed = call(f"/api/workspace/complaints/{preview_cid}/playbooks/route_service/execute",
                            {"preview_token": plan["preview_token"]})
            assert executed["complaint"]["decision_status"] == "confirmed"
            print("PASS 12: presence polling does not invalidate a stored playbook preview")

            before = call("/api/workspace/operators")["items"]
            health_before = call("/api/workspace/routing-health")["summary"]
            presence(cid, {"session_id": s2, "operator_id": "op-timur", "mode": "editing"})
            stop_server(proc)
            proc, url = start_server(ROOT, db, find_free_port())
            after = call("/api/workspace/operators")["items"]
            fields = ("id", "current_load", "capacity", "workload", "workload_capacity")
            assert [{k: o[k] for k in fields} for o in before] == [{k: o[k] for k in fields} for o in after]
            assert len(after) == 7 and next(o for o in after if o["id"] == "op-overloaded")["capacity"] == 6
            assert call("/api/workspace/routing-health")["summary"] == health_before
            assert [p["operator_id"] for p in
                    presence(cid, {"session_id": s1, "operator_id": "op-aidana", "mode": "viewing"})["peers"]] == ["op-timur"]
            print("PASS 13: workload, coverage summary and presence survive a restart")
        finally:
            stop_server(proc)
    print("ALL ROUTING AND PRESENCE CHECKS PASSED")


if __name__ == "__main__":
    run()

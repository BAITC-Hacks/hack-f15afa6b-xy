"""Exercise operator playbooks through real HTTP against an isolated SQLite DB.

Preview/execute lifecycle: no change on preview, explicit confirmation, stale and
cross-case tokens, atomic rollback, idempotent replay, restart persistence, the
FAQ closure guards and manual routing on an unavailable demo analysis.
"""

import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_clarification import start_server, stop_server  # noqa: E402
from smoke import find_free_port, http_request  # noqa: E402
from playbooks import FAQ_TEMPLATE, audit_id  # noqa: E402

PLAYBOOK_IDS = ["link_mass_incident", "route_service", "request_clarification", "close_faq",
                "quarantine", "restore", "unlink_incident", "reopen_faq"]


def run():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "playbooks.db"
        proc, url = start_server(ROOT, db, find_free_port())

        def call(path, body=None, expected=200):
            code, data = http_request(url + path, "POST" if body is not None else "GET", body)
            assert code == expected, (path, code, data)
            return data

        def post(path, body, expected=200):
            code, data = http_request(url + path, "POST", body)
            assert code == expected, (path, code, data)
            return data

        def intake(text, **extra):
            return call("/api/workspace/intake", {"text": text, "region_id": "KZ-ALA", "language": "ru",
                                                  "district": "Алмалинский", "channel": "web", **extra}, 201)["id"]

        def listing(cid):
            return {item["id"]: item for item in call(f"/api/workspace/complaints/{cid}/playbooks")["items"]}

        def preview(cid, pid, body=None):
            return call(f"/api/workspace/complaints/{cid}/playbooks/{pid}/preview", body or {})

        def execute(cid, pid, token, expected=200):
            return call(f"/api/workspace/complaints/{cid}/playbooks/{pid}/execute",
                        {"preview_token": token}, expected)

        def events(cid):
            return call(f"/api/complaints/{cid}")["events"]

        try:
            call("/api/workspace/seed", {})
            cid = intake("Добрый день, на Абая 44 с утра нет воды, весь дом без воды, когда включат?")
            call(f"/api/workspace/complaints/{cid}/triage", {})
            before = call(f"/api/complaints/{cid}")

            items = listing(cid)
            assert list(items) == PLAYBOOK_IDS, list(items)
            assert items["route_service"]["can_execute"] and items["link_mass_incident"]["can_execute"]
            assert items["quarantine"]["can_execute"]
            assert not items["request_clarification"]["can_execute"]
            assert "вопрос" in items["request_clarification"]["blocked_reason"].lower()
            assert not items["close_faq"]["can_execute"] and items["close_faq"]["blocked_reason"]
            assert not items["restore"]["can_execute"] and not items["reopen_faq"]["can_execute"]
            assert not listing("PULSE-2440")["close_faq"]["can_execute"], "No FAQ close on a genuine incident"
            planned = preview(cid, "route_service", {"topic": "water_supply", "priority": "normal"})
            assert planned["can_execute"] and planned["preview_token"].startswith("pb-")
            assert planned["complaint_id"] == cid and planned["delivery"] == "demo_only"
            assert len(planned["actions"]) >= 5 and all(a["label"] and a["value"] for a in planned["actions"])
            fields = {a["label"]: a["value"] for a in planned["actions"]}
            assert fields["Статус"] == "Ожидает службу" and "Приоритет" in fields
            text = " ".join(fields.values())
            assert "water_supply" not in text and "manual=true" not in text and "Водоснабжение" in text
            assert call(f"/api/complaints/{cid}") == before, "Preview must not change the complaint"
            print("PASS 1: availability list + preview are read-only and human-readable")

            token = planned["preview_token"]
            applied = execute(cid, "route_service", token)
            assert applied["replayed"] is False and applied["playbook"] == "route_service"
            complaint = applied["complaint"]
            assert complaint["decision_status"] == "confirmed" and complaint["topic"] == "water_supply"
            assert complaint["priority"] == "normal" and complaint["service_id"] == "srv_vodokanal"
            assert complaint["assigned_operator"] == "op-aidana" and complaint["incident_id"] is None
            kinds = [e["event_type"] for e in events(cid)]
            assert kinds.count("playbook_executed") == 1 and kinds.count("operator_confirmed") == 1
            confirmed = json.loads(next(e for e in events(cid) if e["event_type"] == "operator_confirmed")["payload"])
            assert confirmed["suggested_value"] == confirmed["confirmed_value"] == "water_supply"
            assert confirmed["provider"] == "mock" and confirmed["operator_override"] is False and confirmed["language"] == "ru"
            saved = next(e for e in events(cid) if e["event_type"] == "reply_saved")
            assert json.loads(saved["payload"])["delivery"] == "demo_only"
            assert json.loads(saved["payload"])["playbook"] == "route_service"
            tracked = call(f"/api/workspace/tracking/{cid}")
            assert tracked["status"] == "confirmed" and tracked["updates"][-1]["type"] == "reply_saved"
            snapshot = [e["id"] for e in events(cid)]
            replay = execute(cid, "route_service", token)
            assert replay["replayed"] is True and replay["complaint"]["topic"] == "water_supply"
            assert [e["id"] for e in events(cid)] == snapshot, "Replay must not write again"
            print("PASS 2: confirmed route writes decision, assignment, reply and audit once; replay is a no-op")

            again = preview(cid, "route_service", {"topic": "water_supply", "priority": "normal"})
            assert not again["can_execute"] and again["preview_token"] is None
            assert "подтверждено" in again["blocked_reason"]
            blocked_confirm = post(f"/api/workspace/complaints/{cid}/playbooks/route_service/execute",
                                   {"preview_token": token + "x"}, 409)
            assert "устарел" in blocked_confirm["detail"] or "не найден" in blocked_confirm["detail"]
            print("PASS 3: double confirmation is blocked; unknown tokens are rejected with 409")

            stale = intake("На Абая 46 с утра нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{stale}/triage", {})
            stale_preview = preview(stale, "route_service", {"topic": "water_supply", "priority": "normal"})
            with sqlite3.connect(db) as raw:
                raw.execute("UPDATE complaints SET priority = 'urgent' WHERE id = ?", (stale,))
            code, data = http_request(url + f"/api/workspace/complaints/{stale}/playbooks/route_service/execute",
                                      "POST", {"preview_token": stale_preview["preview_token"]})
            assert code == 409 and any(word in data["detail"] for word in ("измен", "устар", "обнов")), data
            assert call(f"/api/complaints/{stale}")["complaint"]["decision_status"] == "pending"
            print("PASS 4: stale preview is refused with 409 and no partial write")

            other = intake("На Байтурсынова 60 нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{other}/triage", {})
            other_preview = preview(other, "route_service", {"topic": "water_supply", "priority": "normal"})
            other_token = other_preview["preview_token"]
            code, data = http_request(url + f"/api/workspace/complaints/{stale}/playbooks/route_service/execute",
                                      "POST", {"preview_token": other_token})
            assert code == 409 and "другому обращению" in data["detail"], data
            code, data = http_request(url + f"/api/workspace/complaints/{other}/playbooks/quarantine/execute",
                                      "POST", {"preview_token": other_token})
            assert code == 409 and "сценарию" in data["detail"], data
            finish = execute(other, "route_service", other_token)
            assert finish["complaint"]["decision_status"] == "confirmed"
            print("PASS 5: a token is bound to its complaint and playbook; the original token still works")

            quarantine_preview = preview("PULSE-2420", "quarantine")
            assert quarantine_preview["can_execute"]
            quarantined = execute("PULSE-2420", "quarantine", quarantine_preview["preview_token"])
            assert quarantined["complaint"]["quarantined"] == 1
            assert call("/api/workspace/metrics")["quarantined"] >= 1
            blocked_route = preview("PULSE-2420", "route_service", {"topic": "waste_management", "priority": "normal"})
            assert not blocked_route["can_execute"] and "обработку" in blocked_route["blocked_reason"]
            restore_preview = preview("PULSE-2420", "restore")
            restored = execute("PULSE-2420", "restore", restore_preview["preview_token"])
            assert restored["complaint"]["quarantined"] == 0
            assert not preview("PULSE-2420", "restore")["can_execute"]
            print("PASS 6: quarantine and restore round-trip through the same preview flow")

            low = "PULSE-2431"
            low_items = listing(low)
            assert low_items["request_clarification"]["can_execute"] and not low_items["route_service"]["can_execute"]
            assert not low_items["close_faq"]["can_execute"]
            blank = preview(low, "request_clarification", {"question": "   "})
            assert not blank["can_execute"] and blank["preview_token"] is None
            clarifying = preview(low, "request_clarification", {"question": "Какой адрес и что именно произошло?"})
            asked = execute(low, "request_clarification", clarifying["preview_token"])
            assert asked["complaint"]["decision_status"] == "needs_clarification"
            tracked = call(f"/api/workspace/tracking/{low}")
            assert tracked["status"] == "needs_clarification"
            assert tracked["updates"][-1]["text"] == "Какой адрес и что именно произошло?"
            post(f"/api/complaints/{low}/clarification", {"reason": "other", "question": "Q"}, 409)
            post(f"/api/workspace/complaints/{low}/playbooks/request_clarification/preview",
                 {"question": "Q", "reason": "bogus"}, 422)
            print("PASS 7: clarification playbook matches the legacy clarification contract")

            manual_case = intake("Нужна помощь по адресу Абая 90, детали позже")
            call(f"/api/workspace/complaints/{manual_case}/triage", {})
            assert not preview(manual_case, "route_service", {"topic": "roads", "priority": "normal"})["can_execute"]
            manual = preview(manual_case, "route_service",
                             {"topic": "roads", "priority": "normal", "manual": True})
            assert manual["can_execute"], manual["blocked_reason"]
            fields = {a["label"]: a["value"] for a in manual["actions"]}
            assert "Ручное исправление" in fields
            joined = " ".join(fields.values())
            assert "roads" not in joined and "manual=true" not in joined and "Дороги" in joined
            assert "Нурлан" in fields["Оператор"], "Routing must follow the operator-selected category"
            assert fields["Статус"] == "Ожидает службу"
            applied = execute(manual_case, "route_service", manual["preview_token"])
            assert applied["complaint"]["topic"] == "roads"
            assert applied["complaint"]["service_id"] == "srv_roads"
            assert applied["complaint"]["assigned_operator"] == "op-nurlan"
            confirmed = next(e for e in events(manual_case) if e["event_type"] == "operator_confirmed")
            manual_payload = json.loads(confirmed["payload"])
            assert manual_payload["manual_override"] is True and manual_payload["provider"] == "mock"

            reselected = intake("На Абая 80 нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{reselected}/triage", {})
            chosen = preview(reselected, "route_service", {"topic": "sewerage", "priority": "normal"})
            fields = {a["label"]: a["value"] for a in chosen["actions"]}
            assert "Айдана" not in fields["Оператор"], "Selected category must not keep the water-supply operator"
            assert fields["Оператор"].startswith("Мария"), fields["Оператор"]
            chosen_result = execute(reselected, "route_service", chosen["preview_token"])
            assert chosen_result["complaint"]["topic"] == "sewerage"
            assert chosen_result["complaint"]["assigned_operator"] == "op-senior"
            correction = json.loads(next(e for e in events(reselected)
                                    if e["event_type"] == "operator_confirmed")["payload"])
            assert correction["manual_override"] is False and correction["operator_override"] is True
            assert correction["suggested_value"] == "water_supply" and correction["confirmed_value"] == "sewerage"
            print("PASS 8: routing follows the confirmed category, manual override is audited")

            faq = intake("Как проверить статус обращения?")
            faq_items = listing(faq)
            assert faq_items["close_faq"]["can_execute"] and not faq_items["reopen_faq"]["can_execute"]
            assert not listing(intake("Срочно, нет воды! Как проверить статус обращения?"))["close_faq"]["can_execute"]
            faq_preview = preview(faq, "close_faq")
            closed = execute(faq, "close_faq", faq_preview["preview_token"])
            complaint = closed["complaint"]
            assert complaint["resolved_at"] and complaint["resolution_text"] == FAQ_TEMPLATE
            assert complaint["topic"] is None and complaint["decision_status"] == "pending"
            assert "case_resolved" in [e["event_type"] for e in events(faq)]
            tracked = call(f"/api/workspace/tracking/{faq}")
            assert tracked["status"] == "resolved" and tracked["resolution_text"] == FAQ_TEMPLATE
            post(f"/api/complaints/{faq}/confirm",
                 {"topic": "waste_management", "service_id": "srv_clean", "priority": "normal"}, 409)
            post(f"/api/complaints/{faq}/clarification", {"reason": "other", "question": "Q"}, 409)
            post("/api/complaints/syn-020/confirm",
                 {"topic": "sewerage", "service_id": "srv_sewerage", "priority": "normal"})
            print("PASS 9: FAQ closure is audited, guarded against legacy writers, imported rows untouched")

            reopened_items = listing(faq)
            assert reopened_items["reopen_faq"]["can_execute"] and not reopened_items["close_faq"]["can_execute"]
            assert not preview("syn-001", "reopen_faq")["can_execute"]
            reopen_preview = preview(faq, "reopen_faq")
            reopened = execute(faq, "reopen_faq", reopen_preview["preview_token"])
            assert reopened["complaint"]["resolved_at"] is None and reopened["complaint"]["resolution_text"] is None
            assert "case_reopened" in [e["event_type"] for e in events(faq)]
            assert call(f"/api/workspace/tracking/{faq}")["status"] == "pending"
            post(f"/api/complaints/{faq}/clarification", {"reason": "insufficient_detail", "question": "Что уточнить?"})
            print("PASS 10: reopening a closed FAQ case is audited and clears the closure")

            rollback_case = intake("На Масанчи 12 нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{rollback_case}/triage", {})
            rollback_preview = preview(rollback_case, "route_service", {"topic": "water_supply", "priority": "normal"})
            rollback_token = rollback_preview["preview_token"]
            with sqlite3.connect(db) as raw:
                raw.execute("INSERT INTO audit_events VALUES (?, ?, 'reply_saved', ?, ?, 'collision_test', '{}')",
                            (audit_id(rollback_token, "reply_saved"), rollback_case,
                             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
            before = call(f"/api/complaints/{rollback_case}")
            code, data = http_request(url + f"/api/workspace/complaints/{rollback_case}/playbooks/route_service/execute",
                                      "POST", {"preview_token": rollback_token})
            assert code == 409 and "отменены" in data["detail"], data
            after = call(f"/api/complaints/{rollback_case}")
            assert after["complaint"] == before["complaint"], "The failed audit write must roll back the update"
            assert [e["id"] for e in after["events"]] == [e["id"] for e in before["events"]]
            print("PASS 11: a failing audit insert rolls the whole execution back")

            restart_case = intake("На Шевченко 30 нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{restart_case}/triage", {})
            restart_preview = preview(restart_case, "route_service", {"topic": "water_supply", "priority": "normal"})

            stop_server(proc)
            proc, url = start_server(ROOT, db, find_free_port())
            assert call(f"/api/complaints/{cid}")["complaint"]["topic"] == "water_supply"
            assert execute(cid, "route_service", token)["replayed"] is True
            assert execute(restart_case, "route_service", restart_preview["preview_token"])["complaint"]["decision_status"] == "confirmed"
            assert call(f"/api/workspace/tracking/{faq}")["status"] == "needs_clarification"
            print("PASS 12: previews, replays and decisions survive a restart")

            call("/api/workspace/complaints/missing/playbooks", expected=404)
            call(f"/api/workspace/complaints/{cid}/playbooks/missing/preview", {}, 404)
            call(f"/api/workspace/complaints/{cid}/playbooks/missing/execute", {"preview_token": "pb-0123456789ab"}, 404)
            call(f"/api/workspace/complaints/{stale}/playbooks/route_service/preview",
                 {"topic": "unknown_topic", "priority": "normal"}, 422)
            call(f"/api/workspace/complaints/{stale}/playbooks/route_service/preview",
                 {"topic": "roads", "priority": "later"}, 422)
            blocked_incident = preview(stale, "link_mass_incident", {"incident_id": "INC-204", "topic": "roads"})
            assert not blocked_incident["can_execute"] and blocked_incident["blocked_reason"]
            print("PASS 13: unknown ids, invalid input and mismatched incident links stay rejected")

            link_case = intake("На Абая 44 с утра нет холодной воды, весь дом без воды")
            call(f"/api/workspace/complaints/{link_case}/triage", {})
            link_preview = preview(link_case, "link_mass_incident", {"topic": "water_supply", "priority": "normal"})
            assert link_preview["can_execute"], link_preview["blocked_reason"]
            fields = {a["label"]: a["value"] for a in link_preview["actions"]}
            assert "INC-204" in fields["Инцидент"]
            linked = execute(link_case, "link_mass_incident", link_preview["preview_token"])
            assert linked["complaint"]["incident_id"] == "INC-204" and linked["complaint"]["related_to"]
            operators = {o["id"]: o for o in call("/api/workspace/operators")["items"]}
            assert operators["op-aidana"]["current_load"] < operators["op-aidana"]["capacity"]
            assert operators["op-aidana"]["workload"] + 15 > operators["op-aidana"]["workload_capacity"]
            assert linked["complaint"]["assigned_operator"] == "op-timur", linked["complaint"]["assigned_operator"]
            assert "Тимур" in fields["Оператор"], fields["Оператор"]
            kinds = [e["event_type"] for e in events(link_case)]
            assert "incident_linked" in kinds and kinds.count("operator_confirmed") == 1
            unlink_preview = preview(link_case, "unlink_incident")
            assert unlink_preview["can_execute"]
            unlinked = execute(link_case, "unlink_incident", unlink_preview["preview_token"])
            assert unlinked["complaint"]["incident_id"] is None and unlinked["complaint"]["incident_dismissed"] == 1
            assert "incident_rejected" in [e["event_type"] for e in events(link_case)]
            assert not listing(link_case)["link_mass_incident"]["can_execute"]
            print("PASS 14: incident link and audited reversal keep the original text")

            race_case = intake("На Толе би 60 нет воды, весь дом без воды")
            call(f"/api/workspace/complaints/{race_case}/triage", {})
            tokens = [preview(race_case, "route_service", {"topic": "water_supply", "priority": "normal"})["preview_token"]
                      for _ in range(2)]
            results = []

            def fire(token):
                results.append(http_request(
                    url + f"/api/workspace/complaints/{race_case}/playbooks/route_service/execute",
                    "POST", {"preview_token": token}))

            threads = [threading.Thread(target=fire, args=(token,)) for token in tokens]
            [t.start() for t in threads]
            [t.join() for t in threads]
            codes = sorted(code for code, _ in results)
            assert codes == [200, 409], results
            applied = next(data for code, data in results if code == 200)
            refused = next(data for code, data in results if code == 409)
            assert applied["replayed"] is False and applied["complaint"]["decision_status"] == "confirmed"
            assert any(word in refused["detail"] for word in ("измен", "устар", "обнов", "подтверждено")), refused
            kinds = [e["event_type"] for e in events(race_case)]
            assert kinds.count("operator_confirmed") == 1 and kinds.count("playbook_executed") == 1
            print("PASS 15: two tokens for one case cannot both apply; the loser sees the new state")
        finally:
            stop_server(proc)
    print("ALL 15 PLAYBOOK CHECKS PASSED")


if __name__ == "__main__":
    run()

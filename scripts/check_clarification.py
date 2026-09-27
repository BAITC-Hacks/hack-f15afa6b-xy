"""Pulse 109 — T2 clarification workflow check against the real API.

Starts the real application on an isolated synthetic SQLite database and
exercises: pending -> needs_clarification -> pending -> confirmed, including
RU/KK cases, invalid transitions, urgency preservation, stats counters and
persistence across a server restart.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke import find_free_port, http_request, wait_for_server


def start_server(repo_root: Path, db_path: Path, port: int):
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(db_path)
    env["P109_AUTH_DISABLED"] = "1"
    env["PYTHONPATH"] = str(repo_root)
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not wait_for_server(base_url):
        stdout, stderr = proc.communicate(timeout=3)
        raise RuntimeError(f"Server failed to start.\nStdout:\n{stdout.decode()}\nStderr:\n{stderr.decode()}")
    return proc, base_url


def stop_server(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def event_types(detail: dict) -> list[str]:
    return [e["event_type"] for e in detail["events"]]


def payload_of(detail: dict, event_type: str) -> dict:
    event = next(e for e in detail["events"] if e["event_type"] == event_type)
    return {"event": event, "payload": json.loads(event["payload"])}


def run_check():
    repo_root = Path(__file__).resolve().parents[1]
    tmp_dir = tempfile.TemporaryDirectory()
    db_path = Path(tmp_dir.name) / "clarification.db"
    proc, base_url = start_server(repo_root, db_path, find_free_port())
    print(f"[*] Clarification check server: {base_url}")
    print(f"[*] Isolated temporary database: {db_path}\n")

    try:
        # 1. RU intake with an urgent sign and a known topic (heating + urgent)
        ru_text = "Срочно: авария, в доме нет отопления, нужен адрес"
        status, created = http_request(
            f"{base_url}/api/intake", "POST", {"text": ru_text, "region_id": "KZ-AST", "language": "ru"}
        )
        assert status == 201, f"Intake returned {status}"
        cid = created["id"]
        status, cls = http_request(f"{base_url}/api/complaints/{cid}/classify", "POST")
        assert status == 200, f"Classify returned {status}"
        assert cls["proposal"]["topic"] == "heating"
        assert cls["proposal"]["priority"] == "urgent"
        print(f"PASS 1: RU intake '{cid}' classified as heating with urgent proposal")

        init_stats = http_request(f"{base_url}/api/stats")[1]

        # 2. Request clarification: reason + editable question, no topic/service required
        question = "Уточните, пожалуйста, где возникла проблема: адрес или ближайший ориентир."
        status, clar = http_request(
            f"{base_url}/api/complaints/{cid}/clarification",
            "POST",
            {"reason": "unknown_place", "question": question, "actor": "operator_t2"},
        )
        assert status == 200, f"Clarification request returned {status}"
        assert clar["complaint"]["decision_status"] == "needs_clarification"
        assert clar["complaint"]["proposed_priority"] == "urgent", "Urgency must survive clarification"
        assert clar["complaint"]["topic"] is None and clar["complaint"]["service_id"] is None
        requested = payload_of(clar, "clarification_requested")
        assert requested["payload"]["reason"] == "unknown_place"
        assert requested["payload"]["question"] == question
        assert requested["event"]["actor"] == "operator_demo"
        assert requested["event"]["occurred_at"] and requested["event"]["recorded_at"]
        print("PASS 2: needs_clarification saved with reason, question, author and time; urgency intact")

        # 3. Stats count the new status separately
        mid_stats = http_request(f"{base_url}/api/stats")[1]
        assert mid_stats["clarification_count"] == init_stats["clarification_count"] + 1
        assert mid_stats["pending_count"] == init_stats["pending_count"] - 1
        assert mid_stats["total_complaints"] == init_stats["total_complaints"]
        print(f"PASS 3: Stats: clarification_count={mid_stats['clarification_count']}, pending_count={mid_stats['pending_count']}")

        # 4. Direct confirmation is blocked while clarification is pending
        status, _ = http_request(
            f"{base_url}/api/complaints/{cid}/confirm",
            "POST",
            {"topic": "heating", "service_id": "srv_teplo", "priority": "urgent"},
        )
        assert status == 409, f"Confirm during clarification expected 409, got {status}"
        print("PASS 4: Confirm rejected with 409 while clarification is pending")

        # 5. Resume without a received clarification is blocked
        status, _ = http_request(f"{base_url}/api/complaints/{cid}/resume", "POST", {})
        assert status == 409, f"Resume without supplement expected 409, got {status}"
        print("PASS 5: Resume rejected with 409 before any received clarification")

        # 6. Validation: blank supplement 422, invalid reason 422, re-request 409
        status, _ = http_request(
            f"{base_url}/api/complaints/{cid}/clarification-response", "POST", {"text": "   "}
        )
        assert status == 422, f"Blank supplement expected 422, got {status}"
        status, _ = http_request(
            f"{base_url}/api/complaints/{cid}/clarification",
            "POST",
            {"reason": "bogus_reason", "question": "Q"},
        )
        assert status == 422, f"Invalid reason expected 422, got {status}"
        status, _ = http_request(
            f"{base_url}/api/complaints/{cid}/clarification",
            "POST",
            {"reason": "other", "question": "Повторный запрос"},
        )
        assert status == 409, f"Re-request from needs_clarification expected 409, got {status}"
        print("PASS 6: Blank supplement 422, invalid reason 422, repeated request 409")

        # 7. Record the received clarification (KK) as a separate entry
        kk_supplement = "Астана қаласы, Абай даңғылы 15"
        status, sup = http_request(
            f"{base_url}/api/complaints/{cid}/clarification-response",
            "POST",
            {"text": kk_supplement, "actor": "operator_t2"},
        )
        assert status == 200, f"Clarification response returned {status}"
        assert sup["complaint"]["decision_status"] == "needs_clarification"
        assert sup["complaint"]["proposed_priority"] == "urgent"
        received = payload_of(sup, "clarification_received")
        assert received["payload"]["text"] == kk_supplement, "Received text must be preserved exactly"
        assert received["event"]["actor"] == "operator_demo"
        print("PASS 7: KK supplement stored separately from the original with author and time")

        # 8. Explicit return to pending
        status, resumed = http_request(f"{base_url}/api/complaints/{cid}/resume", "POST", {})
        assert status == 200, f"Resume returned {status}"
        assert resumed["complaint"]["decision_status"] == "pending"
        assert resumed["complaint"]["proposed_priority"] == "urgent", "Urgency must survive resume"
        assert resumed["complaint"]["text"] == ru_text, "Original text must never be overwritten"
        resumed_stats = http_request(f"{base_url}/api/stats")[1]
        assert resumed_stats["clarification_count"] == init_stats["clarification_count"]
        assert resumed_stats["pending_count"] == init_stats["pending_count"]
        print("PASS 8: Resume returned the complaint to pending; counters consistent")

        # 9. Confirmation after the clarification cycle
        status, conf = http_request(
            f"{base_url}/api/complaints/{cid}/confirm",
            "POST",
            {"topic": "heating", "service_id": "srv_teplo", "priority": "urgent"},
        )
        assert status == 200, f"Confirm returned {status}"
        assert conf["complaint"]["decision_status"] == "confirmed"
        assert conf["complaint"]["text"] == ru_text
        detail = http_request(f"{base_url}/api/complaints/{cid}")[1]
        assert event_types(detail) == [
            "intake",
            "classification_proposed",
            "clarification_requested",
            "clarification_received",
            "clarification_resolved",
            "operator_confirmed",
        ], f"Unexpected event order: {event_types(detail)}"
        print("PASS 9: Confirm succeeded; event trail is ordered and complete")

        # 10. Invalid transitions after confirmation and unknown ids
        status, _ = http_request(
            f"{base_url}/api/complaints/{cid}/clarification", "POST", {"reason": "other", "question": "Q"}
        )
        assert status == 409
        status, _ = http_request(f"{base_url}/api/complaints/{cid}/clarification-response", "POST", {"text": "T"})
        assert status == 409
        status, _ = http_request(f"{base_url}/api/complaints/{cid}/resume", "POST", {})
        assert status == 409
        for action, body in (
            ("clarification", {"reason": "other", "question": "Q"}),
            ("clarification-response", {"text": "T"}),
            ("resume", {}),
        ):
            status, _ = http_request(f"{base_url}/api/complaints/cmp-unknown/{action}", "POST", body)
            assert status == 404, f"Unknown complaint {action} expected 404, got {status}"
        print("PASS 10: Post-confirm transitions 409; unknown complaint ids 404")

        # 11. KK scenario: unknown topic stays unset and urgency is not invented
        status, created_kk = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Түсініксіз жағдай, көмек керек", "region_id": "KZ-ALA", "language": "kk"}
        )
        assert status == 201
        kk_id = created_kk["id"]
        status, cls_kk = http_request(f"{base_url}/api/complaints/{kk_id}/classify", "POST")
        assert status == 200
        assert cls_kk["proposal"]["topic"] is None
        assert cls_kk["proposal"]["priority"] is None, "Unknown urgency must be null, not needs_review"
        status, clar_kk = http_request(
            f"{base_url}/api/complaints/{kk_id}/clarification",
            "POST",
            {"reason": "unclear_event", "question": "Қандай мәселе болғанын толығырақ жазыңыз.", "actor": "operator_t2"},
        )
        assert status == 200
        assert clar_kk["complaint"]["proposed_priority"] is None
        status, _ = http_request(
            f"{base_url}/api/complaints/{kk_id}/clarification-response",
            "POST",
            {"text": "Абай даңғылында су жоқ", "actor": "operator_t2"},
        )
        assert status == 200
        status, resumed_kk = http_request(f"{base_url}/api/complaints/{kk_id}/resume", "POST", {})
        assert status == 200
        assert resumed_kk["complaint"]["topic"] is None
        assert resumed_kk["complaint"]["service_id"] is None
        assert resumed_kk["complaint"]["proposed_priority"] is None
        print("PASS 11: KK unknown-topic case: no invented topic/service/urgency")

        # 12. Blank question rejected on a fresh complaint
        status, created_blank = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Тестовая заявка", "region_id": "KZ-AST"}
        )
        assert status == 201
        status, _ = http_request(
            f"{base_url}/api/complaints/{created_blank['id']}/clarification",
            "POST",
            {"reason": "other", "question": "   "},
        )
        assert status == 422, f"Blank question expected 422, got {status}"
        print("PASS 12: Blank question rejected with 422")

        # 13. Re-classification and search use original + received clarifications, kept separately
        status, enrich_created = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Жалоба без конкретики", "region_id": "KZ-AST", "language": "ru"}
        )
        assert status == 201
        enrich_id = enrich_created["id"]
        status, enrich_cls = http_request(f"{base_url}/api/complaints/{enrich_id}/classify", "POST")
        assert status == 200
        assert enrich_cls["proposal"]["topic"] is None
        assert enrich_cls["proposal"]["priority"] is None
        status, _ = http_request(
            f"{base_url}/api/complaints/{enrich_id}/clarification",
            "POST",
            {"reason": "insufficient_detail", "question": "Добавьте детали.", "actor": "operator_t2"},
        )
        assert status == 200
        supplement = "Мұнда мусор шығарылмайды, контейнер жоқ"
        status, _ = http_request(
            f"{base_url}/api/complaints/{enrich_id}/clarification-response",
            "POST",
            {"text": supplement, "actor": "operator_t2"},
        )
        assert status == 200
        status, enrich_cls2 = http_request(f"{base_url}/api/complaints/{enrich_id}/classify", "POST")
        assert status == 200
        assert enrich_cls2["proposal"]["topic"] == "waste_management", "Classification must see the received clarification"
        assert enrich_cls2["proposal"]["service_id"] == "srv_clean"
        status, enrich_detail = http_request(f"{base_url}/api/complaints/{enrich_id}")
        assert enrich_detail["complaint"]["text"] == "Жалоба без конкретики"
        proposed_events = [e for e in enrich_detail["events"] if e["event_type"] == "classification_proposed"]
        assert json.loads(proposed_events[-1]["payload"])["clarification_count"] == 1
        received_events = [e for e in enrich_detail["events"] if e["event_type"] == "clarification_received"]
        assert json.loads(received_events[-1]["payload"])["text"] == supplement
        status, enrich_similar = http_request(f"{base_url}/api/complaints/{enrich_id}/similar?limit=5")
        assert status == 200
        assert enrich_similar["candidates"], "Enriched retrieval must find candidates"
        assert all(c["topic"] == "waste_management" for c in enrich_similar["candidates"])
        print("PASS 13: classify and similar use original + clarifications, stored separately")

        # 14. Priority semantics: unknown is null, urgency is a proposal, legacy values survive
        status, urgent_created = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Авария, непонятно что происходит", "region_id": "KZ-AST"}
        )
        assert status == 201
        urgent_id = urgent_created["id"]
        status, urgent_cls = http_request(f"{base_url}/api/complaints/{urgent_id}/classify", "POST")
        assert status == 200
        assert urgent_cls["proposal"]["topic"] is None
        assert urgent_cls["proposal"]["priority"] == "urgent", "Urgency signal must survive an unknown topic"
        status, urgent_detail = http_request(f"{base_url}/api/complaints/{urgent_id}")
        assert urgent_detail["complaint"]["priority"] is None, "Proposal must not become a confirmed priority"
        assert urgent_detail["complaint"]["proposed_priority"] == "urgent"
        status, _ = http_request(
            f"{base_url}/api/complaints/{urgent_id}/confirm",
            "POST",
            {"topic": "roads", "service_id": "srv_roads", "priority": "needs_review"},
        )
        assert status == 422, "needs_review is not a priority value"
        with sqlite3.connect(db_path) as legacy_conn:
            legacy_conn.execute("UPDATE complaints SET priority = 'needs_review' WHERE id = 'syn-020'")
        status, legacy_stats = http_request(f"{base_url}/api/stats")
        assert legacy_stats["by_priority"].get("needs_review") == 1, "Legacy value must stay visible, not be rewritten"
        status, legacy_detail = http_request(f"{base_url}/api/complaints/syn-020")
        assert legacy_detail["complaint"]["priority"] == "needs_review"
        status, legacy_confirmed = http_request(
            f"{base_url}/api/complaints/syn-020/confirm",
            "POST",
            {
                "topic": legacy_detail["complaint"]["topic"],
                "service_id": legacy_detail["complaint"]["service_id"],
                "priority": "normal",
            },
        )
        assert status == 200 and legacy_confirmed["complaint"]["priority"] == "normal"
        print("PASS 14: unknown urgency is null, urgency is a proposal, needs_review rejected, legacy preserved")

        # 15. Re-classification never overwrites the human decision
        status, human_created = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Авария, срочно, нет отопления", "region_id": "KZ-ABA"}
        )
        assert status == 201
        human_id = human_created["id"]
        status, _ = http_request(f"{base_url}/api/complaints/{human_id}/classify", "POST")
        assert status == 200
        status, human_confirmed = http_request(
            f"{base_url}/api/complaints/{human_id}/confirm",
            "POST",
            {"topic": "roads", "service_id": "srv_roads", "priority": "normal"},
        )
        assert status == 200 and human_confirmed["complaint"]["priority"] == "normal"
        status, again = http_request(f"{base_url}/api/complaints/{human_id}/classify", "POST")
        assert status == 200 and again["proposal"]["priority"] == "urgent"
        status, human_detail = http_request(f"{base_url}/api/complaints/{human_id}")
        assert human_detail["complaint"]["priority"] == "normal", "Human priority must survive re-classification"
        assert human_detail["complaint"]["topic"] == "roads" and human_detail["complaint"]["service_id"] == "srv_roads"
        assert human_detail["complaint"]["decision_status"] == "confirmed"
        assert human_detail["complaint"]["proposed_priority"] == "urgent"
        status, urgent_queue = http_request(f"{base_url}/api/complaints?priority=urgent&view=all&page_size=50")
        assert status == 200 and human_id not in [c["id"] for c in urgent_queue["items"]]
        print("PASS 15: re-classification updates only the proposal; human priority/topic/service stay")

        # 16. Restart persistence: statuses and event trails survive
        stop_server(proc)
        proc, base_url = start_server(repo_root, db_path, find_free_port())
        detail_after = http_request(f"{base_url}/api/complaints/{cid}")[1]
        assert detail_after["complaint"]["decision_status"] == "confirmed"
        assert len(detail_after["events"]) == 6
        detail_kk = http_request(f"{base_url}/api/complaints/{kk_id}")[1]
        assert detail_kk["complaint"]["decision_status"] == "pending"
        assert event_types(detail_kk) == [
            "intake",
            "classification_proposed",
            "clarification_requested",
            "clarification_received",
            "clarification_resolved",
        ]
        stats_after = http_request(f"{base_url}/api/stats")[1]
        assert stats_after["total_complaints"] == init_stats["total_complaints"] + 5
        print("PASS 16: Statuses and audit trails persist across a server restart")

        print("\n========================================================")
        print("ALL 16 CLARIFICATION CHECKS PASSED SUCCESSFULLY!")
        print("========================================================")
    finally:
        print("[*] Terminating test server process...")
        stop_server(proc)
        tmp_dir.cleanup()
        print("[*] Temporary test resources cleaned up.")


if __name__ == "__main__":
    run_check()

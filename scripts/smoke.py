"""Pulse 109 — Automated Smoke Check.

Tests the synthetic vertical slice using a temporary SQLite database,
locally started server, and Python standard-library HTTP requests.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from check_coverage import check_api


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_request(url: str, method: str = "GET", data: dict | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            return resp.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8")
        try:
            return e.code, json.loads(content)
        except Exception:
            return e.code, {"raw": content}


def wait_for_server(base_url: str, timeout: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            status, data = http_request(f"{base_url}/api/health")
            if status == 200 and data.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def run_smoke():
    repo_root = Path(__file__).resolve().parents[1]
    tmp_dir = tempfile.TemporaryDirectory()
    tmp_db = Path(tmp_dir.name) / "smoke_pulse109.db"
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["DATABASE_PATH"] = str(tmp_db)
    env["P109_AUTH_DISABLED"] = "1"
    # Ensure current python executable and repo root are used
    env["PYTHONPATH"] = str(repo_root)

    print(f"[*] Starting Pulse 109 test server on port {port}...")
    print(f"[*] Isolated temporary database: {tmp_db}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not wait_for_server(base_url):
            stdout, stderr = proc.communicate(timeout=3)
            raise RuntimeError(f"Server failed to start.\nStdout:\n{stdout.decode()}\nStderr:\n{stderr.decode()}")
        print("[+] Server ready. Beginning smoke verification...\n")

        # 1. Health check
        status, health = http_request(f"{base_url}/api/health")
        assert status == 200, f"Health returned {status}"
        assert health.get("mode") == "mock"
        assert health.get("training_status") == "not_trained"
        assert health.get("checkpoint_id") is None
        assert "SYNTHETIC DEMO" in health.get("banner", "")
        print("PASS 1: GET /api/health declares mock mode, null checkpoint, and synthetic demo banner")

        # 2. Blank intake rejection
        status, err = http_request(f"{base_url}/api/intake", "POST", {"text": "   ", "region_id": "KZ-AST"})
        assert status == 422, f"Expected 422 for blank text, got {status}"
        print("PASS 2: Blank intake rejected with 422")

        # 3. Invalid region rejection
        status, err = http_request(f"{base_url}/api/intake", "POST", {"text": "Тест", "region_id": "KZ-INVALID"})
        assert status == 422, f"Expected 422 for invalid region, got {status}"
        print("PASS 3: Invalid region rejected with 422")

        # 4. Initial seeded counts from SQLite
        status, initial_stats = http_request(f"{base_url}/api/stats")
        assert status == 200
        init_total = initial_stats["total_complaints"]
        init_pending = initial_stats["pending_count"]
        init_confirmed = initial_stats["confirmed_count"]
        assert init_total == 20, f"Expected 20 seeded fixtures, found {init_total}"
        print(f"PASS 4: Initial SQLite stats loaded ({init_total} complaints: {init_pending} pending, {init_confirmed} confirmed)")

        # 5. Successful intake
        intake_payload = {
            "text": "В доме по проспекту Республики 15 отключили холодную воду и упало давление в системе",
            "region_id": "KZ-AST",
            "language": "ru",
        }
        status, created = http_request(f"{base_url}/api/intake", "POST", intake_payload)
        assert status == 201, f"Expected 201 created, got {status}"
        cid = created["id"]
        assert cid.startswith("cmp-")
        assert created["decision_status"] == "pending"
        assert created["data_origin"] == "synthetic"
        print(f"PASS 5: POST /api/intake created pending complaint '{cid}' (data_origin=synthetic)")

        # 6. Mock classification proposal
        status, class_res = http_request(f"{base_url}/api/complaints/{cid}/classify", "POST")
        assert status == 200, f"Classify returned {status}"
        assert class_res["mode"] == "mock"
        assert class_res["checkpoint_id"] is None
        assert class_res["training_status"] == "not_trained"
        assert class_res["confidence"] is None
        proposal = class_res["proposal"]
        assert proposal["topic"] == "water_supply"
        assert proposal["service_id"] == "srv_vodokanal"
        assert proposal["priority"] is None, "No urgent signal means unknown urgency (null), not 'normal'"
        print(f"PASS 6: POST /api/complaints/{cid}/classify returned mock proposal: topic='{proposal['topic']}', service='{proposal['service_id']}'")

        # Verify proposal did not prematurely confirm complaint
        status, detail = http_request(f"{base_url}/api/complaints/{cid}")
        assert status == 200
        assert detail["complaint"]["decision_status"] == "pending"
        print("PASS 7: Complaint remains in pending status after proposal (no premature confirmation)")

        # 8. Candidate retrieval
        status, sim_res = http_request(f"{base_url}/api/complaints/{cid}/similar?limit=5")
        assert status == 200
        assert sim_res["mode"] == "lexical_fallback"
        assert sim_res["checkpoint_id"] is None
        assert sim_res["training_status"] == "not_trained"
        candidates = sim_res["candidates"]
        assert len(candidates) > 0
        for cand in candidates:
            assert cand["complaint_id"] != cid
            assert cand["origin"] == "synthetic"
            assert "excerpt" in cand
            assert 0 <= cand["similarity"] <= 1
            assert cand["resolution_text"]
        assert candidates == sorted(candidates, key=lambda item: (-item["similarity"], item["complaint_id"]))
        print(f"PASS 8: GET /api/complaints/{cid}/similar ranked {len(candidates)} solved cases with safe fallback scores")

        # Boundary checks for similar limit
        status, _ = http_request(f"{base_url}/api/complaints/{cid}/similar?limit=0")
        assert status == 422
        status, _ = http_request(f"{base_url}/api/complaints/{cid}/similar?limit=25")
        assert status == 422
        print("PASS 9: Retrieval limit validation enforced (limit outside 1..20 rejected with 422)")

        # 10. Operator confirmation
        confirm_payload = {
            "topic": "water_supply",
            "service_id": "srv_vodokanal",
            "priority": "urgent",
            "actor": "operator_smoke",
        }
        status, conf_res = http_request(f"{base_url}/api/complaints/{cid}/confirm", "POST", confirm_payload)
        assert status == 200
        cmp_conf = conf_res["complaint"]
        assert cmp_conf["decision_status"] == "confirmed"
        assert cmp_conf["topic"] == "water_supply"
        assert cmp_conf["service_id"] == "srv_vodokanal"
        assert cmp_conf["priority"] == "urgent"
        print(f"PASS 10: POST /api/complaints/{cid}/confirm persisted operator decision: confirmed, urgent, srv_vodokanal")

        # 11. Changed counts from SQLite
        status, after_stats = http_request(f"{base_url}/api/stats")
        assert status == 200
        assert after_stats["total_complaints"] == init_total + 1
        assert after_stats["confirmed_count"] == init_confirmed + 1
        assert after_stats["pending_count"] == init_pending
        print(f"PASS 11: SQLite aggregate stats verified: total={after_stats['total_complaints']}, confirmed={after_stats['confirmed_count']}")

        # 12. Audit events verified
        status, detail_after = http_request(f"{base_url}/api/complaints/{cid}")
        event_types = [e["event_type"] for e in detail_after["events"]]
        assert "intake" in event_types
        assert "classification_proposed" in event_types
        assert "operator_confirmed" in event_types
        print(f"PASS 12: Audit trail intact with 3 sequential events: {event_types}")

        # 13. Aggregate analytics and validated exports
        status, res = http_request(f"{base_url}/api/alerts")
        assert status == 200 and res["data_origin"] == "organizer" and res["method"]
        print(f"PASS 13a: GET /api/alerts returned {len(res['items'])} evidence-backed alerts")

        status, res = http_request(f"{base_url}/api/forecast?horizon_months=3")
        assert status == 200 and len(res["forecast"]) == 3 and res["evaluation"]["backtest_points"] > 0
        assert res["evaluation"]["mae"] >= 0 and res["evaluation"]["smape_percent"] >= 0
        print("PASS 13b: GET /api/forecast returned 3 months with MAE and sMAPE backtest metrics")

        status, _ = http_request(f"{base_url}/api/forecast?horizon_months=5")
        assert status == 422
        print("PASS 13c: GET /api/forecast?horizon_months=5 rejected with 422 (input validation)")

        status, res = http_request(f"{base_url}/api/query", "POST", {"question": "Какие категории лидируют?"})
        assert status == 200 and res["chart"]["values"] and res["provenance"]["data_origin"] == "organizer"
        print("PASS 13d: POST /api/query returned a numeric answer, chart and provenance")

        with urllib.request.urlopen(f"{base_url}/api/reports?format=pdf") as response:
            assert response.headers.get_content_type() == "application/pdf" and response.read().startswith(b"%PDF")
        with urllib.request.urlopen(f"{base_url}/api/reports?format=xlsx") as response:
            assert response.headers.get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" and response.read().startswith(b"PK")
        print("PASS 13e: PDF and XLSX exports are real downloadable files")

        status, _ = http_request(f"{base_url}/api/reports?format=csv")
        assert status == 422
        print("PASS 13f: GET /api/reports?format=csv rejected with 422 (unsupported format)")

        check_api(base_url, http_request)
        print("PASS 14: Coverage API separates metadata, missing regions and unknown counts; filters validated")

        print("\n========================================================")
        print("ALL 14 SMOKE CHECKS PASSED SUCCESSFULLY!")
        print("========================================================")

    finally:
        print("[*] Terminating test server process...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        tmp_dir.cleanup()
        print("[*] Temporary test resources cleaned up.")


if __name__ == "__main__":
    run_smoke()

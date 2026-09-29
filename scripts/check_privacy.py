"""Focused privacy and public-moderation check using isolated SQLite databases."""

import base64
import http.cookiejar
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
PRIVATE_TEXT = ("Отключили холодную воду, кв. 17. Телефон +7 (777) 123-45-67, "
                "email citizen@example.kz, ИИН 900101301234")


class Client:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def cookie(self, name):
        return next(cookie.value for cookie in self.cookies if cookie.name == name)

    def request(self, url, method="GET", data=None, headers=None, raw=False):
        body = json.dumps(data).encode() if data is not None else None
        request_headers = {**({"Content-Type": "application/json"} if data is not None else {}),
                           **(headers or {})}
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        content = response.read()
        if raw:
            return response.status, content
        try:
            payload = json.loads(content) if content else {}
        except json.JSONDecodeError:
            payload = {"raw": content.decode(errors="replace")}
        return response.status, payload


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def legacy_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE complaints (
                id TEXT PRIMARY KEY, data_origin TEXT NOT NULL, source_system TEXT,
                source_record_id TEXT, text TEXT NOT NULL, region_id TEXT NOT NULL,
                received_at TEXT, ingested_at TEXT NOT NULL, language TEXT NOT NULL,
                source_category TEXT, source_service TEXT, source_status TEXT,
                topic TEXT, service_id TEXT, priority TEXT,
                decision_status TEXT NOT NULL DEFAULT 'pending',
                incident_id TEXT, duplicate_of TEXT, resolution_text TEXT, resolved_at TEXT,
                proposed_topic TEXT, proposed_service_id TEXT, proposed_priority TEXT
                , public_consent INTEGER NOT NULL DEFAULT 0
                , moderation_status TEXT NOT NULL DEFAULT 'private'
                , public_text TEXT
            );
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY, complaint_id TEXT NOT NULL, event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        rows = [
            ("legacy-seed", "synthetic", "fixture_generator", "Синтетическая заявка", "KZ-ALA"),
            ("legacy-ambiguous", "synthetic", "operator_demo_intake", "Старая публичная заявка", "KZ-ALA"),
            ("legacy-organizer", "organizer", None, "Закрытая запись организатора", "KZ-ALA"),
            ("legacy-citizen", "citizen", None, "Закрытая заявка жителя", "KZ-ALA"),
        ]
        conn.executemany("""INSERT INTO complaints
            (id, data_origin, source_system, text, region_id, ingested_at, language, decision_status)
            VALUES (?, ?, ?, ?, ?, '2026-09-28T00:00:00+00:00', 'ru', 'pending')""", rows)
        conn.execute("""INSERT INTO complaints
            (id, data_origin, text, region_id, ingested_at, language, decision_status,
             public_consent, moderation_status)
            VALUES ('legacy-confirmed', 'citizen', ?, 'KZ-ALA', '2026-09-28T00:00:00+00:00',
                    'ru', 'confirmed', 1, 'pending')""", (PRIVATE_TEXT,))


def start(db_path, demo=False, auth_disabled=False):
    port = free_port()
    env = os.environ.copy()
    env.update({"DATABASE_PATH": str(db_path), "PYTHONPATH": str(ROOT), "P109_SECURE_COOKIES": "0", "P109_SIGNUP_INVITE": "privacy-test-invite"})
    for key in ("P109_DEMO_MODE", "P109_AUTH_DISABLED", "P109_S3_ENDPOINT", "P109_S3_BUCKET"):
        env.pop(key, None)
    if demo:
        env["P109_DEMO_MODE"] = "1"
    if auth_disabled:
        env["P109_AUTH_DISABLED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    client = Client()
    for _ in range(60):
        try:
            if client.request(base + "/api/health")[0] == 200:
                return process, base
        except OSError:
            pass
        time.sleep(.2)
    stdout, stderr = process.communicate(timeout=3)
    raise RuntimeError(f"server failed\n{stdout.decode()}\n{stderr.decode()}")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def signup(base):
    client = Client()
    status, payload = client.request(base + "/api/auth/signup", "POST", {
        "name": "Privacy Reviewer", "email": "privacy@example.kz",
        "password": "correct horse battery staple", "role": "operator", "invite_code": "privacy-test-invite",
    })
    assert status == 201, (status, payload)
    return client, client.cookie("pulse109_csrf"), payload["user"]["id"]


def real_mode_check(db_path):
    legacy_db(db_path)
    process, base = start(db_path)
    anonymous = Client()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(complaints)")}
            assert {"public_consent", "moderation_status", "public_text"} <= columns
            migrated = {row["id"]: dict(row) for row in conn.execute(
                "SELECT * FROM complaints WHERE id LIKE 'legacy-%'")}
            assert migrated["legacy-seed"]["moderation_status"] == "approved"
            assert migrated["legacy-seed"]["public_text"] == "Синтетическая заявка"
            for key in ("legacy-ambiguous", "legacy-organizer", "legacy-citizen"):
                assert migrated[key]["public_consent"] == 0
                assert migrated[key]["moderation_status"] == "private"
                assert migrated[key]["public_text"] is None
            assert migrated["legacy-confirmed"]["moderation_status"] == "approved"
            assert PRIVATE_TEXT not in migrated["legacy-confirmed"]["public_text"]
        print("PASS 1: migration publishes only known seed rows and keeps ambiguous legacy intake private")

        intake = {"text": PRIVATE_TEXT, "region_id": "KZ-ALA", "language": "ru",
                  "address": "Абая 44", "public_consent": True, "photo_data": PNG}
        status, created = anonymous.request(base + "/api/workspace/intake", "POST", intake)
        assert status == 201 and created["data_origin"] == "citizen"
        assert created["moderation_status"] == "pending"
        cid = created["id"]
        status, feed = anonymous.request(base + "/api/workspace/public/complaints")
        assert status == 200 and cid not in {item["id"] for item in feed["items"]}
        assert anonymous.request(base + f"/api/workspace/public/complaints/{cid}/subscribe", "POST",
                                 {"subscriber_key": "privacy-user"})[0] == 404
        assert anonymous.request(base + f"/api/workspace/public/complaints/{cid}/photo", raw=True)[0] == 404
        assert anonymous.request(base + f"/api/workspace/complaints/{cid}/photo", raw=True)[0] == 401
        assert anonymous.request(base + f"/api/workspace/complaints/{cid}/moderation", "POST",
                                 {"status": "approved", "public_text": PRIVATE_TEXT})[0] == 401
        print("PASS 2: real intake is pending and hidden from feed, media and subscriptions")

        operator, csrf, actor = signup(base)
        headers = {"X-CSRF-Token": csrf}
        assert anonymous.request(base + "/api/workspace/demo/intake", "POST", {})[0] == 401
        status, demo = operator.request(base + "/api/workspace/demo/intake", "POST", {}, headers)
        assert status == 201 and demo["data_origin"] == "synthetic"
        assert demo["moderation_status"] == "approved"
        status, feed = anonymous.request(base + "/api/workspace/public/complaints")
        assert demo["id"] in {item["id"] for item in feed["items"]}
        print("PASS 2b: authenticated jury demo creates an explicitly synthetic public record")
        assert operator.request(base + f"/api/workspace/complaints/{cid}/photo", raw=True) == (200, b"\x89PNG\r\n\x1a\n")
        assert operator.request(base + f"/api/workspace/complaints/{cid}/triage", "POST", {}, headers)[0] == 200
        status, decided = operator.request(base + f"/api/workspace/complaints/{cid}/decide", "POST",
                                            {"topic": "water_supply", "priority": "normal"}, headers)
        assert status == 200 and decided["complaint"]["moderation_status"] == "approved"
        safe = decided["complaint"]["public_text"]
        for secret in ("citizen@example.kz", "+7 (777) 123-45-67", "900101301234", "кв. 17"):
            assert secret not in safe
        assert all(label in safe for label in ("[EMAIL СКРЫТ]", "[ТЕЛЕФОН СКРЫТ]", "[ИИН СКРЫТ]",
                                                "[КВАРТИРА СКРЫТА]"))
        status, feed = anonymous.request(base + "/api/workspace/public/complaints")
        public = next(item for item in feed["items"] if item["id"] == cid)
        assert public["text"] == safe and not public["has_photo"] and not public["has_video"]
        assert anonymous.request(base + f"/api/workspace/public/complaints/{cid}/photo", raw=True)[0] == 404
        status, similar = anonymous.request(base + "/api/workspace/public/similar", "POST", {
            "text": "На Абая 44 нет холодной воды", "region_id": "KZ-ALA", "address": "Абая 44",
        })
        assert status == 200 and cid in {item["id"] for item in similar["items"]}
        assert anonymous.request(base + f"/api/workspace/public/complaints/{cid}/subscribe", "POST",
                                 {"subscriber_key": "privacy-user"})[0] == 200
        with sqlite3.connect(db_path) as conn:
            event = conn.execute("""SELECT actor, payload FROM audit_events
                                     WHERE complaint_id = ? AND event_type = 'public_moderation'""", (cid,)).fetchone()
        assert event[0] == actor and PRIVATE_TEXT not in event[1]
        print("PASS 3: operator confirmation publishes the consented case with redaction and map coordinates")

        status, rejected = anonymous.request(base + "/api/workspace/intake", "POST", {
            "text": "На улице не горит фонарь", "region_id": "KZ-ALA", "public_consent": True,
        })
        rejected_id = rejected["id"]
        assert operator.request(base + f"/api/workspace/complaints/{rejected_id}/moderation", "POST",
                                {"status": "rejected"}, headers)[0] == 200
        assert anonymous.request(base + f"/api/workspace/public/complaints/{rejected_id}/subscribe", "POST",
                                 {"subscriber_key": "privacy-user"})[0] == 404
        status, private = anonymous.request(base + "/api/intake", "POST", {
            "text": "Закрытая заявка", "region_id": "KZ-ALA", "public_consent": False,
        })
        assert status == 201 and private["data_origin"] == "citizen" and private["moderation_status"] == "private"
        assert operator.request(base + f"/api/workspace/complaints/{private['id']}/moderation", "POST",
                                {"status": "approved", "public_text": "Закрытая заявка"}, headers)[0] == 409
        print("PASS 4: rejection and missing consent keep real records private")

        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE complaints SET resolution_text = NULL")
            conn.execute("""INSERT INTO complaints
                (id, data_origin, text, region_id, ingested_at, language, decision_status,
                 topic, resolution_text, resolved_at, public_consent, moderation_status)
                VALUES ('cross-topic-solution', 'synthetic', 'Починили электрощит', 'KZ-ALA',
                        '2026-09-28T00:00:00+00:00', 'ru', 'confirmed', 'electricity',
                        'Электрощит заменён', '2026-09-28T01:00:00+00:00', 1, 'approved')""")
            conn.execute("UPDATE complaints SET proposed_topic = 'water_supply' WHERE id = ?", (cid,))
        status, candidates = operator.request(base + f"/api/complaints/{cid}/similar?limit=20")
        assert status == 200 and [item["complaint_id"] for item in candidates["candidates"]] == ["cross-topic-solution"]
        print("PASS 5: similar-case ranking keeps the bounded cross-topic candidate pool")
    finally:
        stop(process)

    from demo_data import init_workspace
    with sqlite3.connect(db_path) as conn:
        init_workspace(conn)
        init_workspace(conn)
        assert conn.execute("SELECT moderation_status FROM complaints WHERE id = ?", (cid,)).fetchone()[0] == "approved"
    print("PASS 6: migration is idempotent and preserves reviewed decisions")


def demo_mode_check(db_path):
    process, base = start(db_path, demo=True, auth_disabled=True)
    client = Client()
    try:
        status, created = client.request(base + "/api/workspace/intake", "POST", {
            "text": PRIVATE_TEXT, "region_id": "KZ-ALA", "language": "ru", "public_consent": False,
            "address": "Абая 44", "photo_data": PNG,
        })
        assert status == 201 and created["data_origin"] == "synthetic"
        assert created["moderation_status"] == "approved"
        cid = created["id"]
        status, feed = client.request(base + "/api/workspace/public/complaints")
        item = next(item for item in feed["items"] if item["id"] == cid)
        assert item["text"] == PRIVATE_TEXT and item["has_photo"]
        status, content = client.request(base + f"/api/workspace/public/complaints/{cid}/photo", raw=True)
        assert status == 200 and content == b"\x89PNG\r\n\x1a\n"
        print("PASS 7: explicit demo mode keeps synthetic text and media unchanged")
    finally:
        stop(process)
    process, base = start(db_path, demo=True, auth_disabled=True)
    try:
        status, feed = client.request(base + "/api/workspace/public/complaints")
        assert status == 200 and cid in {item["id"] for item in feed["items"]}
        print("PASS 7b: trusted local demo intake remains public after restart")
    finally:
        stop(process)


def public_demo_flag_check(db_path):
    process, base = start(db_path, demo=True, auth_disabled=False)
    client = Client()
    try:
        status, created = client.request(base + "/api/workspace/intake", "POST", {
            "text": PRIVATE_TEXT, "region_id": "KZ-ALA", "public_consent": True,
        })
        assert status == 201 and created["data_origin"] == "citizen"
        assert created["moderation_status"] == "pending"
        status, feed = client.request(base + "/api/workspace/public/complaints")
        assert created["id"] not in {item["id"] for item in feed["items"]}
        print("PASS 8: demo flag cannot auto-publish intake while production authentication is enabled")
    finally:
        stop(process)


def main():
    with tempfile.TemporaryDirectory() as temp:
        real_mode_check(Path(temp) / "real.db")
        demo_mode_check(Path(temp) / "demo.db")
        public_demo_flag_check(Path(temp) / "public-demo.db")
    print("ALL PRIVACY CHECKS PASSED")


if __name__ == "__main__":
    main()

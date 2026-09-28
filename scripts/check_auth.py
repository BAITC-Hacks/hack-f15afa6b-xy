"""Small end-to-end check for Pulse 109 account and session security."""

from __future__ import annotations

import hashlib
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


class Client:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def cookie(self, name: str) -> str:
        return next(cookie.value for cookie in self.cookies if cookie.name == name)

    def request(self, url: str, method: str = "GET", data: dict | None = None,
                headers: dict | None = None):
        request_headers = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return response.status, payload, response.headers


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(client: Client, base_url: str) -> None:
    for _ in range(50):
        try:
            if client.request(base_url + "/api/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(.2)
    raise RuntimeError("Pulse 109 auth check server did not start")


def run() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "auth.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "DATABASE_PATH": str(db_path),
            "PYTHONPATH": str(root),
            "P109_AUTH_ATTEMPTS": "3",
            "P109_AUTH_WINDOW_SECONDS": "900",
            "P109_SIGNUP_INVITE": "",
            "P109_SECURE_COOKIES": "0",
        })
        env.pop("P109_AUTH_DISABLED", None)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        anonymous = Client()
        try:
            wait_for_server(anonymous, base_url)

            assert anonymous.request(base_url + "/api/auth/me")[0] == 401
            assert anonymous.request(base_url + "/api/workspace/queue")[0] == 401
            assert anonymous.request(base_url + "/map")[0] == 200
            voice_status, voice_page, voice_headers = anonymous.request(base_url + "/voice")
            assert voice_status == 200 and "ГОЛОСОВОЕ ОБРАЩЕНИЕ" in voice_page["raw"]
            assert voice_headers["Permissions-Policy"] == "camera=(), microphone=(self), geolocation=(self)"
            status, voice_health, _ = anonymous.request(base_url + "/api/voice/health")
            assert status == 200 and voice_health["status"] == "disabled"
            status, voice_ai, _ = anonymous.request(base_url + "/api/voice/analyze", "POST", {
                "text": "Во всем доме нет воды", "language": "ru", "region_id": "KZ-ALA",
            })
            assert status == 200 and voice_ai["category"] == "water_supply"
            status, city_data, _ = anonymous.request(base_url + "/api/workspace/cities")
            assert status == 200 and city_data["count"] > 80
            status, public_map, _ = anonymous.request(base_url + "/api/workspace/public/complaints")
            assert status == 200 and public_map["count"] == len(public_map["items"])
            assert public_map["data_origin"] == "synthetic" and all(
                item["id"].startswith("syn-") for item in public_map["items"]
            )
            status, subscriptions, _ = anonymous.request(
                base_url + "/api/workspace/public/subscriptions/test-user"
            )
            assert status == 200 and subscriptions["delivery"] == "in_app"
            assert anonymous.request(base_url + "/api/workspace/public/complaints/missing/video")[0] == 404
            status, config, _ = anonymous.request(base_url + "/api/auth/config")
            assert status == 200 and config["signup_available"] and config["first_account"]
            print("PASS 1: public map and voice intake are anonymous; operator data remains protected")

            valid_password = "correct horse battery staple"
            invalid_signups = (
                {"name": " ", "email": "blank-name@example.kz", "password": valid_password},
                {"name": "Ayan", "email": "not-an-email", "password": valid_password},
                {"name": "Ayan", "email": "short@example.kz", "password": "too-short"},
            )
            for payload in invalid_signups:
                assert anonymous.request(base_url + "/api/auth/signup", "POST", payload)[0] == 422
            print("PASS 2: signup rejects blank names, invalid email addresses and short passwords")

            account = Client()
            status, signup, signup_headers = account.request(
                base_url + "/api/auth/signup",
                "POST",
                {"name": "Ayan Operator", "email": "  AYAN@Example.KZ ", "password": valid_password},
            )
            assert status == 201, (status, signup)
            assert signup["user"]["email"] == "ayan@example.kz"
            assert signup["user"]["role"] == "admin"
            session_token = account.cookie("pulse109_session")
            csrf_token = account.cookie("pulse109_csrf")
            cookie_lines = signup_headers.get_all("Set-Cookie") or []
            session_cookie = next(line for line in cookie_lines if line.startswith("pulse109_session="))
            csrf_cookie = next(line for line in cookie_lines if line.startswith("pulse109_csrf="))
            assert all(part in session_cookie.lower() for part in ("httponly", "samesite=lax", "path=/"))
            assert "httponly" not in csrf_cookie.lower()
            assert "samesite=lax" in csrf_cookie.lower() and "path=/" in csrf_cookie.lower()

            with sqlite3.connect(db_path) as connection:
                password_hash = connection.execute(
                    "SELECT password_hash FROM auth_users WHERE email = ?", ("ayan@example.kz",)
                ).fetchone()[0]
                token_hash, stored_csrf = connection.execute(
                    "SELECT token_hash, csrf_hash FROM auth_sessions WHERE user_id = ?",
                    (signup["user"]["id"],),
                ).fetchone()
            assert password_hash.startswith("scrypt$") and valid_password not in password_hash
            assert token_hash == hashlib.sha256(session_token.encode()).hexdigest()
            assert stored_csrf == hashlib.sha256(csrf_token.encode()).hexdigest()
            assert session_token not in (token_hash, stored_csrf)
            print("PASS 3: first signup creates an admin with scrypt password and hashed session secrets")

            status, closed_config, _ = anonymous.request(base_url + "/api/auth/config")
            assert status == 200 and not closed_config["signup_available"] and not closed_config["first_account"]
            duplicate_status, _, _ = anonymous.request(
                base_url + "/api/auth/signup", "POST",
                {"name": "Duplicate", "email": "ayan@example.kz", "password": valid_password},
            )
            second_status, _, _ = anonymous.request(
                base_url + "/api/auth/signup", "POST",
                {"name": "Second", "email": "second@example.kz", "password": valid_password},
            )
            assert duplicate_status == 409 and second_status == 403
            print("PASS 4: registration closes after bootstrap; duplicate and uninvited accounts are rejected")

            status, current, _ = account.request(base_url + "/api/auth/me")
            assert status == 200 and current["user"]["email"] == "ayan@example.kz"
            status, queue, _ = account.request(base_url + "/api/workspace/queue")
            assert status == 200 and queue["items"]
            complaint_id = next(
                item["complaint"]["id"]
                for item in queue["items"]
                if item["complaint"]["decision_status"] == "pending"
            )
            csrf_path = f"/api/complaints/{complaint_id}/classify"
            assert account.request(base_url + csrf_path, "POST", {})[0] == 403
            status, _, _ = account.request(
                base_url + csrf_path, "POST", {}, {"X-CSRF-Token": csrf_token}
            )
            assert status == 200
            status, clarified, _ = account.request(
                f"{base_url}/api/complaints/{complaint_id}/clarification",
                "POST",
                {"reason": "other", "question": "Уточните детали.", "actor": "spoofed-operator"},
                {"X-CSRF-Token": csrf_token},
            )
            assert status == 200, (status, clarified)
            event = next(item for item in clarified["events"] if item["event_type"] == "clarification_requested")
            assert event["actor"] == signup["user"]["id"]
            print("PASS 5: session grants access, CSRF protects writes and audit actors cannot be spoofed")

            status, logout, _ = account.request(
                base_url + "/api/auth/logout", "POST", {}, {"X-CSRF-Token": csrf_token}
            )
            assert status == 200 and logout == {"ok": True}
            assert account.request(base_url + "/api/auth/me")[0] == 401
            with sqlite3.connect(db_path) as connection:
                revoked_at = connection.execute(
                    "SELECT revoked_at FROM auth_sessions WHERE token_hash = ?", (token_hash,)
                ).fetchone()[0]
            assert revoked_at
            replay = Client()
            replay_status, _, _ = replay.request(
                base_url + "/api/auth/me",
                headers={"Cookie": f"pulse109_session={session_token}; pulse109_csrf={csrf_token}"},
            )
            assert replay_status == 401
            print("PASS 6: logout revokes the server-side session and a copied cookie cannot be replayed")

            wrong_status, wrong_body, _ = anonymous.request(
                base_url + "/api/auth/login", "POST",
                {"email": "ayan@example.kz", "password": "wrong password value"},
            )
            unknown_status, unknown_body, _ = anonymous.request(
                base_url + "/api/auth/login", "POST",
                {"email": "unknown@example.kz", "password": "wrong password value"},
            )
            assert wrong_status == unknown_status == 401 and wrong_body == unknown_body
            fresh = Client()
            status, login, _ = fresh.request(
                base_url + "/api/auth/login", "POST",
                {"email": "AYAN@example.kz", "password": valid_password},
            )
            assert status == 200 and login["user"]["email"] == "ayan@example.kz"
            assert fresh.cookie("pulse109_session") != session_token
            print("PASS 7: login errors do not reveal accounts and successful login rotates the session")

            limiter = Client()
            statuses = [
                limiter.request(
                    base_url + "/api/auth/login", "POST",
                    {"email": "flood@example.kz", "password": "wrong password value"},
                )[0]
                for _ in range(5)
            ]
            assert 429 in statuses, statuses
            print("PASS 8: repeated login failures are rate limited")
            print("ALL AUTH CHECKS PASSED")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    run()

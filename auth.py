"""Small server-side authentication layer for the operator workspace."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


SESSION_COOKIE = "pulse109_session"
CSRF_COOKIE = "pulse109_csrf"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_MAXMEM = 64 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_actor: ContextVar[str] = ContextVar("pulse109_actor", default="anonymous")


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    invite_code: Optional[str] = Field(default=None, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


def current_actor() -> str:
    return _actor.get()


def init_auth(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'operator')),
            created_at INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            csrf_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            FOREIGN KEY (user_id) REFERENCES auth_users(id)
        );
        CREATE INDEX IF NOT EXISTS auth_sessions_user_id ON auth_sessions(user_id);
        CREATE TABLE IF NOT EXISTS auth_attempts (
            attempt_key TEXT NOT NULL,
            attempted_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS auth_attempts_key_time ON auth_attempts(attempt_key, attempted_at);
        """
    )


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=PASSWORD_MAXMEM
    )
    return "scrypt$32768$8$1${}${}".format(
        base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt, validate=True),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
            maxmem=PASSWORD_MAXMEM,
        )
        return hmac.compare_digest(digest, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = _hash_password("invalid-password-for-timing")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.fullmatch(value):
        raise HTTPException(422, "Enter a valid email address")
    return value


def _user(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["full_name"], "email": row["email"], "role": row["role"]}


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _attempt_keys(request: Request, email: str) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    route_ip = f"{request.url.path}|{ip}"
    return _token_hash(route_ip), _token_hash(f"{route_ip}|{email}")


def _rate_limited(get_connection: Callable[[], sqlite3.Connection], keys: tuple[str, str]) -> bool:
    cutoff = int(time.time()) - _env_int("P109_AUTH_WINDOW_SECONDS", 900)
    with get_connection() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE attempted_at < ?", (cutoff,))
        counts = [
            conn.execute("SELECT COUNT(*) FROM auth_attempts WHERE attempt_key = ?", (key,)).fetchone()[0]
            for key in keys
        ]
    limit = _env_int("P109_AUTH_ATTEMPTS", 10)
    return counts[0] >= limit * 5 or counts[1] >= limit


def _record_failure(get_connection: Callable[[], sqlite3.Connection], keys: tuple[str, str]) -> None:
    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO auth_attempts (attempt_key, attempted_at) VALUES (?, ?)",
            [(key, int(time.time())) for key in keys],
        )


def _clear_failures(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM auth_attempts WHERE attempt_key = ?", (key,))


def _create_session(conn: sqlite3.Connection, user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = int(time.time())
    conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, csrf_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_token_hash(token), user_id, _token_hash(csrf), now, now + SESSION_SECONDS),
    )
    return token, csrf


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or os.environ.get("P109_SECURE_COOKIES") == "1"


def _set_session_cookies(response: JSONResponse, request: Request, token: str, csrf: str) -> None:
    secure = _secure_cookie(request)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_SECONDS, path="/", secure=secure, httponly=True, samesite="lax"
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=SESSION_SECONDS, path="/", secure=secure, httponly=False, samesite="lax"
    )


def _load_session(
    get_connection: Callable[[], sqlite3.Connection], token: Optional[str]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if not token:
        return None, None
    token_hash = _token_hash(token)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.full_name, u.role, s.csrf_hash
            FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.disabled = 0
            """,
            (token_hash, int(time.time())),
        ).fetchone()
    return (dict(row), token_hash) if row else (None, None)


def _is_public(request: Request) -> bool:
    path, method = request.url.path, request.method
    if method == "OPTIONS" or path in {
        "/api/health",
        "/api/regions",
        "/api/topics",
        "/api/voice/health",
        "/api/workspace/cities",
        "/api/auth/config",
        "/api/auth/login",
        "/api/auth/signup",
    }:
        return True
    if method == "GET" and path in {"/api/workspace/city-map", "/api/workspace/geocode"}:
        return True
    if method == "POST" and path in {
        "/api/intake", "/api/workspace/intake", "/api/voice/transcribe", "/api/voice/speak",
        "/api/voice/analyze"
    }:
        return True
    if method == "GET" and re.fullmatch(r"/api/workspace/tracking/[^/]+", path):
        return True
    if method == "GET" and (path == "/api/workspace/public/complaints" or
                            re.fullmatch(r"/api/workspace/public/complaints/[^/]+/(?:photo|video)", path) or
                            re.fullmatch(r"/api/workspace/public/subscriptions/[^/]+", path)):
        return True
    if method == "POST" and (path == "/api/workspace/public/similar" or
                             re.fullmatch(r"/api/workspace/public/complaints/[^/]+/subscribe", path)):
        return True
    return bool(method == "POST" and re.fullmatch(r"/api/workspace/incidents/[^/]+/subscribe", path))


def _security_headers(response, path: str = ""):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=(self)"
    if path.startswith("/api/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response


def install_auth(app: FastAPI, get_connection: Callable[[], sqlite3.Connection]) -> None:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/config")
    def auth_config(request: Request):
        with get_connection() as conn:
            first = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0] == 0
        invite = bool(os.environ.get("P109_SIGNUP_INVITE"))
        disabled = os.environ.get("P109_AUTH_DISABLED") == "1"
        return {
            "signup_available": first or invite,
            "first_account": first,
            "invite_required": not first,
            "auth_required": not disabled,
            "user": request.state.auth_user,
        }

    @router.post("/signup", status_code=201)
    def signup(req: SignupRequest, request: Request):
        email = _email(req.email)
        name = " ".join(req.name.split())
        if not name:
            raise HTTPException(422, "Name cannot be blank")
        keys = _attempt_keys(request, email)
        if _rate_limited(get_connection, keys):
            raise HTTPException(429, "Too many attempts. Try again later")

        password_hash = _hash_password(req.password)
        conn = get_connection()
        denied: Optional[tuple[int, str]] = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            first = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0] == 0
            invite = os.environ.get("P109_SIGNUP_INVITE")
            if conn.execute("SELECT 1 FROM auth_users WHERE email = ?", (email,)).fetchone():
                denied = (409, "An account with this email already exists")
            elif not first and not invite:
                denied = (403, "Signup is not available")
            elif not first and not hmac.compare_digest(req.invite_code or "", invite or ""):
                denied = (403, "Invalid invite code")
            else:
                user_id = f"usr-{secrets.token_hex(8)}"
                try:
                    conn.execute(
                        "INSERT INTO auth_users (id, email, full_name, password_hash, role, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, email, name, password_hash, "admin" if first else "operator", int(time.time())),
                    )
                except sqlite3.IntegrityError:
                    denied = (409, "An account with this email already exists")
                if not denied:
                    token, csrf = _create_session(conn, user_id)
                    row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
                    _clear_failures(conn, keys[1])
            if denied:
                conn.rollback()
            else:
                conn.commit()
        finally:
            conn.close()
        if denied:
            _record_failure(get_connection, keys)
            raise HTTPException(denied[0], denied[1])
        response = JSONResponse({"user": _user(row)}, status_code=201)
        _set_session_cookies(response, request, token, csrf)
        return response

    @router.post("/login")
    def login(req: LoginRequest, request: Request):
        email = _email(req.email)
        keys = _attempt_keys(request, email)
        if _rate_limited(get_connection, keys):
            raise HTTPException(429, "Too many attempts. Try again later")
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM auth_users WHERE email = ? AND disabled = 0", (email,)).fetchone()
            valid = _verify_password(req.password, row["password_hash"] if row else _DUMMY_PASSWORD_HASH)
            if valid:
                token, csrf = _create_session(conn, row["id"])
                _clear_failures(conn, keys[1])
        if not valid:
            _record_failure(get_connection, keys)
            raise HTTPException(401, "Invalid email or password")
        response = JSONResponse({"user": _user(row)})
        _set_session_cookies(response, request, token, csrf)
        return response

    @router.get("/me")
    def me(request: Request):
        return {"user": request.state.auth_user}

    @router.post("/logout")
    def logout(request: Request):
        token_hash = getattr(request.state, "auth_token_hash", None)
        if token_hash:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                    (int(time.time()), token_hash),
                )
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    app.include_router(router)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        disabled = os.environ.get("P109_AUTH_DISABLED") == "1"
        user: Optional[dict[str, Any]] = None
        token_hash: Optional[str] = None
        if disabled:
            user = {"id": "operator_demo", "name": "Demo operator", "email": "demo@pulse109.local", "role": "admin"}
        elif request.url.path.startswith("/api/"):
            user, token_hash = _load_session(get_connection, request.cookies.get(SESSION_COOKIE))

        protected = request.url.path.startswith("/api/") and not _is_public(request)
        if protected and not user:
            return _security_headers(
                JSONResponse({"detail": "Authentication required"}, status_code=401), request.url.path
            )
        if protected and request.method not in {"GET", "HEAD", "OPTIONS"} and not disabled:
            csrf = request.headers.get("X-CSRF-Token", "")
            cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
            expected = user.get("csrf_hash", "") if user else ""
            if not csrf or not hmac.compare_digest(csrf, cookie_csrf) or not hmac.compare_digest(_token_hash(csrf), expected):
                return _security_headers(
                    JSONResponse({"detail": "Invalid CSRF token"}, status_code=403), request.url.path
                )

        request.state.auth_user = (
            {k: user[k] for k in ("id", "name", "email", "role")} if disabled else _user_from_session(user)
        ) if user else None
        request.state.auth_token_hash = token_hash
        actor_token = _actor.set(user["id"] if user else "anonymous")
        try:
            response = await call_next(request)
        finally:
            _actor.reset(actor_token)
        return _security_headers(response, request.url.path)


def _user_from_session(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "name": user["full_name"], "email": user["email"], "role": user["role"]}

"""Local account authentication, SQLite sessions and request authorization."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

COOKIE_NAME = "finpulse_session"
COOKIE_PATH = "/api"
SESSION_SECONDS = 24 * 60 * 60
IDLE_SECONDS = 60 * 60
RATE_WINDOW_SECONDS = 15 * 60
router = APIRouter(prefix="/api/auth", tags=["Accounts"])


class AuthError(HTTPException):
    def __init__(self, status, message, code, headers=None):
        super().__init__(status, message, headers)
        self.code = code


def iso_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def public_user(row):
    return {"id": row["id"], "username": row["username"], "nickname": row["nickname"], "role": row["role"], "is_active": bool(row["is_active"]), "created_at": iso_time(row["created_at"]), "last_login_at": iso_time(row["last_login_at"]) if row["last_login_at"] else None}


def token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_token(token):
    return hashlib.sha256(("finpulse-csrf:"+token).encode("ascii")).hexdigest()


class AuthStore:
    dialect = "sqlite"
    integrity_errors = (sqlite3.IntegrityError,)
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.hasher = PasswordHasher()
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    nickname TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    bucket TEXT PRIMARY KEY,
                    window_start INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)
            if "role" not in {row["name"] for row in conn.execute("PRAGMA table_info(users)")}:
                conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin'))")
            conn.execute("PRAGMA user_version=2")

    def check_rate_limit(self, limits):
        now = int(self.clock())
        exceeded = None
        with self.transaction() as conn:
            # Lock concrete bucket rows in a stable order. Global expiry deletes
            # inside this transaction caused InnoDB gap-lock deadlocks under load.
            for bucket, maximum in sorted(limits):
                if self.dialect == 'mysql':
                    # Duplicate-key UPDATE takes an exclusive row lock directly;
                    # INSERT IGNORE followed by FOR UPDATE can deadlock on S->X upgrades.
                    conn.execute('INSERT INTO auth_rate_limits VALUES(?,?,0) ON DUPLICATE KEY UPDATE bucket=bucket', (bucket, now))
                else:
                    conn.execute('INSERT OR IGNORE INTO auth_rate_limits VALUES(?,?,0)', (bucket, now))
                lock = ' FOR UPDATE' if self.dialect == 'mysql' else ''
                row = conn.execute("SELECT attempts, window_start FROM auth_rate_limits WHERE bucket=?" + lock, (bucket,)).fetchone()
                if row['window_start'] <= now-RATE_WINDOW_SECONDS:
                    conn.execute('UPDATE auth_rate_limits SET attempts=0,window_start=? WHERE bucket=?', (now, bucket))
                elif row["attempts"] >= maximum:
                    exceeded = RATE_WINDOW_SECONDS-(now-row["window_start"])
                    break
            if exceeded is None:
                for bucket, _ in limits:
                    conn.execute('UPDATE auth_rate_limits SET attempts=attempts+1 WHERE bucket=?', (bucket,))
        if exceeded is not None:
            raise AuthError(429, "尝试次数过多，请稍后再试。", "RATE_LIMITED", {"Retry-After": str(max(1, exceeded))})

    def _create_session(self, conn, user_id, old_token=None):
        now = int(self.clock())
        if old_token and re.fullmatch(r"[A-Za-z0-9_-]{43}", old_token):
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(old_token),))
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (token_hash(token), user_id, now, now+SESSION_SECONDS, now))
        return token, now+SESSION_SECONDS

    def cleanup_expired(self):
        # Standalone autocommit statements; never combine range deletions with
        # concurrent session/bucket inserts in the same transaction.
        now = int(self.clock())
        with self.connection() as conn:
            conn.execute('DELETE FROM sessions WHERE expires_at<=? OR last_seen_at<=?', (now, now-IDLE_SECONDS))
            conn.execute('DELETE FROM auth_rate_limits WHERE window_start<=?', (now-RATE_WINDOW_SECONDS,))

    def register(self, username, nickname, password, old_token=None):
        # Password hashing and external model calls never hold a SQLite write lock.
        hashed = self.hasher.hash(password)
        now, user_id = int(self.clock()), uuid.uuid4().hex
        try:
            with self.transaction() as conn:
                conn.execute("INSERT INTO users(id,username,nickname,password_hash,is_active,created_at,last_login_at,role) VALUES(?,?,?,?,1,?,?,'user')", (user_id, username, nickname, hashed, now, now))
                token, expiry = self._create_session(conn, user_id, old_token)
                row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        except self.integrity_errors:
            raise AuthError(409, "这个用户名已被使用，请换一个。", "USERNAME_TAKEN") from None
        return public_user(row), token, expiry

    def login(self, username, password, old_token=None, expected_role="user"):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        try:
            self.hasher.verify(row["password_hash"] if row else self.dummy_hash, password)
        except (VerificationError, InvalidHashError):
            raise AuthError(401, "用户名或密码不正确。", "INVALID_CREDENTIALS") from None
        if not row:
            raise AuthError(401, "用户名或密码不正确。", "INVALID_CREDENTIALS")
        if not row["is_active"]:
            raise AuthError(403, "这个账号已停用。", "ACCOUNT_DISABLED")
        replacement = self.hasher.hash(password) if self.hasher.check_needs_rehash(row["password_hash"]) else row["password_hash"]
        with self.transaction() as conn:
            fresh = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            if not fresh or not fresh["is_active"]:
                raise AuthError(403, "这个账号已停用。", "ACCOUNT_DISABLED")
            if fresh["role"] != expected_role:
                raise AuthError(403, "请使用与账号身份对应的登录入口。", "LOGIN_ROLE_MISMATCH")
            if fresh["password_hash"] != row["password_hash"]:
                raise AuthError(401, "账号信息已更新，请重新登录。", "INVALID_CREDENTIALS")
            conn.execute("UPDATE users SET last_login_at=?, password_hash=? WHERE id=?", (int(self.clock()), replacement, row["id"]))
            token, expiry = self._create_session(conn, row["id"], old_token)
            fresh = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return public_user(fresh), token, expiry

    def get_session(self, token):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthError(401, "请先登录后再使用 AI 功能。", "AUTH_REQUIRED")
        hashed, now = token_hash(token), int(self.clock())
        with self.connection() as conn:
            row = conn.execute("SELECT users.*, sessions.expires_at, sessions.last_seen_at FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=?", (hashed,)).fetchone()
        if not row or row["expires_at"] <= now or row["last_seen_at"] <= now-IDLE_SECONDS:
            if row:
                self.logout(token)
            raise AuthError(401, "登录状态已过期，请重新登录。", "AUTH_REQUIRED")
        if not row["is_active"]:
            raise AuthError(403, "这个账号已停用。", "ACCOUNT_DISABLED")
        if now-row["last_seen_at"] >= 60:
            with self.connection() as conn:
                conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now, hashed))
        return {"user": public_user(row), "csrf_token": csrf_token(token), "expires_at": iso_time(row["expires_at"])}

    def logout(self, token):
        if token and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            with self.connection() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def list_users(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC, username LIMIT 500").fetchall()
        return [public_user(row) for row in rows]

    def list_audit(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT actor_name,target_name,action,created_at FROM admin_audit ORDER BY created_at DESC,id DESC LIMIT 100").fetchall()
        return [{**dict(row), "created_at": iso_time(row["created_at"])} for row in rows]

    def set_user_active(self, actor_id, target_id, active):
        with self.transaction() as conn:
            # Recheck the actor inside the write transaction as roles may have changed.
            actor = conn.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["role"] != "admin" or not actor["is_active"]:
                raise AuthError(403, "需要管理员权限。", "ADMIN_REQUIRED")
            target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
            if not target:
                raise AuthError(404, "账号不存在。", "USER_NOT_FOUND")
            if target["role"] == "admin":
                raise AuthError(403, "管理员账号由项目负责人维护，不能在这里停用。", "ADMIN_PROTECTED")
            conn.execute("UPDATE users SET is_active=? WHERE id=?", (int(active), target_id))
            if not active:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
            conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, actor_id, actor["username"], target["username"], "enable_user" if active else "disable_user", int(self.clock())))
            target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
        return public_user(target)

    def change_password(self, user_id, current_password, new_password, old_token):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        try:
            self.hasher.verify(row["password_hash"] if row else self.dummy_hash, current_password)
        except (VerificationError, InvalidHashError):
            raise AuthError(401, "当前密码不正确。", "INVALID_PASSWORD") from None
        hashed = self.hasher.hash(new_password)
        with self.transaction() as conn:
            fresh = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not fresh or not fresh["is_active"] or fresh["password_hash"] != row["password_hash"]:
                raise AuthError(401, "账号信息已更新，请重新登录。", "AUTH_REQUIRED")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            token, expiry = self._create_session(conn, user_id, old_token)
        return public_user(fresh), token, expiry


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        if not isinstance(value, str):
            return value
        value = unicodedata.normalize("NFKC", value).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", value):
            raise ValueError("用户名须为3–32位字母、数字、下划线、点或短横线，并以字母或数字开头")
        return value


class Registration(Credentials):
    password: SecretStr = Field(min_length=8, max_length=128)
    nickname: str = Field(min_length=1, max_length=40)

    @field_validator("nickname", mode="before")
    @classmethod
    def normalize_nickname(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(unicodedata.category(c).startswith("C") for c in value):
                raise ValueError("昵称不能含控制字符")
        return value


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)


class UserStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool = Field(strict=True)


def get_store(request: Request):
    return request.app.state.auth_store


def require_same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    configured = os.environ.get("FINPULSE_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    allowed = {expected, *[value.strip().rstrip("/") for value in configured.split(",") if value.strip()]}
    if not origin or origin not in allowed:
        raise AuthError(403, "请求来源不受信任，请从本站页面发起操作。", "ORIGIN_NOT_ALLOWED")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise AuthError(415, "请使用 JSON 格式提交。", "JSON_REQUIRED")


def current_session(request: Request, store=Depends(get_store)):
    return store.get_session(request.cookies.get(COOKIE_NAME))


def authorize_model_request(request: Request, session=Depends(current_session)):
    require_same_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not re.fullmatch(r"[0-9a-f]{64}", supplied) or not hmac.compare_digest(supplied, session["csrf_token"]):
        raise AuthError(403, "登录状态已更新，请刷新页面后重试。", "CSRF_FAILED")
    return session["user"]


def require_admin(session=Depends(current_session)):
    if session["user"]["role"] != "admin":
        raise AuthError(403, "需要管理员权限。", "ADMIN_REQUIRED")
    return session["user"]


def authorize_admin_write(user=Depends(authorize_model_request)):
    if user["role"] != "admin":
        raise AuthError(403, "需要管理员权限。", "ADMIN_REQUIRED")
    return user


def cookie_secure(request):
    configured = os.environ.get("FINPULSE_COOKIE_SECURE")
    return configured == "1" if configured is not None else request.url.scheme == "https"


def finish_login(response, request, user, token, expiry):
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, path=COOKIE_PATH, secure=cookie_secure(request), httponly=True, samesite="strict")
    return {"user": user, "csrf_token": csrf_token(token), "expires_at": iso_time(expiry)}


def rate_limits(request, username, registering=False):
    client = request.client.host if request.client else "unknown"
    action = "register" if registering else "login"
    # Store hashes of the buckets, not raw IP addresses or usernames.
    limits = [(token_hash(f"{action}:ip:{client}"), 40 if registering else 100)]
    if not registering:
        limits.append((token_hash(f"{action}:user:{username}"), 20))
    return limits


@router.post("/register", status_code=201, dependencies=[Depends(require_same_origin)])
def register(data: Registration, request: Request, response: Response, store=Depends(get_store)):
    store.check_rate_limit(rate_limits(request, data.username, registering=True))
    user, token, expiry = store.register(data.username, data.nickname, data.password.get_secret_value(), request.cookies.get(COOKIE_NAME))
    return finish_login(response, request, user, token, expiry)


@router.post("/login", dependencies=[Depends(require_same_origin)])
def login(data: Credentials, request: Request, response: Response, store=Depends(get_store)):
    store.check_rate_limit(rate_limits(request, data.username))
    user, token, expiry = store.login(data.username, data.password.get_secret_value(), request.cookies.get(COOKIE_NAME))
    return finish_login(response, request, user, token, expiry)


@router.post("/admin/login", dependencies=[Depends(require_same_origin)])
def admin_login(data: Credentials, request: Request, response: Response, store=Depends(get_store)):
    store.check_rate_limit(rate_limits(request, data.username))
    user, token, expiry = store.login(data.username, data.password.get_secret_value(), request.cookies.get(COOKIE_NAME), expected_role="admin")
    return finish_login(response, request, user, token, expiry)


@router.post("/password")
def change_password(data: PasswordChange, request: Request, response: Response, user=Depends(authorize_model_request), store=Depends(get_store)):
    store.check_rate_limit([(token_hash("password:"+user["id"]), 10)])
    result = store.change_password(user["id"], data.current_password.get_secret_value(), data.new_password.get_secret_value(), request.cookies.get(COOKIE_NAME))
    return finish_login(response, request, *result)


admin_router = APIRouter(prefix="/api/admin", tags=["Administration"])


@admin_router.get("/users")
def admin_users(response: Response, user=Depends(require_admin), store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    return {"items": store.list_users()}


@admin_router.get("/audit")
def admin_audit(response: Response, user=Depends(require_admin), store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    return {"items": store.list_audit()}


@admin_router.post("/users/{user_id}/status")
def admin_user_status(user_id: str, data: UserStatus, response: Response, actor=Depends(authorize_admin_write), store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    return {"user": store.set_user_active(actor["id"], user_id, data.is_active)}


@router.get("/me")
def me(response: Response, session=Depends(current_session)):
    response.headers["Cache-Control"] = "no-store"
    return session


@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(authorize_model_request), store=Depends(get_store)):
    store.logout(request.cookies.get(COOKIE_NAME))
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, secure=cookie_secure(request), httponly=True, samesite="strict")
    return {"status": "ok"}


async def auth_error_handler(request, exc):
    response = JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)
    response.headers["Cache-Control"] = "no-store"
    if exc.code in ("AUTH_REQUIRED", "ACCOUNT_DISABLED"):
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, secure=cookie_secure(request), httponly=True, samesite="strict")
    return response

"""MySQL auth storage; SQLite remains available for isolated tests/development."""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from .auth import AuthStore


class QueryResult:
    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class MySQLConnection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        # AuthStore statements use fixed SQL and ? placeholders, never user-built SQL.
        with self.raw.cursor() as cursor:
            cursor.execute(sql.replace("?", "%s"), params)
            return QueryResult(cursor.fetchall() if cursor.description else [], cursor.rowcount)


class MySQLAuthStore(AuthStore):
    dialect = "mysql"
    integrity_errors = (pymysql.IntegrityError,)

    def __init__(self, config, clock=time.time):
        super().__init__(Path("."), clock)
        self.config = config

    @contextmanager
    def connection(self):
        # Retry only connection establishment, before any application statement
        # or transaction. Never replay writes after a connection drops mid-query.
        for attempt in range(3):
            try:
                raw = pymysql.connect(
                    host=self.config.get("host", "127.0.0.1"), port=int(self.config.get("port", 13306)),
                    user=self.config["username"], password=self.config["password"],
                    database=self.config.get("name", "finpulse_dev"), charset="utf8mb4",
                    cursorclass=DictCursor, autocommit=True, connect_timeout=5,
                    # Avoid gap locks from expiry cleanup competing with new session inserts.
                    init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
                    read_timeout=15, write_timeout=15,
                )
                break
            except pymysql.OperationalError as exc:
                if attempt == 2 or not exc.args or exc.args[0] not in (2003, 2006, 2013):
                    raise
                time.sleep(2 * (attempt + 1))
        try:
            yield MySQLConnection(raw)
        finally:
            raw.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.raw.begin()
            try:
                yield conn
                conn.raw.commit()
            except BaseException:
                conn.raw.rollback()
                raise

    def initialize(self):
        # The runtime account has CRUD privileges only; schema setup is an admin task.
        with self.connection() as conn:
            conn.execute("SELECT id,role FROM users LIMIT 0")
            conn.execute("SELECT token_hash FROM sessions LIMIT 0")
            conn.execute("SELECT bucket FROM auth_rate_limits LIMIT 0")
            conn.execute("SELECT id FROM admin_audit LIMIT 0")


def create_auth_store(root):
    raw = json.loads((root / "config.local.json").read_text(encoding="utf-8-sig")) if (root / "config.local.json").exists() else {}
    config = raw.get("database", {})
    engine = os.environ.get("FINPULSE_DB_ENGINE", config.get("engine", "sqlite"))
    if engine == "mysql":
        # Never silently fall back to a different database when the shared DB is down.
        return MySQLAuthStore(config)
    if engine != "sqlite":
        raise ValueError("FINPULSE_DB_ENGINE must be mysql or sqlite")
    return AuthStore(Path(os.environ.get("FINPULSE_DB_PATH", root / "data" / "finpulse.db")))

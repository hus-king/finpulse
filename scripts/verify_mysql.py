"""Explicit live integration check. Deletes only its own temporary test records."""
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymysql
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.app import app
from backend.database import create_auth_store


def main():
    credentials = json.loads((ROOT / "data" / "mysql-admin-credentials.local.json").read_text(encoding="utf-8"))
    store = create_auth_store(ROOT)
    store.initialize()
    username = "check_" + uuid.uuid4().hex[:12]
    table = "check_" + uuid.uuid4().hex[:12]
    origin = {"Origin": "http://localhost"}
    with store.connection() as conn:
        roles = conn.execute("SELECT username,role FROM users WHERE username IN ('hsq','szy','zhr','lzk')").fetchall()
        assert len(roles) == 4 and all(row["role"] == "admin" for row in roles)
    for name, password in credentials["mysql_admins"].items():
        with pymysql.connect(host="127.0.0.1", port=13306, user=name, password=password, database="finpulse_dev", autocommit=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE TABLE `{table}` (value INT)")
                try:
                    cursor.execute(f"INSERT INTO `{table}` VALUES (1)")
                    cursor.execute(f"SELECT value FROM `{table}`")
                    assert cursor.fetchone()[0] == 1
                    try:
                        cursor.execute("SELECT User FROM mysql.user")
                    except pymysql.MySQLError as exc:
                        assert exc.args[0] == 1142
                    else:
                        raise AssertionError("Project admin unexpectedly has system account privileges")
                finally:
                    cursor.execute(f"DROP TABLE `{table}`")
        print(f"MySQL administrator {name}: schema CRUD works; system account access denied")
    with store.connection() as conn:
        try:
            conn.execute(f"CREATE TABLE `{table}` (value INT)")
        except pymysql.MySQLError as exc:
            assert exc.args[0] == 1142
        else:
            raise AssertionError("Runtime account unexpectedly has schema privileges")
    print("Runtime account: schema changes denied")
    prefix = "load_" + uuid.uuid4().hex[:8] + "_"
    try:
        def register(index):
            return store.register(f"{prefix}{index:02}", "并发测试", "Concurrency-check-2026!")[0]["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(register, range(20)))
        assert len(set(ids)) == 20
        print("Shared MySQL: 20 concurrent registrations passed")
    finally:
        with store.transaction() as conn:
            conn.execute("DELETE FROM users WHERE username LIKE ?", (prefix+"%",))
    app.state.auth_store = store
    try:
        with TestClient(app, base_url="http://localhost") as client:
            registration = client.post("/api/auth/register", headers=origin, json={"username": username, "nickname": "集成测试", "password": "Integration-only-2026!"})
            assert registration.status_code == 201, registration.status_code
            target = registration.json()["user"]["id"]
            target_token = client.cookies.get("finpulse_session")
            assert registration.json()["user"]["role"] == "user"
            assert client.get("/api/admin/users").status_code == 403
            assert client.post("/api/auth/admin/login", headers=origin, json={"username": username, "password": "Integration-only-2026!"}).status_code == 403
            for name, password in credentials["website_admins"].items():
                if password is None:
                    print(f"Website administrator {name}: existing password preserved; role verified")
                    continue
                login = client.post("/api/auth/admin/login", headers=origin, json={"username": name, "password": password})
                assert login.status_code == 200, login.status_code
                csrf = {**origin, "X-CSRF-Token": login.json()["csrf_token"]}
                assert client.get("/api/admin/users").status_code == 200
                assert client.post(f"/api/admin/users/{target}/status", headers=origin, json={"is_active": False}).status_code == 403
                assert client.post(f"/api/admin/users/{target}/status", headers=csrf, json={"is_active": False}).status_code == 200
                with store.connection() as conn:
                    assert not conn.execute("SELECT token_hash FROM sessions WHERE user_id=?", (target,)).fetchall()
                assert client.post(f"/api/admin/users/{target}/status", headers=csrf, json={"is_active": True}).status_code == 200
                assert client.post("/api/auth/logout", headers=csrf, json={}).status_code == 200
                print(f"Website administrator {name}: login, user management, CSRF, session revocation passed")
    finally:
        with store.transaction() as conn:
            conn.execute("DELETE FROM admin_audit WHERE target_name=?", (username,))
            conn.execute("DELETE FROM users WHERE username=?", (username,))
        del app.state.auth_store
    print("Shared MySQL integration passed; temporary data removed.")


if __name__ == "__main__":
    main()

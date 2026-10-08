import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import AuthError, AuthStore, COOKIE_NAME, SESSION_SECONDS

ORIGIN = {"Origin": "http://localhost"}
PASSWORD = "Local-test-password-2026!"
CHAT = {"messages": [{"role": "user", "content": "连接测试"}]}
ANALYZE = {"title": "测试新闻", "content": "仅用于权限测试", "stock_name": "测试标的"}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = [1800000000]
        self.store = AuthStore(Path(self.directory.name)/"accounts.db", clock=lambda: self.now[0])
        app.state.auth_store = self.store
        self.context = TestClient(app, base_url="http://localhost")
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        del app.state.auth_store
        self.directory.cleanup()

    def register(self, username="tester", **extra):
        return self.client.post("/api/auth/register", headers=ORIGIN, json={"username": username, "nickname": "测试用户", "password": PASSWORD, **extra})

    def auth_headers(self, response):
        return {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}

    def test_guests_can_browse_but_cannot_reach_either_model_call(self):
        self.assertEqual(self.client.get("/api/stocks").status_code, 200)
        with patch("backend.app.completion", new_callable=AsyncMock) as upstream:
            for endpoint, payload in [("/api/chat", CHAT), ("/api/analyze", ANALYZE)]:
                response = self.client.post(endpoint, headers={**ORIGIN, "Authorization": "Bearer forged"}, json=payload)
                self.assertEqual(response.status_code, 401)
            upstream.assert_not_awaited()

    def test_registration_persists_hashes_and_sets_private_cookie(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/api", cookie)
        self.assertIn(f"Max-Age={SESSION_SECONDS}", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("password", json.dumps(response.json()))
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertNotIn(token, response.text)
        with self.store.connection() as conn:
            user = conn.execute("SELECT * FROM users").fetchone()
            session = conn.execute("SELECT * FROM sessions").fetchone()
            self.assertTrue(user["password_hash"].startswith("$argon2id$"))
            self.assertNotEqual(user["password_hash"], PASSWORD)
            self.assertNotEqual(session["token_hash"], token)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["nickname"], "测试用户")

    def test_case_insensitive_unique_username_and_login(self):
        self.assertEqual(self.register("TestER").status_code, 201)
        self.assertEqual(self.register("tester").status_code, 409)
        response = self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "TESTER", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "tester")

    def test_invalid_credentials_do_not_reveal_which_field_is_wrong(self):
        self.register()
        first = self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "tester", "password": "wrong"})
        second = self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "unknown", "password": "wrong"})
        self.assertEqual(first.status_code, 401)
        self.assertEqual(first.json(), second.json())

    def test_csrf_and_origin_checks_precede_model_calls(self):
        response = self.register()
        good_headers = self.auth_headers(response)
        with patch("backend.app.completion", new_callable=AsyncMock) as upstream:
            for headers in [ORIGIN, {**ORIGIN, "X-CSRF-Token": "0"*64}, {**good_headers, "Origin": "https://attacker.example"}, {**good_headers, "Origin": "http://localhost:55025"}]:
                for endpoint, payload in [("/api/chat", CHAT), ("/api/analyze", ANALYZE)]:
                    self.assertEqual(self.client.post(endpoint, headers=headers, json=payload).status_code, 403)
            upstream.assert_not_awaited()

    def test_valid_session_and_csrf_allow_both_model_routes(self):
        response = self.register()
        headers = self.auth_headers(response)
        result = {"content": "连接成功", "model": "test", "elapsed_ms": 1, "usage": {}, "request_id": "test", "source": "test"}
        with patch("backend.app.completion", new=AsyncMock(return_value=result)) as upstream:
            self.assertEqual(self.client.post("/api/chat", headers=headers, json=CHAT).status_code, 200)
            self.assertEqual(upstream.await_count, 1)
        analysis = {"sentiment_score": 0, "summary": "测试摘要", "causal_chain": ["事实", "影响", "预期"], "uncertainty": "仅用于权限测试"}
        with patch("backend.app.completion", new=AsyncMock(return_value={**result, "content": json.dumps({**analysis,'assessment':'neutral','confidence':'low','horizon':'unclear','positive_factors':[],'negative_factors':[],'watch_points':[]})})) as upstream:
            self.assertEqual(self.client.post("/api/analyze", headers=headers, json=ANALYZE).status_code, 200)
            self.assertEqual(upstream.await_count, 1)

    def test_logout_revokes_the_server_session_even_if_cookie_is_replayed(self):
        response = self.register()
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.auth_headers(response), json={}).status_code, 200)
        self.client.cookies.set(COOKIE_NAME, token, path="/api")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_login_rotates_cookie_and_invalidates_the_previous_one(self):
        self.register()
        old = self.client.cookies.get(COOKIE_NAME)
        response = self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "tester", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.client.cookies.get(COOKIE_NAME), old)
        with self.assertRaises(AuthError):
            self.store.get_session(old)

    def test_session_survives_two_days_idle_and_startup_cleanup(self):
        response = self.register()
        token = self.client.cookies.get(COOKIE_NAME)
        self.now[0] += 2 * 24 * 60 * 60
        recreated = AuthStore(self.store.path, clock=lambda: self.now[0])
        recreated.initialize()
        recreated.cleanup_expired()
        app.state.auth_store = recreated
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["id"], response.json()["user"]["id"])
        self.assertEqual(self.client.cookies.get(COOKIE_NAME), token)
        # Recovery does not silently extend the fixed expiration date.
        self.assertEqual(me.json()["expires_at"], response.json()["expires_at"])

    def test_new_browser_client_restores_session_without_another_login(self):
        response = self.register()
        token = self.client.cookies.get(COOKIE_NAME)
        with TestClient(app, base_url="http://localhost") as restored:
            restored.cookies.set(COOKIE_NAME, token, path="/api")
            me = restored.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json(), response.json())

    def test_cleanup_removes_expired_sessions(self):
        self.register()
        self.now[0] += SESSION_SECONDS
        self.store.cleanup_expired()
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_absolute_expiry_is_enforced_even_with_recent_activity(self):
        self.register()
        self.now[0] += SESSION_SECONDS+1
        with self.store.connection() as conn:
            conn.execute("UPDATE sessions SET last_seen_at=?", (self.now[0],))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_disabled_account_cannot_invoke_model(self):
        response = self.register()
        with self.store.connection() as conn:
            conn.execute("UPDATE users SET is_active=0")
        with patch("backend.app.completion", new_callable=AsyncMock) as upstream:
            self.assertEqual(self.client.post("/api/chat", headers=self.auth_headers(response), json=CHAT).status_code, 403)
            upstream.assert_not_awaited()

    def test_sessions_survive_recreating_store(self):
        response = self.register()
        token = self.client.cookies.get(COOKIE_NAME)
        recreated = AuthStore(self.store.path, clock=lambda: self.now[0])
        recreated.initialize()
        self.assertEqual(recreated.get_session(token)["user"]["id"], response.json()["user"]["id"])

    def test_signup_requires_trusted_json_request_and_cannot_set_roles(self):
        body = {"username": "tester", "nickname": "测试", "password": PASSWORD}
        self.assertEqual(self.client.post("/api/auth/register", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/register", headers={"Origin": "http://localhost", "Content-Type": "text/plain"}, content=json.dumps(body)).status_code, 415)
        response = self.register(role="admin")
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(PASSWORD, response.text)
        self.assertEqual(self.register(password="short").status_code, 422)

    def test_rate_limit_persists_and_window_expires(self):
        limits = [("test-bucket", 2)]
        self.store.check_rate_limit(limits)
        self.store.check_rate_limit(limits)
        with self.assertRaises(AuthError) as exc:
            self.store.check_rate_limit(limits)
        self.assertEqual(exc.exception.status_code, 429)
        self.now[0] += 901
        self.store.check_rate_limit(limits)

    def test_untrusted_host_is_rejected(self):
        self.assertEqual(self.client.get("/api/stocks", headers={"Host": "attacker.example"}).status_code, 400)

    def test_twenty_users_can_register_without_database_lock_errors(self):
        def register(index):
            return self.store.register(f"member{index:02}", f"成员{index}", PASSWORD)[0]["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(register, range(20)))
        self.assertEqual(len(set(ids)), 20)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 20)


if __name__ == "__main__":
    unittest.main()

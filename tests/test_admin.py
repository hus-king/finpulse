import unittest
from unittest.mock import AsyncMock, patch

import test_auth as auth_tests
from test_auth import ORIGIN, PASSWORD, CHAT
from backend.auth import COOKIE_NAME


class AdminTests(unittest.TestCase):
    tearDown = auth_tests.AccountTests.tearDown
    register = auth_tests.AccountTests.register
    auth_headers = auth_tests.AccountTests.auth_headers

    def setUp(self):
        auth_tests.AccountTests.setUp(self)
        admin, token, _ = self.store.register("hsq", "管理员", PASSWORD)
        self.store.logout(token)
        self.admin_id = admin["id"]
        with self.store.connection() as conn:
            conn.execute("UPDATE users SET role='admin' WHERE id=?", (self.admin_id,))

    def admin_login(self):
        return self.client.post("/api/auth/admin/login", headers=ORIGIN, json={"username": "hsq", "password": PASSWORD})

    def test_guests_and_ordinary_users_cannot_manage_accounts(self):
        self.assertEqual(self.client.get("/api/admin/users").status_code, 401)
        regular = self.register()
        self.assertEqual(regular.json()["user"]["role"], "user")
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/audit").status_code, 403)
        self.assertEqual(self.client.post(f"/api/admin/users/{self.admin_id}/status", headers=self.auth_headers(regular), json={"is_active": False}).status_code, 403)

    def test_login_portals_enforce_database_role(self):
        self.register()
        old_cookie = self.client.cookies.get(COOKIE_NAME)
        wrong = self.client.post("/api/auth/admin/login", headers=ORIGIN, json={"username": "tester", "password": PASSWORD})
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(self.client.cookies.get(COOKIE_NAME), old_cookie)
        self.assertEqual(self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "hsq", "password": PASSWORD}).status_code, 403)
        correct = self.admin_login()
        self.assertEqual(correct.status_code, 200)
        self.assertEqual(correct.json()["user"]["role"], "admin")
        self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_disable_revokes_sessions_and_audits_actor(self):
        regular = self.register()
        target_id = regular.json()["user"]["id"]
        target_token = self.client.cookies.get(COOKIE_NAME)
        admin = self.admin_login()
        response = self.client.post(f"/api/admin/users/{target_id}/status", headers=self.auth_headers(admin), json={"is_active": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["user"]["is_active"])
        with self.assertRaises(Exception):
            self.store.get_session(target_token)
        audit = self.client.get("/api/admin/audit").json()["items"]
        self.assertEqual((audit[0]["actor_name"], audit[0]["action"]), ("hsq", "disable_user"))
        self.assertEqual(self.client.post(f"/api/admin/users/{target_id}/status", headers=self.auth_headers(admin), json={"is_active": True}).status_code, 200)

    def test_admin_mutations_require_csrf_and_cannot_disable_admins(self):
        admin = self.admin_login()
        endpoint = f"/api/admin/users/{self.admin_id}/status"
        self.assertEqual(self.client.post(endpoint, headers=ORIGIN, json={"is_active": False}).status_code, 403)
        response = self.client.post(endpoint, headers=self.auth_headers(admin), json={"is_active": False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "ADMIN_PROTECTED")
        self.assertEqual(self.client.post(endpoint, headers=self.auth_headers(admin), json={"is_active": False, "role": "admin"}).status_code, 422)

    def test_role_changes_take_effect_without_relogin(self):
        self.admin_login()
        with self.store.connection() as conn:
            conn.execute("UPDATE users SET role='user' WHERE id=?", (self.admin_id,))
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["role"], "user")

    def test_password_change_rotates_all_sessions_and_requires_old_password(self):
        regular = self.register()
        original_token = self.client.cookies.get(COOKIE_NAME)
        headers = self.auth_headers(regular)
        bad = self.client.post("/api/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "Another-password!"})
        self.assertEqual(bad.status_code, 401)
        good = self.client.post("/api/auth/password", headers=headers, json={"current_password": PASSWORD, "new_password": "Another-password!"})
        self.assertEqual(good.status_code, 200)
        self.assertNotEqual(original_token, self.client.cookies.get(COOKIE_NAME))
        with self.assertRaises(Exception):
            self.store.get_session(original_token)
        self.assertEqual(self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "tester", "password": PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", headers=ORIGIN, json={"username": "tester", "password": "Another-password!"}).status_code, 200)

    def test_admins_can_use_model_routes_with_normal_session_checks(self):
        admin = self.admin_login()
        result = {"content": "test", "model": "test", "elapsed_ms": 1, "usage": {}, "request_id": "test", "source": "test"}
        with patch("backend.app.completion", new=AsyncMock(return_value=result)):
            self.assertEqual(self.client.post("/api/chat", headers=self.auth_headers(admin), json=CHAT).status_code, 200)


if __name__ == "__main__":
    unittest.main()

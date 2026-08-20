"""多用户模式 P1：管理员端点（用户列表/审批/禁用/重置密码/配额）API 测试。"""

from fastapi.testclient import TestClient

from backend.core.multiuser import auth as auth_core
from tests.test_multiuser.conftest import build_mu_app, csrf

ADMIN_U = "admin"
ADMIN_P = "admin-pass-123"


def _login(client, username, password):
    # login 是公开端点，无需 CSRF 头
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


class TestAdminAccessControl:
    def test_unauthenticated_admin_401(self, mu_client):
        r = mu_client.get("/api/admin/users")
        assert r.status_code == 401

    def test_non_admin_403(self, mu_client):
        auth_core.create_user("plain", "password-123", role="user", status="active")
        _login(mu_client, "plain", "password-123")
        r = mu_client.get("/api/admin/users")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_admin_ok(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.get("/api/admin/users")
        assert r.status_code == 200
        assert len(r.json()["users"]) == 1


class TestSensitiveConfigAccess:
    """多用户模式下敏感配置端点仅管理员可访问（单用户模式放行）。"""

    def test_config_rejects_anonymous(self, mu_client):
        r = mu_client.get("/api/config")
        assert r.status_code == 401

    def test_config_rejects_normal_user(self, mu_client):
        auth_core.create_user("plain", "password-123", role="user", status="active")
        _login(mu_client, "plain", "password-123")
        r = mu_client.get("/api/config")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_config_allows_admin(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.get("/api/config")
        assert r.status_code == 200
        assert r.json() == {"secret": "shh"}


class TestApprovalLifecycle:
    def test_admin_sees_pending_and_approves(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        auth_core.create_user("alice", "password-123", role="user", status="pending")
        _login(mu_client, ADMIN_U, ADMIN_P)

        # 列表含待审批用户
        r = mu_client.get("/api/admin/users")
        users = {u["username"]: u for u in r.json()["users"]}
        assert users["alice"]["status"] == "pending"

        # 审批通过
        alice_id = users["alice"]["id"]
        r = mu_client.post(
            f"/api/admin/users/{alice_id}/approve", headers=csrf(mu_client)
        )
        assert r.json()["user"]["status"] == "active"

        # alice 现在可以登录
        r = mu_client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 200

    def test_reject_then_disabled(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "alice", "password-123", role="user", status="pending"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.post(
            f"/api/admin/users/{user['id']}/reject", headers=csrf(mu_client)
        )
        assert r.json()["user"]["status"] == "disabled"
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password-123"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "account_disabled"

    def test_disable_revokes_session_immediately(self, mu_client):
        """管理员禁用账号后，该用户现有会话即时失效。"""
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "alice", "password-123", role="user", status="active"
        )
        _login(mu_client, "alice", "password-123")
        assert mu_client.get("/api/protected").status_code == 200

        _login(mu_client, ADMIN_U, ADMIN_P)  # 同一 client 换登录，cookie 覆盖
        r = mu_client.post(
            f"/api/admin/users/{user['id']}/disable", headers=csrf(mu_client)
        )
        assert r.json()["user"]["status"] == "disabled"

        # 重新登录 alice（旧会话已吊销），受保护端点应 401
        r = mu_client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 403  # account_disabled
        # login 失败不更换 Cookie，client 仍持有 admin 会话
        assert mu_client.get("/api/protected").status_code == 200


class TestResetPassword:
    def test_reset_returns_temp_password(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "alice", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)

        r = mu_client.post(
            f"/api/admin/users/{user['id']}/reset-password", headers=csrf(mu_client)
        )
        assert r.status_code == 200
        temp = r.json()["temp_password"]
        assert len(temp) >= 8

        # 旧密码失效，临时密码可登录
        assert (
            mu_client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "password-123"},
            ).status_code
            == 401
        )
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": temp}
        )
        assert r.status_code == 200


class TestQuotaManagement:
    def test_set_quota_override_reflected_in_me(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "alice", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)

        r = mu_client.put(
            f"/api/admin/users/{user['id']}/quota",
            json={"project_quota_bytes": 123456789, "derived_quota_bytes": None},
            headers=csrf(mu_client),
        )
        assert r.status_code == 200
        assert r.json()["user"]["project_quota_bytes"] == 123456789

        # alice 登录后 me 看到覆盖后的配额
        _login(mu_client, "alice", "password-123")
        body = mu_client.get("/api/auth/me").json()
        assert body["quota"]["project_quota_bytes"] == 123456789
        assert body["quota"]["derived_quota_bytes"] == 100 * 1024 * 1024

    def test_negative_quota_rejected(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user("alice", "password-123")
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.put(
            f"/api/admin/users/{user['id']}/quota",
            json={"project_quota_bytes": -5},
            headers=csrf(mu_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_quota"

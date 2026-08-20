"""多用户模式 P1：注册 / 审批 / 登录 / 会话 / CLI 引导的 API 集成测试。

覆盖中间件强制会话认证、CSRF 头校验、权限依赖与完整注册审批登录流程。
"""

from fastapi.testclient import TestClient

from backend.core.multiuser import auth as auth_core
from tests.test_multiuser.conftest import build_mu_app, csrf

ADMIN_U = "admin"
ADMIN_P = "admin-pass-123"


def _make_admin(mu_client, client, username=ADMIN_U, password=ADMIN_P):
    """直接创建管理员并登录，返回登录后的 client。"""
    auth_core.create_user(username, password, role="admin", status="active")
    # login 是公开端点，无需 CSRF 头
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


class TestMiddlewares:
    """中间件：会话强制 + CSRF（多用户模式）。"""

    def test_protected_requires_session(self, mu_client):
        r = mu_client.get("/api/protected")
        assert r.status_code == 401
        assert r.json()["detail"]["error"] == "unauthorized"

    def test_protected_invalid_session(self, mu_client):
        mu_client.cookies.set("latexcellence_session", "invalid-token")
        r = mu_client.get("/api/protected")
        assert r.status_code == 401

    def test_protected_valid_session_ok(self, mu_client):
        _make_admin(mu_client, mu_client)  # 登录后自动携带 Cookie
        r = mu_client.get("/api/protected")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_mutating_requires_csrf(self, mu_client):
        _make_admin(mu_client, mu_client)
        # 不加 CSRF 头的 POST 被拒（403 invalid_csrf），而不是放行
        r = mu_client.post("/api/auth/logout")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "invalid_csrf"

    def test_mutating_with_csrf_ok(self, mu_client):
        _make_admin(mu_client, mu_client)
        r = mu_client.post("/api/auth/logout", headers=csrf(mu_client))
        assert r.status_code == 200

    def test_mutating_wrong_csrf_value_rejected(self, mu_client):
        """请求头与 CSRF Cookie 值不一致时拒绝（双提交校验值而非仅存在性）。"""
        _make_admin(mu_client, mu_client)
        r = mu_client.post(
            "/api/auth/logout", headers={"X-Latexcellence-CSRF": "attacker-value"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "invalid_csrf"

    def test_mutating_non_ascii_csrf_rejected_not_500(self, mu_client):
        """非 ASCII CSRF 头按字节常量时间比较返回 403，而非 TypeError 500。

        线上攻击者可发送含 ≥0x80 字节的请求头（中间件以 latin-1 解码），
        测试用 latin-1 字节等价模拟。"""
        _make_admin(mu_client, mu_client)
        r = mu_client.post(
            "/api/auth/logout",
            headers={"X-Latexcellence-CSRF": "üñïcodé-token".encode("latin-1")},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "invalid_csrf"

    def test_cookie_secure_honors_forwarded_proto(self):
        """反代终止 TLS 时依据 X-Forwarded-Proto 判定 Secure 标志。"""
        from starlette.requests import Request

        from backend.modules.auth.router import _cookie_secure

        def req(scheme, xfp=None):
            headers = []
            if xfp:
                headers.append((b"x-forwarded-proto", xfp.encode()))
            return Request(
                {
                    "type": "http",
                    "scheme": scheme,
                    "server": ("testserver", 80),
                    "headers": headers,
                    "method": "GET",
                    "path": "/",
                    "query_string": b"",
                }
            )

        assert _cookie_secure(req("https")) is True
        assert _cookie_secure(req("http")) is False
        assert _cookie_secure(req("http", "https")) is True
        assert _cookie_secure(req("http", "HTTPS")) is True
        assert _cookie_secure(req("http", "http")) is False

    def test_login_sets_session_and_csrf_cookies(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        r = mu_client.post(
            "/api/auth/login", json={"username": ADMIN_U, "password": ADMIN_P}
        )
        assert r.status_code == 200
        assert mu_client.cookies.get("latexcellence_session")
        assert mu_client.cookies.get("latexcellence_csrf")

    def test_public_endpoints_no_session(self, mu_client):
        assert mu_client.get("/api/health").status_code == 200
        assert mu_client.get("/api/config/status").status_code == 200
        r = mu_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 200  # 注册无需会话

    def test_single_user_mode_register_404(self, mu_env_factory):
        """单用户模式下认证端点返回 404（保持现有行为不受影响）。"""
        mu_env_factory(enabled=False)
        client = TestClient(build_mu_app())
        assert client.get("/api/protected").status_code == 200  # 单用户不强制会话
        assert client.get("/api/config").status_code == 200  # 敏感端点单用户保持放行
        r = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 404
        assert client.get("/api/auth/me").status_code == 404


class TestRegisterApproveLoginFlow:
    def test_no_password_hash_leak(self, mu_client):
        """任何认证/管理接口响应都不应包含 password_hash。"""
        r = mu_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 200
        assert "password_hash" not in r.text
        alice_id = r.json()["user"]["id"]

        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        r = mu_client.post(
            "/api/auth/login", json={"username": ADMIN_U, "password": ADMIN_P}
        )
        assert r.status_code == 200
        assert "password_hash" not in r.text

        r = mu_client.post(
            f"/api/admin/users/{alice_id}/approve", headers=csrf(mu_client)
        )
        assert r.status_code == 200
        assert "password_hash" not in r.text

        r = mu_client.post(
            f"/api/admin/users/{alice_id}/reset-password", headers=csrf(mu_client)
        )
        assert r.status_code == 200
        assert "password_hash" not in r.text

    def test_full_flow(self, mu_client):
        # 1. 注册 -> pending
        r = mu_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "email": "a@x.io"},
        )
        assert r.status_code == 200
        reg = r.json()
        assert reg["user"]["status"] == "pending"
        assert reg["user"]["role"] == "user"
        alice_id = reg["user"]["id"]

        # 2. pending 不能登录
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password-123"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "account_pending"

        # 3. 管理员登录并审批
        _make_admin(mu_client, mu_client)
        r = mu_client.post(
            f"/api/admin/users/{alice_id}/approve", headers=csrf(mu_client)
        )
        assert r.status_code == 200
        assert r.json()["user"]["status"] == "active"

        # 4. alice 登录成功，GetSet-Cookie
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password-123"}
        )
        assert r.status_code == 200
        assert "latexcellence_session" in mu_client.cookies

        # 5. me 返回个人信息 + 配额
        r = mu_client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["username"] == "alice"
        assert body["user"]["role"] == "user"
        assert body["quota"]["project_quota_bytes"] == 500 * 1024 * 1024

    def test_login_wrong_password(self, mu_client):
        auth_core.create_user("alice", "password-123", role="user", status="active")
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": "bad-pass-xyz"}
        )
        assert r.status_code == 401
        assert r.json()["detail"]["error"] == "unauthorized"

    def test_login_invalid_username_same_error(self, mu_client):
        r = mu_client.post(
            "/api/auth/login", json={"username": "missing", "password": "whatever"}
        )
        assert r.status_code == 401

    def test_logout_revokes(self, mu_client):
        _make_admin(mu_client, mu_client)
        r = mu_client.get("/api/protected")
        assert r.status_code == 200
        r = mu_client.post("/api/auth/logout", headers=csrf(mu_client))
        assert r.status_code == 200
        r = mu_client.get("/api/protected")
        assert r.status_code == 401

    def test_register_disabled(self, mu_env_factory):
        mu_env_factory(registration_enabled=False)
        client = TestClient(build_mu_app())
        r = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "registration_disabled"

    def test_register_short_password(self, mu_client):
        r = mu_client.post(
            "/api/auth/register", json={"username": "alice", "password": "short"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_password"

    def test_register_duplicate_username(self, mu_client):
        auth_core.create_user("alice", "password-123")
        r = mu_client.post(
            "/api/auth/register", json={"username": "Alice", "password": "password-123"}
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "username_taken"

    def test_disabled_account_login_rejected(self, mu_client):
        user = auth_core.create_user("alice", "password-123")
        auth_core.set_user_status(user["id"], "disabled")
        r = mu_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password-123"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "account_disabled"


class TestLoginRateLimit:
    def test_rate_limit_429(self, mu_env_factory):
        mu_env_factory(login_limit=3)
        client = TestClient(build_mu_app())
        for _ in range(4):
            r = client.post(
                "/api/auth/login", json={"username": "bob", "password": "bad-pass-xyz"}
            )
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "rate_limited"

    def test_rate_limit_per_username_across_ips(self, mu_env_factory):
        """每用户名全局上限：轮换出口 IP 不能线性放大对单账号的在线爆破。"""
        mu_env_factory(login_limit=3)
        r = None
        for i in range(4):
            client = TestClient(build_mu_app(), client=(f"10.0.0.{i}", 1234))
            r = client.post(
                "/api/auth/login", json={"username": "bob", "password": "bad-pass-xyz"}
            )
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "rate_limited"

    def test_rate_limit_isolated_per_username(self, mu_env_factory):
        """不同用户名的限流计数互不影响。"""
        mu_env_factory(login_limit=3)
        client = TestClient(build_mu_app())
        for _ in range(3):
            r = client.post(
                "/api/auth/login", json={"username": "bob", "password": "bad-pass-xyz"}
            )
        # bob 已达上限，但 carol 不受影响（仍为普通 401 而非 429）
        r = client.post(
            "/api/auth/login", json={"username": "carol", "password": "bad-pass-xyz"}
        )
        assert r.status_code == 401

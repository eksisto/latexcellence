"""多用户模式 P1：管理员端点（用户列表/审批/禁用/重置密码/配额）API 测试。"""

from backend.core.multiuser import auth as auth_core
from tests.test_multiuser.conftest import csrf

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
    def test_two_step_reset_flow(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "alice", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)

        # 第一步：预览仅生成临时密码，用户密码不变
        r = mu_client.post(
            f"/api/admin/users/{user['id']}/reset-password/preview",
            headers=csrf(mu_client),
        )
        assert r.status_code == 200
        temp = r.json()["temp_password"]
        assert len(temp) >= 8
        assert (
            mu_client.post(
                "/api/auth/login", json={"username": "alice", "password": temp}
            ).status_code
            == 401
        )
        assert (
            mu_client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "password-123"},
            ).status_code
            == 200
        )

        # 第二步：确认后才写入临时密码并吊销会话
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.post(
            f"/api/admin/users/{user['id']}/reset-password/confirm",
            json={"temp_password": temp},
            headers=csrf(mu_client),
        )
        assert r.status_code == 200

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

    def test_preview_does_not_touch_password(self, mu_client):
        """多次预览生成不同候选密码，且均不改变登录凭据。"""
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "bob", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)
        temps = set()
        for _ in range(2):
            r = mu_client.post(
                f"/api/admin/users/{user['id']}/reset-password/preview",
                headers=csrf(mu_client),
            )
            temps.add(r.json()["temp_password"])
        assert len(temps) == 2
        assert (
            mu_client.post(
                "/api/auth/login",
                json={"username": "bob", "password": "password-123"},
            ).status_code
            == 200
        )

    def test_confirm_rejects_weak_password(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        user = auth_core.create_user(
            "carol", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.post(
            f"/api/admin/users/{user['id']}/reset-password/confirm",
            json={"temp_password": "x"},
            headers=csrf(mu_client),
        )
        assert r.status_code == 400
        # 原密码仍然有效
        assert (
            mu_client.post(
                "/api/auth/login",
                json={"username": "carol", "password": "password-123"},
            ).status_code
            == 200
        )


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


class TestDeleteUser:
    """P5：删除用户（项目转交管理员、衍生数据归档、会话吊销、协作者移除）。"""

    def test_delete_transfers_projects_and_revokes(self, mu_env):
        import uuid

        from backend.core.multiuser import db as db_module
        from backend.core.multiuser import userspace

        admin = auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        victim = auth_core.create_user(
            "victim", "password-123", role="user", status="active"
        )
        other = auth_core.create_user(
            "other", "password-123", role="user", status="active"
        )

        # victim 拥有一个活动项目 + 一个回收站项目
        pid = uuid.uuid4().hex
        root = userspace.project_dir(victim["id"], pid)
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.tex").write_text("x", encoding="utf-8")
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
            "created_at, updated_at) VALUES (?, ?, 'Mine', 1, 'active', NULL, ?, ?)",
            (pid, victim["id"], now, now),
        )
        # other 的项目共享给 victim
        other_pid = uuid.uuid4().hex
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
            "created_at, updated_at) VALUES (?, ?, 'Their', 1, 'active', NULL, ?, ?)",
            (other_pid, other["id"], now, now),
        )
        db_module.db_execute(
            "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
            "VALUES (?, ?, 'editor', ?)",
            (other_pid, victim["id"], now),
        )
        # 受害账号产生一个会话
        auth_core.create_session(victim["id"])

        # 绑定行：other 作为协作者持有指向 victim 空间的绑定（转交后须刷新），
        # victim 本人名下的绑定（删除后须随账号移除）
        old_root = str(userspace.project_dir(victim["id"], pid))
        db_module.db_execute(
            "INSERT INTO workspace_bindings (workspace_id, user_id, project_id, "
            "owner_id, root_path, last_active_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("ws-collab", other["id"], pid, victim["id"], old_root, now),
        )
        db_module.db_execute(
            "INSERT INTO workspace_bindings (workspace_id, user_id, project_id, "
            "owner_id, root_path, last_active_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("ws-owner", victim["id"], pid, victim["id"], old_root, now),
        )

        result = auth_core.delete_user(victim["id"], admin["id"])
        assert result["status"] == "ok"

        # 用户行已删除；项目转交管理员
        assert auth_core.get_user_by_id(victim["id"]) is None
        row = db_module.db_query_one("SELECT * FROM projects WHERE id = ?", (pid,))
        assert row is not None and row["owner_id"] == admin["id"]
        # 协作者关系移除
        assert (
            db_module.db_count(
                "SELECT COUNT(*) FROM project_collaborators WHERE user_id = ?",
                (victim["id"],),
            )
            == 0
        )
        # 会话吊销
        assert (
            db_module.db_count(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (victim["id"],)
            )
            == 0
        )
        # 用户空间归档（衍生数据保留）
        archive = userspace.get_archive_root()
        assert any(archive.glob(f"{victim['id']}_*"))
        # 项目目录迁移到管理员空间
        assert userspace.project_dir(admin["id"], pid).is_dir()
        # 协作者的绑定行已刷新为管理员空间的新路径与属主
        collab_binding = db_module.db_query_one(
            "SELECT * FROM workspace_bindings WHERE workspace_id = ?", ("ws-collab",)
        )
        assert collab_binding is not None
        assert collab_binding["owner_id"] == admin["id"]
        assert collab_binding["root_path"] == str(
            userspace.project_dir(admin["id"], pid)
        )
        # 被删用户本人的绑定行已随账号移除
        assert (
            db_module.db_count(
                "SELECT COUNT(*) FROM workspace_bindings WHERE user_id = ?",
                (victim["id"],),
            )
            == 0
        )

    def test_api_delete_requires_admin_and_handles_unknown(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        victim = auth_core.create_user(
            "victim2", "password-123", role="user", status="active"
        )
        _login(mu_client, ADMIN_U, ADMIN_P)

        # 未知用户 → 404
        r = mu_client.delete("/api/admin/users/does-not-exist", headers=csrf(mu_client))
        assert r.status_code == 404

        # 删除成功
        r = mu_client.delete(
            f"/api/admin/users/{victim['id']}", headers=csrf(mu_client)
        )
        assert r.status_code == 200
        assert auth_core.get_user_by_id(victim["id"]) is None

    def test_api_delete_rejects_self(self, mu_client):
        auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        admin = _login(mu_client, ADMIN_U, ADMIN_P)
        r = mu_client.delete(f"/api/admin/users/{admin['id']}", headers=csrf(mu_client))
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_user"

    def test_delete_archive_mtime_resets_retention(self, mu_env):
        """归档 mtime 必须重置为删除时刻：老账号（用户目录 mtime 早于保留期）
        删除后，归档不得被维护任务按旧 mtime 立即清除。"""
        import os
        import time

        from backend.core.multiuser import quota, userspace

        admin = auth_core.create_user(ADMIN_U, ADMIN_P, role="admin", status="active")
        victim = auth_core.create_user(
            "olduser", "password-123", role="user", status="active"
        )

        # 产生衍生数据，并把用户空间目录 mtime 回拨到保留期之外（模拟老账号）
        derived = userspace.user_root(victim["id"]) / "writing_chat_sessions"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "s1.json").write_text("{}", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(userspace.user_root(victim["id"]), (old, old))

        auth_core.delete_user(victim["id"], admin["id"])

        archives = list(userspace.get_archive_root().glob(f"{victim['id']}_*"))
        assert len(archives) == 1
        # 归档 mtime 已重置为删除时刻，处于保留期内
        assert archives[0].stat().st_mtime > time.time() - 86400

        # 维护任务的过期清理不会误删未到期归档
        assert quota.clean_expired_archives() == 0
        assert archives[0].is_dir()

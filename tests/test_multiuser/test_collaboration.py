"""多用户模式 P4：权限协作测试（协作者管理 / 转交 / 权限即时生效）。

覆盖内容：
    - GET /api/projects/{id}/permissions 列表（owner 视角；非 owner 403）
    - POST /api/projects/{id}/permissions 按用户名添加 / 更新角色（owner 专属）
    - PUT /api/projects/{id}/permissions/{user_id} 改角色
    - DELETE /api/projects/{id}/permissions/{user_id} 移除协作者，权限即时生效
    - POST /api/projects/{id}/transfer 转交 owner（viewer 保留 / 退出；目录迁移；改名去重）
    - 权限矩阵端到端：viewer 只读 / 写 403；移除后失去访问
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import access_control
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware
from backend.core.workspace_manager import get_workspace_manager
from tests.test_multiuser.conftest import build_mu_env, csrf

PASSWORD = "password-123"


def build_mu_collab_app() -> FastAPI:
    """多用户测试应用：中间件 + 认证 + 项目 + 项目路由。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.project_router import router as project_router
    from backend.modules.projects.router import router as projects_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(project_router)
    app.include_router(projects_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


def _client_for(username: str) -> TestClient:
    """创建独立的 TestClient 并以指定用户登录（避免会话互相覆盖）。"""
    c = TestClient(build_mu_collab_app())
    _login(c, username)
    return c


@pytest.fixture(autouse=True)
def _clean_workspaces():
    yield
    for ws in list(get_workspace_manager().list_workspaces()):
        get_workspace_manager().remove(ws["workspace_id"])


@pytest.fixture
def env(tmp_path):
    """多用户环境 + alice(owner) / bob / carol（均 active）。"""
    from tests.test_multiuser.conftest import _reset_config_state

    env = build_mu_env(tmp_path)
    env["alice"] = auth_core.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    env["bob"] = auth_core.create_user("bob", PASSWORD, role="user", status="active")
    env["carol"] = auth_core.create_user(
        "carol", PASSWORD, role="user", status="active"
    )
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def alice() -> TestClient:
    return _client_for("alice")


def _make_project(env, user_id: str, name: str, files: dict[str, str]) -> str:
    """直接落地一个活动项目（不走上传），返回 project_id。"""
    project_id = uuid.uuid4().hex
    root = userspace.project_dir(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    now = db_module.utcnow()
    size = sum((root / r).stat().st_size for r in files)
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (project_id, user_id, name, size, now, now),
    )
    return project_id


# ── 权限列表 ─────────────────────────────────────────────────────
class TestPermissionsList:
    def test_owner_lists_permissions(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.get(f"/api/projects/{pid}/permissions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["owner"]["username"] == "alice"
        assert body["collaborators"] == []

    def test_add_then_list(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "editor"},
            headers=csrf(alice),
        )
        r = alice.get(f"/api/projects/{pid}/permissions")
        collabs = r.json()["collaborators"]
        assert len(collabs) == 1
        assert collabs[0]["username"] == "bob"
        assert collabs[0]["role"] == "editor"

    def test_non_owner_cannot_list(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        bob = _client_for("bob")
        r = bob.get(f"/api/projects/{pid}/permissions")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_requires_login(self, env):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = TestClient(build_mu_collab_app()).get(f"/api/projects/{pid}/permissions")
        assert r.status_code == 401


# ── 添加 / 更新 / 移除协作者 ─────────────────────────────────────
class TestCollaboratorCrud:
    def test_add_editor_and_viewer(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "editor"},
            headers=csrf(alice),
        )
        assert r.status_code == 201, r.text
        assert r.json()["collaborator"]["role"] == "editor"
        # 已存在时按最新角色更新
        r2 = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "viewer"},
            headers=csrf(alice),
        )
        assert r2.status_code == 201, r2.text
        assert r2.json()["collaborator"]["role"] == "viewer"
        # 权限矩阵即时反映
        assert (
            access_control.get_user_role_for_project(env["bob"]["id"], pid) == "viewer"
        )
        assert access_control.is_shared_project(pid) is True

    def test_add_invalid_role(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "admin"},
            headers=csrf(alice),
        )
        assert r.status_code == 422  # Pydantic Literal 校验

    def test_add_unknown_user_404(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "ghost", "role": "editor"},
            headers=csrf(alice),
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "user_not_found"

    def test_add_pending_user_rejected(self, env, alice):
        # dave 处于 pending（默认态）
        auth_core.create_user("dave", PASSWORD)  # pending
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "dave", "role": "editor"},
            headers=csrf(alice),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_user"

    def test_add_owner_rejected(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "alice", "role": "viewer"},
            headers=csrf(alice),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_user"

    def test_non_owner_cannot_add(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        bob = _client_for("bob")
        r = bob.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "carol", "role": "editor"},
            headers=csrf(bob),
        )
        assert r.status_code == 403

    def test_update_role(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "editor"},
            headers=csrf(alice),
        )
        r = alice.put(
            f"/api/projects/{pid}/permissions/{env['bob']['id']}",
            json={"role": "viewer"},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        assert r.json()["collaborator"]["role"] == "viewer"
        # 更新非协作者 404
        r2 = alice.put(
            f"/api/projects/{pid}/permissions/{env['carol']['id']}",
            json={"role": "viewer"},
            headers=csrf(alice),
        )
        assert r2.status_code == 404
        # 更新 owner 自身 400
        r3 = alice.put(
            f"/api/projects/{pid}/permissions/{env['alice']['id']}",
            json={"role": "viewer"},
            headers=csrf(alice),
        )
        assert r3.status_code == 400

    def test_remove_collaborator_loses_access_immediately(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "hello"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "viewer"},
            headers=csrf(alice),
        )
        bob = _client_for("bob")
        # 添加为协作者后可以只读访问
        assert bob.get(f"/api/projects/{pid}/download").status_code == 200
        # owner 移除
        r = alice.delete(
            f"/api/projects/{pid}/permissions/{env['bob']['id']}",
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        # 权限即时生效：bob 立即失去访问（下载 403）
        r2 = bob.get(f"/api/projects/{pid}/download")
        assert r2.status_code == 403
        assert r2.json()["detail"]["error"] == "no_permission"
        # 再删不存在 → 404
        r3 = alice.delete(
            f"/api/projects/{pid}/permissions/{env['bob']['id']}",
            headers=csrf(alice),
        )
        assert r3.status_code == 404
        # 移除 owner 自身 → 400
        r4 = alice.delete(
            f"/api/projects/{pid}/permissions/{env['alice']['id']}",
            headers=csrf(alice),
        )
        assert r4.status_code == 400


# ── 转交 ─────────────────────────────────────────────────────────
class TestTransfer:
    def test_transfer_move_failure_reverts_ownership(self, env, alice, monkeypatch):
        """目录迁移失败时回滚 DB 归属（补偿），不产生"文件已搬 / 库未改"失联态。"""
        from backend.modules.projects import service as projects_service

        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})

        def boom(old_root, new_root):
            raise OSError("simulated cross-device move failure")

        monkeypatch.setattr(projects_service, "_move_project_dir", boom)
        # raise_server_exceptions=False：让服务端 500 作为响应返回而非抛出
        c = TestClient(build_mu_collab_app(), raise_server_exceptions=False)
        _login(c, "alice")
        r = c.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob", "keep_access": False},
            headers=csrf(c),
        )
        assert r.status_code == 500
        # DB 归属已回滚：项目仍属 alice、名字不变，目录未被迁移
        row = db_module.db_query_one("SELECT * FROM projects WHERE id = ?", (pid,))
        assert row is not None
        assert row["owner_id"] == env["alice"]["id"]
        assert row["name"] == "P"
        assert userspace.project_dir(env["alice"]["id"], pid).is_dir()
        assert not userspace.project_dir(env["bob"]["id"], pid).exists()

    def test_transfer_keep_access_former_owner_viewer(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "hello world"})
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob", "keep_access": True},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["new_owner_username"] == "bob"
        # DB 层面
        assert (
            access_control.get_user_role_for_project(env["bob"]["id"], pid) == "owner"
        )
        assert (
            access_control.get_user_role_for_project(env["alice"]["id"], pid)
            == "viewer"
        )
        # 目录已迁移到新 owner 空间
        assert not userspace.project_dir(env["alice"]["id"], pid).exists()
        assert (userspace.project_dir(env["bob"]["id"], pid) / "main.tex").exists()
        # bob 登录后可见并拥有
        bob = _client_for("bob")
        projects = bob.get("/api/projects").json()["projects"]
        assert [p["project_id"] for p in projects] == [pid]
        assert projects[0]["role"] == "owner"

    def test_transfer_leave(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "x"})
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob", "keep_access": False},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        # 原 owner 直接退出：无任何角色
        assert access_control.get_user_role_for_project(env["alice"]["id"], pid) is None
        assert (
            access_control.get_user_role_for_project(env["bob"]["id"], pid) == "owner"
        )
        assert userspace.project_dir(env["bob"]["id"], pid).exists()

    def test_transfer_existing_collaborator_promoted(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "x"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "editor"},
            headers=csrf(alice),
        )
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob"},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        # bob 从 editor 转为 owner；alice 退出
        assert (
            access_control.get_user_role_for_project(env["bob"]["id"], pid) == "owner"
        )
        assert access_control.get_user_role_for_project(env["alice"]["id"], pid) is None

    def test_transfer_name_conflict_renames(self, env, alice):
        # bob 已有同名活动项目，转交后自动改名 Doc (2)
        _make_project(env, env["bob"]["id"], "Doc", {"main.tex": "bob's"})
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "alice's"})
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob"},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_name"] == "Doc (2)"
        row = db_module.db_query_one(
            "SELECT name, owner_id FROM projects WHERE id = ?", (pid,)
        )
        assert row["name"] == "Doc (2)"
        assert row["owner_id"] == env["bob"]["id"]

    def test_transfer_errors(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "x"})
        # 转给自己
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "alice"},
            headers=csrf(alice),
        )
        assert r.status_code == 400
        # 不存在用户
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "ghost"},
            headers=csrf(alice),
        )
        assert r.status_code == 404
        # 非 owner 转交 403
        bob = _client_for("bob")
        r = bob.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "carol"},
            headers=csrf(bob),
        )
        assert r.status_code == 403

    def test_transfer_recipient_quota_exceeded(self, env, alice):
        """接收方配额不足时转交被拒（413），目录与归属保持不变。"""
        pid = _make_project(env, env["alice"]["id"], "Big", {"main.tex": "x" * 1024})
        auth_core.set_user_quota(env["bob"]["id"], 100, None)
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob"},
            headers=csrf(alice),
        )
        assert r.status_code == 413, r.text
        assert r.json()["detail"]["error"] == "quota_exceeded"
        # 未迁移、未改归属
        assert userspace.project_dir(env["alice"]["id"], pid).exists()
        row = db_module.db_query_one(
            "SELECT owner_id FROM projects WHERE id = ?", (pid,)
        )
        assert row["owner_id"] == env["alice"]["id"]

    def test_transfer_updates_open_workspace(self, env, alice):
        """转交时若项目已在工作区打开，绑定与文件树同步到新位置。"""
        pid = _make_project(
            env, env["alice"]["id"], "Doc", {"main.tex": "paragraph text"}
        )
        r = alice.post(
            "/api/project/open",
            params={"workspace_id": "ws-transfer"},
            json={"project_id": pid},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        # 转交给 bob（alice 保留 viewer）
        r = alice.post(
            f"/api/projects/{pid}/transfer",
            json={"username": "bob", "keep_access": True},
            headers=csrf(alice),
        )
        assert r.status_code == 200, r.text
        # 工作区绑定已指向新 owner / 新根路径
        binding = get_workspace_manager().get_binding("ws-transfer")
        assert binding["owner_id"] == env["bob"]["id"]
        assert binding["root_path"] == str(userspace.project_dir(env["bob"]["id"], pid))
        # alice（viewer）仍可从原工作区只读访问
        r2 = alice.get(
            "/api/project/paragraphs",
            params={"file_path": "main.tex", "workspace_id": "ws-transfer"},
        )
        assert r2.status_code == 200, r2.text


# ── 端到端权限矩阵 ───────────────────────────────────────────────
class TestPermissionMatrixEndToEnd:
    def test_viewer_read_only_and_forbidden_write(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "hello world"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "viewer"},
            headers=csrf(alice),
        )
        bob = _client_for("bob")
        r = bob.post(
            "/api/project/open",
            params={"workspace_id": "ws-bob"},
            json={"project_id": pid},
            headers=csrf(bob),
        )
        assert r.status_code == 200, r.text
        # viewer 只读
        r2 = bob.get(
            "/api/project/paragraphs",
            params={"file_path": "main.tex", "workspace_id": "ws-bob"},
        )
        assert r2.status_code == 200
        # viewer 写 403
        r3 = bob.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-bob"},
            json={"file_path": "main.tex", "paragraph_index": 0, "new_text": "h"},
            headers=csrf(bob),
        )
        assert r3.status_code == 403
        assert r3.json()["detail"]["error"] == "no_permission"

    def test_editor_can_write(self, env, alice):
        pid = _make_project(env, env["alice"]["id"], "Doc", {"main.tex": "hello world"})
        alice.post(
            f"/api/projects/{pid}/permissions",
            json={"username": "bob", "role": "editor"},
            headers=csrf(alice),
        )
        bob = _client_for("bob")
        bob.post(
            "/api/project/open",
            params={"workspace_id": "ws-bob"},
            json={"project_id": pid},
            headers=csrf(bob),
        )
        r = bob.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-bob"},
            json={"file_path": "main.tex", "paragraph_index": 0, "new_text": "Edited"},
            headers=csrf(bob),
        )
        assert r.status_code == 200, r.text
        path = userspace.project_dir(env["alice"]["id"], pid) / "main.tex"
        assert "Edited" in path.read_text(encoding="utf-8")

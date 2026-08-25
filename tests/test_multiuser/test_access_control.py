"""
多用户模式 P2：用户空间与访问控制测试。

覆盖内容：
    - resolve_user_path() 统一路径解析（越界 / 绝对路径 / '..' / 符号链接）
    - access_control 权限矩阵：owner / editor / viewer / 无关用户
    - workspace 绑定 + 请求级访问依赖（读 / 写）
    - project_router 端点收紧：
        `/open` 多用户模式拒绝 path、仅接受 project_id
        `/browse` 多用户模式返回 403
        viewer 写操作 403、owner/editor 放行
        共享项目撤销/重做解禁（v2 P3 反向补丁，按当前用户栈实时计算）
        未绑定工作区返回 400 invalid_workspace
"""

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import access_control
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware
from backend.core.workspace_manager import get_workspace_manager
from backend.utils.path_guard import resolve_user_path
from tests.test_multiuser.conftest import build_mu_env, csrf

PASSWORD = "password-123"


def build_mu_project_app() -> FastAPI:
    """多用户测试应用：中间件 + project_router + 认证/管理路由。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.project_router import router as project_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(project_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


@pytest.fixture(autouse=True)
def _clean_workspaces():
    """每个测试后清空工作区绑定，避免跨测试污染。"""
    yield
    wm = get_workspace_manager()
    for ws in list(wm.list_workspaces()):
        wm.remove(ws["workspace_id"])


@pytest.fixture
def mu(env):
    return env


@pytest.fixture
def env(tmp_path):
    """多用户环境 + 三个活跃用户 + 一个项目（owner/editor/viewer）。"""
    env = build_mu_env(tmp_path)

    owner = auth_core.create_user("owner", PASSWORD, role="user", status="active")
    editor = auth_core.create_user("editor", PASSWORD, role="user", status="active")
    viewer = auth_core.create_user("viewer", PASSWORD, role="user", status="active")
    outsider = auth_core.create_user("outsider", PASSWORD, role="user", status="active")

    project_id = uuid.uuid4().hex
    env["owner"] = owner
    env["editor"] = editor
    env["viewer"] = viewer
    env["outsider"] = outsider
    env["project_id"] = project_id
    env["project_name"] = "My Project"

    # 创建项目目录（data/users/<owner_id>/projects/<project_id>/）
    proj_root = userspace.project_dir(owner["id"], project_id)
    proj_root.mkdir(parents=True, exist_ok=True)
    (proj_root / "main.tex").write_text(
        r"\documentclass{article}"
        + "\n"
        + r"\begin{document}"
        + "\n"
        + r"First paragraph."
        + "\n"
        + "\n"
        + r"Second paragraph."
        + "\n"
        + r"\end{document}",
        encoding="utf-8",
    )

    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (project_id, owner["id"], env["project_name"], 0, now, now),
    )
    # 协作者
    db_module.db_execute(
        "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
        "VALUES (?, ?, 'editor', ?)",
        (project_id, editor["id"], now),
    )
    db_module.db_execute(
        "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
        "VALUES (?, ?, 'viewer', ?)",
        (project_id, viewer["id"], now),
    )
    yield env
    db_module.close_db()
    from tests.test_multiuser.conftest import _reset_config_state

    _reset_config_state()


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


def _client(env) -> TestClient:
    return TestClient(build_mu_project_app())


# ── resolve_user_path ───────────────────────────────────────────────────────
class TestResolveUserPath:
    def test_resolves_within_project(self, env):
        user_id = env["owner"]["id"]
        project_id = env["project_id"]
        resolved = resolve_user_path(user_id, project_id, "main.tex")
        assert resolved == userspace.project_dir(user_id, project_id) / "main.tex"
        assert resolved.exists()

    def test_id_components_reject_traversal(self, env):
        """user_root / project_dir 对 id 组件做防穿越校验（防御纵深）。"""
        with pytest.raises(ValueError):
            userspace.project_dir(env["owner"]["id"], "../../evil")
        with pytest.raises(ValueError):
            userspace.project_dir("../outside", env["project_id"])
        with pytest.raises(ValueError):
            userspace.project_dir(env["owner"]["id"], "sub/..")
        with pytest.raises(ValueError):
            userspace.user_root("a/b")
        with pytest.raises(ValueError):
            userspace.user_root("")
        # 合法 id 不受影响
        assert userspace.project_dir(env["owner"]["id"], env["project_id"]).is_dir()

    def test_rejects_parent_traversal(self, env):
        with pytest.raises(ValueError):
            resolve_user_path(env["owner"]["id"], env["project_id"], "../main.tex")

    def test_rejects_absolute_path(self, env):
        with pytest.raises(ValueError):
            resolve_user_path(env["owner"]["id"], env["project_id"], str(Path.home()))

    def test_rejects_symlink_escape(self, env, tmp_path):
        user_id = env["owner"]["id"]
        project_id = env["project_id"]
        proj_root = userspace.project_dir(user_id, project_id)
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        link = proj_root / "evil_link.txt"
        link.symlink_to(outside)
        with pytest.raises(ValueError):
            resolve_user_path(user_id, project_id, "evil_link.txt")


# ── access_control 权限矩阵 ────────────────────────────────────────────────
class TestAccessControlMatrix:
    def test_owner_editor_viewer_roles(self, env):
        pid = env["project_id"]
        assert (
            access_control.get_user_role_for_project(env["owner"]["id"], pid) == "owner"
        )
        assert (
            access_control.get_user_role_for_project(env["editor"]["id"], pid)
            == "editor"
        )
        assert (
            access_control.get_user_role_for_project(env["viewer"]["id"], pid)
            == "viewer"
        )
        assert (
            access_control.get_user_role_for_project(env["outsider"]["id"], pid) is None
        )
        # 不存在的项目
        assert (
            access_control.get_user_role_for_project(env["owner"]["id"], "nope") is None
        )

    def test_require_project_access_matrix(self, env):
        pid = env["project_id"]
        # owner/editor 可读可写
        for uid_key in ("owner", "editor"):
            acc = access_control.require_project_access(
                env[uid_key]["id"], pid, write=True
            )
            assert acc["role"] in ("owner", "editor")
        # viewer 可读不可写
        view_acc = access_control.require_project_access(
            env["viewer"]["id"], pid, write=False
        )
        assert view_acc["role"] == "viewer"
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            access_control.require_project_access(env["viewer"]["id"], pid, write=True)
        assert ei.value.status_code == 403
        # 无关用户无权限
        with pytest.raises(HTTPException) as ei:
            access_control.require_project_access(
                env["outsider"]["id"], pid, write=False
            )
        assert ei.value.status_code == 403
        # 不存在的项目 404
        with pytest.raises(HTTPException) as ei:
            access_control.require_project_access(
                env["owner"]["id"], "nope", write=False
            )
        assert ei.value.status_code == 404

    def test_get_projects_for_user(self, env):
        pid = env["project_id"]
        owner_projects = access_control.get_projects_for_user(env["owner"]["id"])
        editor_projects = access_control.get_projects_for_user(env["editor"]["id"])
        viewer_projects = access_control.get_projects_for_user(env["viewer"]["id"])
        outsider_projects = access_control.get_projects_for_user(env["outsider"]["id"])

        assert [p["project_id"] for p in owner_projects] == [pid]
        assert owner_projects[0]["role"] == "owner"
        assert editor_projects[0]["role"] == "editor"
        assert viewer_projects[0]["role"] == "viewer"
        assert outsider_projects == []

    def test_is_shared(self, env):
        assert access_control.is_shared_project(env["project_id"]) is True


# ── /api/project 多用户端点收紧 ────────────────────────────────────────────
class TestProjectRouterMultiuser:
    def _open(self, client, env, project_id=None, use_path=False):
        """打开项目（多用户），返回响应。"""
        body = {}
        if use_path:
            body["path"] = str(
                userspace.project_dir(env["owner"]["id"], env["project_id"])
            )
        elif project_id is not None:
            body["project_id"] = project_id
        return client.post(
            "/api/project/open",
            params={"workspace_id": "ws-1"},
            json=body,
            headers=csrf(client),
        )

    def test_browse_disabled_in_multiuser(self, env):
        client = _client(env)
        _login(client, "owner")
        r = client.get("/api/project/browse", params={"path": "/tmp"})
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_open_rejects_path_in_multiuser(self, env):
        client = _client(env)
        _login(client, "owner")
        r = self._open(client, env, use_path=True)
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "path_disabled"

    def test_open_requires_project_id(self, env):
        client = _client(env)
        _login(client, "owner")
        r = client.post(
            "/api/project/open",
            params={"workspace_id": "ws-1"},
            json={},
            headers=csrf(client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_request"

    def test_open_unknown_project_404(self, env):
        client = _client(env)
        _login(client, "owner")
        r = self._open(client, env, project_id="nope")
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "not_found"

    def test_open_outsider_forbidden(self, env):
        client = _client(env)
        _login(client, "outsider")
        r = self._open(client, env, project_id=env["project_id"])
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_open_then_binding_enforced(self, env):
        """打开后，另一个用户不能复用该 workspace 访问项目。"""
        owner_client = _client(env)
        _login(owner_client, "owner")
        r = self._open(owner_client, env, project_id=env["project_id"])
        assert r.status_code == 200, r.text

        # 另一浏览器/用户使用相同 workspace_id
        outsider_client = _client(env)
        _login(outsider_client, "outsider")
        r2 = outsider_client.get(
            "/api/project/file-tree", params={"workspace_id": "ws-1"}
        )
        assert r2.status_code == 403
        assert r2.json()["detail"]["error"] == "no_permission"

    def test_open_response_hides_root_path(self, env):
        """多用户模式打开响应不得携带服务器上的项目绝对路径（路径约束）。"""
        client = _client(env)
        _login(client, "owner")
        r = self._open(client, env, project_id=env["project_id"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["root_path"] is None
        real_root = str(
            userspace.project_dir(env["owner"]["id"], env["project_id"]).resolve()
        )
        assert real_root not in r.text

    def test_unbound_workspace_invalid(self, env):
        client = _client(env)
        _login(client, "owner")
        r = client.get(
            "/api/project/file-tree", params={"workspace_id": "never-opened"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_workspace"

    def test_reader_requires_session(self, env):
        # 未登录访问受保护项目端点 → 中间件 401
        client = _client(env)
        r = client.get("/api/project/file-tree", params={"workspace_id": "ws-1"})
        assert r.status_code == 401

    def test_open_and_read_paragraphs_as_viewer(self, env):
        client = _client(env)
        _login(client, "viewer")
        r = self._open(client, env, project_id=env["project_id"])
        assert r.status_code == 200, r.text
        r2 = client.get(
            "/api/project/paragraphs",
            params={"file_path": "main.tex", "workspace_id": "ws-1"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["total"] == 2

    def test_viewer_cannot_apply_change(self, env):
        client = _client(env)
        _login(client, "viewer")
        self._open(client, env, project_id=env["project_id"])
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Hacked.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_editor_can_apply_change(self, env):
        client = _client(env)
        _login(client, "editor")
        self._open(client, env, project_id=env["project_id"])
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Edited.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text
        # 文件确实被修改
        path = userspace.project_dir(env["owner"]["id"], env["project_id"]) / "main.tex"
        assert "Edited." in path.read_text(encoding="utf-8")

    def test_demote_to_viewer_takes_effect_immediately(self, env):
        """权限变更即时生效：editor 运行中被降级为 viewer 后，
        同一 workspace 的下一次写请求立即 403（实时查库，无缓存）。"""
        client = _client(env)
        _login(client, "editor")
        self._open(client, env, project_id=env["project_id"])
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Before demotion.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text

        # owner 将 editor 降级为 viewer（直接改库模拟权限变更）
        db_module.db_execute(
            "UPDATE project_collaborators SET role = 'viewer' "
            "WHERE project_id = ? AND user_id = ?",
            (env["project_id"], env["editor"]["id"]),
        )

        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 1,
                "new_text": "After demotion.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"
        # 降级前的写入仍在，降级后的写入未落盘
        path = userspace.project_dir(env["owner"]["id"], env["project_id"]) / "main.tex"
        content = path.read_text(encoding="utf-8")
        assert "Before demotion." in content
        assert "After demotion." not in content

    def test_collaborator_removal_takes_effect_immediately(self, env):
        """权限变更即时生效：协作者被移除后，下一次请求立即 403。"""
        client = _client(env)
        _login(client, "editor")
        self._open(client, env, project_id=env["project_id"])

        db_module.db_execute(
            "DELETE FROM project_collaborators WHERE project_id = ? AND user_id = ?",
            (env["project_id"], env["editor"]["id"]),
        )

        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "After removal.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_owner_can_undo_private_project(self, env):
        """私有项目（无协作者）undo 正常；项目被共享后禁用（v1）。"""
        client = _client(env)
        _login(client, "owner")
        # 创建一个私有项目（无协作者）
        private_id = uuid.uuid4().hex
        root = userspace.project_dir(env["owner"]["id"], private_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.tex").write_text(
            r"\documentclass{article}" + "\n" + r"\begin{document}" + "\n"
            r"Paragraph." + "\n" + r"\end{document}",
            encoding="utf-8",
        )
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, 'Private', 0, 'active', NULL, ?, ?)",
            (private_id, env["owner"]["id"], now, now),
        )
        self._open(client, env, project_id=private_id)
        # 先做一次可撤销修改
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Edited.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text
        r2 = client.post(
            "/api/project/undo",
            params={"workspace_id": "ws-1"},
            json={"file_path": "main.tex"},
            headers=csrf(client),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["undone"] is True

    def test_undo_enabled_for_shared_project(self, env):
        """v2 P3 解禁：共享项目可撤销自己的修改（原 v1 禁令测试反转）。"""
        client = _client(env)
        _login(client, "owner")
        self._open(client, env, project_id=env["project_id"])
        # 先做一次可撤销修改
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Edited.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text
        r2 = client.post(
            "/api/project/undo",
            params={"workspace_id": "ws-1"},
            json={"file_path": "main.tex"},
            headers=csrf(client),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["undone"] is True

    def test_can_undo_true_for_shared_project(self, env):
        """v2 P3 解禁：共享项目 undo-status 按当前用户补丁栈实时计算。"""
        client = _client(env)
        _login(client, "owner")
        self._open(client, env, project_id=env["project_id"])
        # 无编辑时不可撤销
        r0 = client.get(
            "/api/project/undo-status",
            params={"file_path": "main.tex", "workspace_id": "ws-1"},
        )
        assert r0.status_code == 200
        assert r0.json()["can_undo"] is False
        assert r0.json()["can_redo"] is False
        # 有自己的编辑后可撤销（共享状态不再隐藏按钮）
        r = client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-1"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": "Edited.",
            },
            headers=csrf(client),
        )
        assert r.status_code == 200, r.text
        r1 = client.get(
            "/api/project/undo-status",
            params={"file_path": "main.tex", "workspace_id": "ws-1"},
        )
        assert r1.status_code == 200
        assert r1.json()["can_undo"] is True

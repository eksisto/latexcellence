"""编辑器文件树新建/删除接口测试（多用户模式）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖多用户侧验收：owner 创建成功（磁盘落盘 + 响应携带重扫后 file_tree）、
viewer 写保护 403、创建不纳入统一撤销记账（无 workspace_edit_ops 残留）、
项目配额预检超限 413（落盘前拦截）、owner 删除成功。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware

PASSWORD = "password-123"

MAIN_TEX = r"""\documentclass{article}
\begin{document}
Hello world.
\end{document}
"""


@pytest.fixture
def file_ops_env(mu_env):
    """多用户环境 + owner/viewer 用户与一个含 main.tex 的活动项目，
    以及挂载 project_router 的认证测试客户端。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.project_router import router as project_router
    from tests.test_multiuser.conftest import csrf

    owner = auth_core.create_user("owner", PASSWORD, role="user", status="active")
    viewer = auth_core.create_user("viewer", PASSWORD, role="user", status="active")
    project_id = uuid.uuid4().hex
    root = userspace.project_dir(owner["id"], project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
        "deleted_at, created_at, updated_at) "
        "VALUES (?, ?, 'P', 0, 'active', NULL, ?, ?)",
        (project_id, owner["id"], now, now),
    )
    db_module.db_execute(
        "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
        "VALUES (?, ?, 'viewer', ?)",
        (project_id, viewer["id"], now),
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)
    app.include_router(project_router)
    app.include_router(auth_router)
    app.include_router(admin_router)

    def login(client, username):
        r = client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text

    env = dict(mu_env)
    env.update(
        {
            "owner": owner,
            "viewer": viewer,
            "project_id": project_id,
            "root": root,
            "csrf": csrf,
            "client_factory": TestClient,
            "app": app,
            "login": login,
        }
    )
    yield env

    from backend.core.workspace_manager import get_workspace_manager

    wm = get_workspace_manager()
    for ws in list(wm.list_workspaces()):
        wm.remove(ws["workspace_id"])


class FileOpsApi:
    """文件树操作端点的 API 调用小助手（自动附加 CSRF 头）。"""

    def __init__(self, env):
        self.env = env

    def client_for(self, username: str) -> TestClient:
        client = self.env["client_factory"](self.env["app"])
        self.env["login"](client, username)
        return client

    def open(self, client, ws_id):
        return client.post(
            "/api/project/open",
            params={"workspace_id": ws_id},
            json={"project_id": self.env["project_id"]},
            headers=self.env["csrf"](client),
        )

    def create(self, client, ws_id, file_path, content=""):
        return client.post(
            "/api/project/create-file",
            json={
                "file_path": file_path,
                "content": content,
                "workspace_id": ws_id,
            },
            headers=self.env["csrf"](client),
        )

    def delete(self, client, ws_id, file_path):
        return client.post(
            "/api/project/delete-file",
            json={"file_path": file_path, "workspace_id": ws_id},
            headers=self.env["csrf"](client),
        )


@pytest.fixture
def api(file_ops_env):
    return FileOpsApi(file_ops_env)


def _op_rows(env) -> list[dict]:
    return db_module.db_query_all(
        "SELECT * FROM workspace_edit_ops WHERE project_id = ? ORDER BY seq",
        (env["project_id"],),
    )


def _tree_paths(tree):
    paths = []
    for node in tree:
        if node["is_dir"]:
            paths.extend(_tree_paths(node.get("children", [])))
        else:
            paths.append(node["path"])
    return paths


class TestEditorFileOpsMultiuser:
    def test_owner_creates_file_without_edit_log_op(self, api, file_ops_env):
        client = api.client_for("owner")
        ws = "ws-file-ops-create"
        assert api.open(client, ws).status_code == 200
        (file_ops_env["root"] / "chapters").mkdir()

        resp = api.create(client, ws, "chapters/new.tex", "% new\n")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert "chapters/new.tex" in _tree_paths(data["file_tree"])
        assert (file_ops_env["root"] / "chapters" / "new.tex").read_text(
            encoding="utf-8"
        ) == "% new\n"
        # 新建/删除不纳入统一撤销记账：不产生 workspace_edit_ops 记录
        assert _op_rows(file_ops_env) == []

    def test_owner_deletes_file(self, api, file_ops_env):
        client = api.client_for("owner")
        ws = "ws-file-ops-delete"
        assert api.open(client, ws).status_code == 200
        assert api.create(client, ws, "temp.tex").status_code == 200

        resp = api.delete(client, ws, "temp.tex")
        assert resp.status_code == 200, resp.text
        assert "temp.tex" not in _tree_paths(resp.json()["file_tree"])
        assert not (file_ops_env["root"] / "temp.tex").exists()
        assert _op_rows(file_ops_env) == []

    def test_viewer_cannot_create_or_delete(self, api, file_ops_env):
        client = api.client_for("viewer")
        ws = "ws-file-ops-viewer"
        assert api.open(client, ws).status_code == 200

        created = api.create(client, ws, "hijack.tex", "% hijack\n")
        assert created.status_code == 403
        assert created.json()["detail"]["error"] == "no_permission"

        deleted = api.delete(client, ws, "main.tex")
        assert deleted.status_code == 403
        assert deleted.json()["detail"]["error"] == "no_permission"
        # viewer 写保护：磁盘未被改动
        assert (file_ops_env["root"] / "main.tex").exists()
        assert not (file_ops_env["root"] / "hijack.tex").exists()

    def test_create_quota_precheck_rejects_before_write(self, api, file_ops_env):
        # 配额压到极小：任何非空新文件都会在落盘前被预检拒绝
        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = ? WHERE id = ?",
            (10, file_ops_env["owner"]["id"]),
        )
        client = api.client_for("owner")
        ws = "ws-file-ops-quota"
        assert api.open(client, ws).status_code == 200

        resp = api.create(client, ws, "big.tex", "x" * 64)
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "quota_exceeded"
        assert not (file_ops_env["root"] / "big.tex").exists()

    def test_paths_sanitized_in_error_messages(self, api, file_ops_env):
        client = api.client_for("owner")
        ws = "ws-file-ops-sanitize"
        assert api.open(client, ws).status_code == 200

        resp = api.create(client, ws, "main.tex")
        assert resp.status_code == 409
        # 相对路径脱敏：响应任何位置不得出现用户空间绝对路径
        assert str(file_ops_env["root"]) not in resp.text
        assert str(file_ops_env["tmp_path"]) not in resp.text

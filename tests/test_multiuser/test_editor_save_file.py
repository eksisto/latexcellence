"""编辑器整文件读写接口测试（多用户模式，editor-design §5.1 阶段一）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖双模式验收中的多用户侧：打开读取、正常保存（反向补丁记账 op 落表）、
MD5 冲突 409（相对路径脱敏）、保存后 undo 回退恢复原文（redo 对称）、
viewer 写保护、配额超限回滚且编辑日志无残留（方案 §8.4）。
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
First paragraph.

Second paragraph.
\end{document}
"""

EDITED_TEX = MAIN_TEX.replace("First paragraph.", "First paragraph, edited.")


def _md5(text: str) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()


@pytest.fixture
def editor_env(mu_env):
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
    # viewer 以协作者身份可读不可写
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


class EditorApi:
    """编辑器端点的 API 调用小助手（自动附加 CSRF 头）。"""

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

    def get_content(self, client, ws_id, file_path="main.tex"):
        return client.get(
            "/api/project/file-content",
            params={"file_path": file_path, "workspace_id": ws_id},
        )

    def save(self, client, ws_id, content, base_md5=None):
        body = {"file_path": "main.tex", "workspace_id": ws_id, "content": content}
        if base_md5 is not None:
            body["base_md5"] = base_md5
        return client.post(
            "/api/project/save-file", json=body, headers=self.env["csrf"](client)
        )

    def save_with_query_ws(self, client, ws_id, content, base_md5=None):
        """复刻前端 saveFile 的历史请求形状：workspace_id 只拼在 query 上，
        JSON body 不携带（曾触发 invalid_workspace 的回归场景）。"""
        body = {"file_path": "main.tex", "content": content}
        if base_md5 is not None:
            body["base_md5"] = base_md5
        return client.post(
            "/api/project/save-file",
            params={"workspace_id": ws_id},
            json=body,
            headers=self.env["csrf"](client),
        )

    def undo(self, client, ws_id):
        return client.post(
            "/api/project/undo",
            params={"workspace_id": ws_id},
            json={"file_path": "main.tex"},
            headers=self.env["csrf"](client),
        )

    def redo(self, client, ws_id):
        return client.post(
            "/api/project/redo",
            params={"workspace_id": ws_id},
            json={"file_path": "main.tex"},
            headers=self.env["csrf"](client),
        )


@pytest.fixture
def api(editor_env):
    return EditorApi(editor_env)


def _op_rows(env) -> list[dict]:
    return db_module.db_query_all(
        "SELECT * FROM workspace_edit_ops WHERE project_id = ? ORDER BY seq",
        (env["project_id"],),
    )


class TestEditorSaveFileMultiuser:
    def test_open_and_save_records_reverse_patch_op(self, api, editor_env):
        client = api.client_for("owner")
        ws = "ws-editor-mu"
        assert api.open(client, ws).status_code == 200

        opened = api.get_content(client, ws)
        assert opened.status_code == 200
        data = opened.json()
        assert data["content"] == MAIN_TEX
        assert data["md5"] == _md5(MAIN_TEX)

        saved = api.save(client, ws, EDITED_TEX, base_md5=data["md5"])
        assert saved.status_code == 200, saved.text
        assert saved.json()["md5"] == _md5(EDITED_TEX)
        assert (editor_env["root"] / "main.tex").read_text(
            encoding="utf-8"
        ) == EDITED_TEX

        # 统一记账：手动编辑与 AI 改动共享同一套 workspace_edit_ops 历史
        rows = _op_rows(editor_env)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "active"
        assert row["actor_user_id"] == editor_env["owner"]["id"]
        assert row["source_module"] == "editor"
        assert row["file_count"] == 1

    def test_save_accepts_workspace_id_in_query_only(self, api, editor_env):
        """回归：前端 saveFile 曾只把 workspace_id 拼在 query（body 缺失），
        权限依赖从 body 读取导致每次保存 400 invalid_workspace（日志表现为
        workspace=<empty> reason=missing）。依赖回退读 query 后应保存成功。"""
        client = api.client_for("owner")
        ws = "ws-editor-query-ws"
        assert api.open(client, ws).status_code == 200

        saved = api.save_with_query_ws(client, ws, EDITED_TEX, base_md5=_md5(MAIN_TEX))
        assert saved.status_code == 200, saved.text
        assert saved.json()["md5"] == _md5(EDITED_TEX)
        assert (editor_env["root"] / "main.tex").read_text(
            encoding="utf-8"
        ) == EDITED_TEX

    def test_save_without_workspace_id_anywhere_rejected(self, api, editor_env):
        client = api.client_for("owner")
        ws = "ws-editor-no-ws"
        assert api.open(client, ws).status_code == 200

        resp = client.post(
            "/api/project/save-file",
            json={"file_path": "main.tex", "content": EDITED_TEX},
            headers=api.env["csrf"](client),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_workspace"
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX
        assert _op_rows(editor_env) == []

    def test_conflict_returns_409_with_sanitized_message(self, api, editor_env):
        client = api.client_for("owner")
        ws = "ws-editor-conflict"
        assert api.open(client, ws).status_code == 200

        resp = api.save(client, ws, "Conflicting edit.\n", base_md5="deadbeef")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "file_conflict"
        assert detail["file_path"] == "main.tex"
        # 相对路径脱敏：响应任何位置不得出现服务器上的用户空间绝对路径
        assert str(editor_env["root"]) not in resp.text
        assert str(editor_env["tmp_path"]) not in resp.text
        # 磁盘未被改动，也未产生编辑日志 op
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX
        assert _op_rows(editor_env) == []

    def test_save_then_undo_restores_original_and_redo_reapplies(self, api, editor_env):
        client = api.client_for("owner")
        ws = "ws-editor-undo"
        assert api.open(client, ws).status_code == 200
        assert (
            api.save(client, ws, EDITED_TEX, base_md5=_md5(MAIN_TEX)).status_code == 200
        )

        undone = api.undo(client, ws)
        assert undone.status_code == 200, undone.text
        body = undone.json()
        assert body["undone"] is True
        assert body["files"] == ["main.tex"]
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX
        assert all(r["status"] == "undone" for r in _op_rows(editor_env))

        redone = api.redo(client, ws)
        assert redone.status_code == 200, redone.text
        assert redone.json()["redone"] is True
        assert (editor_env["root"] / "main.tex").read_text(
            encoding="utf-8"
        ) == EDITED_TEX
        assert all(r["status"] == "active" for r in _op_rows(editor_env))

    def test_viewer_cannot_save(self, api, editor_env):
        client = api.client_for("viewer")
        ws = "ws-editor-viewer"
        assert api.open(client, ws).status_code == 200

        resp = api.save(client, ws, "Viewer write attempt.\n", base_md5=_md5(MAIN_TEX))
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_permission"
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX

    def test_quota_exceeded_rolls_back_and_discards_op(self, api, editor_env):
        # 配额压到极小：任何增长都会超限
        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = ? WHERE id = ?",
            (10, editor_env["owner"]["id"]),
        )
        client = api.client_for("owner")
        ws = "ws-editor-quota"
        assert api.open(client, ws).status_code == 200

        bigger = EDITED_TEX + "\n% grows the file beyond quota\n"
        resp = api.save(client, ws, bigger, base_md5=_md5(MAIN_TEX))
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "quota_exceeded"
        # 写前内容已直接恢复（不经撤销通道）
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX
        # 撤销栈不被失败写入污染：本次 op 已随回滚一并撤销（§8.4）
        assert _op_rows(editor_env) == []

    def test_corrupted_content_rejected_before_any_write(self, api, editor_env):
        client = api.client_for("owner")
        ws = "ws-editor-corrupt"
        assert api.open(client, ws).status_code == 200

        resp = api.save(client, ws, "Bad:\x0bcontrol\n", base_md5=_md5(MAIN_TEX))
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_content"
        assert _op_rows(editor_env) == []
        assert (editor_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX

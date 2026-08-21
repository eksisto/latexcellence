"""
多用户模式功能模块路由访问控制测试（C1 修复回归）。

覆盖内容（设计 §7.1 / §7.2：所有读写项目文件的入口必须经 access_control 校验）：
    - viewer 通过功能模块写端点（typo-check / code-review / bib-manager /
      writing-chat apply-edit / style-modify agent apply & undo）写共享项目 → 403
    - 无关用户通过功能模块读/写端点访问他人项目 → 403
    - 流式端点（path 参数 workspace_id）同样受保护
    - editor 写操作不被依赖误伤（!= 403）
    - writing-chat /sessions 无 workspace（session-only）流程不被破坏
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
from tests.test_multiuser.conftest import build_mu_env, csrf

PASSWORD = "password-123"


def build_mu_feature_app() -> FastAPI:
    """多用户测试应用：中间件 + project_router + 各功能模块路由 + 认证路由。"""
    from backend.core.security_middleware import AccessSecurityMiddleware as Mw
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.bib_manager.router import router as bib_router
    from backend.modules.code_review.router import router as code_review_router
    from backend.modules.compile.router import router as compile_router
    from backend.modules.expression_opt.router import router as expression_opt_router
    from backend.modules.git_diff.router import router as git_diff_router
    from backend.modules.paragraph_translate.router import (
        router as paragraph_translate_router,
    )
    from backend.modules.plot_gen.router import router as plot_gen_router
    from backend.modules.project_router import router as project_router
    from backend.modules.style_modify.router import router as style_modify_router
    from backend.modules.template_gen.router import router as template_gen_router
    from backend.modules.terminology_check.router import router as terminology_router
    from backend.modules.typo_check.router import router as typo_router
    from backend.modules.writing_chat.router import router as writing_chat_router

    app = FastAPI()
    app.add_middleware(Mw)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(project_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(typo_router)
    app.include_router(code_review_router)
    app.include_router(expression_opt_router)
    app.include_router(paragraph_translate_router)
    app.include_router(terminology_router)
    app.include_router(bib_router)
    app.include_router(compile_router)
    app.include_router(git_diff_router)
    app.include_router(writing_chat_router)
    app.include_router(style_modify_router)
    app.include_router(plot_gen_router)
    app.include_router(template_gen_router)
    return app


@pytest.fixture(autouse=True)
def _clean_workspaces():
    """每个测试后清空工作区绑定，避免跨测试污染。"""
    yield
    wm = get_workspace_manager()
    for ws in list(wm.list_workspaces()):
        wm.remove(ws["workspace_id"])


@pytest.fixture
def env(tmp_path):
    """多用户环境 + owner/editor/viewer/outsider + 一个项目（含 main.tex 与 refs.bib）。"""
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

    proj_root = userspace.project_dir(owner["id"], project_id)
    proj_root.mkdir(parents=True, exist_ok=True)
    (proj_root / "main.tex").write_text(
        r"\documentclass{article}"
        + "\n"
        + r"\begin{document}"
        + "\n"
        + r"First paragraph."
        + "\n"
        + r"\end{document}",
        encoding="utf-8",
    )
    (proj_root / "refs.bib").write_text(
        "@article{key2024,\n  title = {Test},\n}\n", encoding="utf-8"
    )

    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (project_id, owner["id"], "Proj", 0, now, now),
    )
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


def _client(env) -> TestClient:
    return TestClient(build_mu_feature_app())


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


def _open(client: TestClient, env, workspace_id: str, username: str) -> None:
    """以指定用户打开（绑定）项目工作区。"""
    _login(client, username)
    r = client.post(
        "/api/project/open",
        params={"workspace_id": workspace_id},
        json={"project_id": env["project_id"]},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text


class TestFeatureRoutersWriteProtection:
    """viewer 不能通过任何功能模块端点写共享项目（设计 §7.1 要求 403）。"""

    def setup_bindings(self, env):
        """owner 与 viewer 各自（只读地）打开项目，建立独立工作区。"""
        owner_client = _client(env)
        _open(owner_client, env, "ws-owner", "owner")

        viewer_client = _client(env)
        _open(viewer_client, env, "ws-viewer", "viewer")
        return owner_client, viewer_client

    def test_viewer_cannot_write_via_typo_check_apply(self, env):
        _, viewer_client = self.setup_bindings(env)
        body = {
            "file_path": "main.tex",
            "changes": [{"paragraph_index": 0, "new_text": "evil"}],
        }
        r = viewer_client.post(
            "/api/typo-check/apply",
            params={"workspace_id": "ws-viewer"},
            json=body,
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"
        content = (
            userspace.project_dir(env["owner"]["id"], env["project_id"]) / "main.tex"
        ).read_text(encoding="utf-8")
        assert "evil" not in content

    def test_viewer_cannot_write_via_code_review_apply(self, env):
        _, viewer_client = self.setup_bindings(env)
        body = {
            "file_path": "main.tex",
            "changes": [{"paragraph_index": 0, "new_text": "{\\Huge evil}"}],
        }
        r = viewer_client.post(
            "/api/code-review/apply",
            params={"workspace_id": "ws-viewer"},
            json=body,
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_viewer_cannot_write_via_bib_manager_save(self, env):
        _, viewer_client = self.setup_bindings(env)
        body = {
            "file_path": "refs.bib",
            "bibtex_code": "@article{evil2024, title = {Evil}}",
            "cite_key": "evil2024",
        }
        r = viewer_client.post(
            "/api/bib-manager/save",
            params={"workspace_id": "ws-viewer"},
            json=body,
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_viewer_cannot_write_via_writing_chat_apply_edit(self, env):
        _, viewer_client = self.setup_bindings(env)
        body = {
            "session_id": "session-x",
            "edit_message_id": "msg-1",
            "new_latex": "evil",
        }
        r = viewer_client.post(
            "/api/writing-chat/apply-edit",
            params={"workspace_id": "ws-viewer"},
            json=body,
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_viewer_cannot_write_via_style_modify_agent_apply(self, env):
        _, viewer_client = self.setup_bindings(env)
        r = viewer_client.post(
            "/api/style-modify/agent/sessions/s1/apply",
            json={"revision": 1, "workspace_id": "ws-viewer"},
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_viewer_cannot_write_via_style_modify_agent_undo(self, env):
        _, viewer_client = self.setup_bindings(env)
        r = viewer_client.post(
            "/api/style-modify/agent/operations/op1/undo",
            json={"workspace_id": "ws-viewer"},
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/expression-opt/apply",
            "/api/paragraph-translate/apply",
            "/api/terminology-check/apply",
        ],
    )
    def test_viewer_cannot_write_via_paragraph_check_apply_endpoints(self, env, path):
        _, viewer_client = self.setup_bindings(env)
        body = {
            "file_path": "main.tex",
            "changes": [{"paragraph_index": 0, "new_text": "evil"}],
        }
        r = viewer_client.post(
            path,
            params={"workspace_id": "ws-viewer"},
            json=body,
            headers=csrf(viewer_client),
        )
        assert r.status_code == 403, (path, r.status_code)
        assert r.json()["detail"]["error"] == "no_permission"


class TestFeatureRoutersReadProtection:
    """无关用户不能通过功能模块读端点访问他人项目。"""

    def test_outsider_cannot_read_via_typo_check_preview(self, env):
        owner_client = _client(env)
        _open(owner_client, env, "ws-owner", "owner")

        outsider_client = _client(env)
        _login(outsider_client, "outsider")
        # 复用 owner 打开的 workspace：读 + 写均必须拒绝
        for path, params in (
            ("/api/typo-check/preview", {"file_path": "main.tex"}),
            ("/api/bib-manager/list-bib-files", {}),
        ):
            r = outsider_client.get(path, params={"workspace_id": "ws-owner", **params})
            assert r.status_code == 403, (path, r.status_code)
            assert r.json()["detail"]["error"] == "no_permission"

    def test_outsider_cannot_read_via_stream_path_param(self, env):
        owner_client = _client(env)
        _open(owner_client, env, "ws-owner", "owner")

        outsider_client = _client(env)
        _login(outsider_client, "outsider")
        r = outsider_client.get(
            "/api/typo-check/stream/ws-owner",
            params={"file_path": "main.tex"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

        r2 = outsider_client.get(
            "/api/expression-opt/stream/ws-owner",
            params={"file_path": "main.tex"},
        )
        assert r2.status_code == 403

    def test_outsider_cannot_read_compile_pdf(self, env):
        owner_client = _client(env)
        _open(owner_client, env, "ws-owner", "owner")

        outsider_client = _client(env)
        _login(outsider_client, "outsider")
        r = outsider_client.get(
            "/api/compile/pdf",
            params={"main_file": "main.tex", "workspace_id": "ws-owner"},
        )
        assert r.status_code == 403

    def test_outsider_cannot_read_git_diff_refs(self, env):
        owner_client = _client(env)
        _open(owner_client, env, "ws-owner", "owner")

        outsider_client = _client(env)
        _login(outsider_client, "outsider")
        r = outsider_client.get(
            "/api/git-diff/refs", params={"workspace_id": "ws-owner"}
        )
        assert r.status_code == 403


class TestFeatureRoutersAccessMatrix:
    """editor 可写；viewer 可读；session-only 写作助手流程不受影响。"""

    def test_editor_write_not_blocked_by_dependency(self, env):
        editor_client = _client(env)
        _open(editor_client, env, "ws-editor", "editor")
        body = {
            "file_path": "main.tex",
            "changes": [{"paragraph_index": 0, "new_text": "ok"}],
        }
        r = editor_client.post(
            "/api/typo-check/apply",
            params={"workspace_id": "ws-editor"},
            json=body,
            headers=csrf(editor_client),
        )
        # 依赖层放行（非 403）；业务层可能因 marker 缺失等返回 200/400，但不能被权限拒绝
        assert r.status_code != 403

    def test_viewer_can_read_via_feature_read_endpoints(self, env):
        viewer_client = _client(env)
        _open(viewer_client, env, "ws-viewer", "viewer")
        r = viewer_client.get(
            "/api/typo-check/preview",
            params={"file_path": "main.tex", "workspace_id": "ws-viewer"},
        )
        assert r.status_code == 200, r.text

    def test_writing_chat_session_only_flow_not_broken(self, env):
        """无 workspace（session-only）的写作助手端点不能被访问依赖误伤。"""
        client = _client(env)
        _login(client, "viewer")
        r = client.get("/api/writing-chat/sessions")
        assert r.status_code == 200, r.text
        assert r.json() == {"sessions": []}


class TestDerivedQuotaEnforcement:
    """R1：衍生数据配额在会话/输出创建时强制执行（方案 §9.2）。"""

    def test_plot_upload_over_derived_quota_413(self, env):
        """绘图上传：数据文件使衍生用量超限 → 413 quota_exceeded（非流式端点）。"""
        client = _client(env)
        _login(client, "owner")
        # 衍生配额压到 10 字节，上传 20 字节 CSV
        auth_core.set_user_quota(env["owner"]["id"], None, 10)
        csv_bytes = b"x,y\n" + b"1,2\n" * 10
        r = client.post(
            "/api/plot-gen/upload",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
            data={"description": "t", "style": "nature", "language": "zh"},
            headers=csrf(client),
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"

    def test_template_generate_sse_quota_error_event(self, env):
        """模板生成（SSE）会话创建：衍生配额超限 → 流内首事件为结构化 quota 错误。"""
        client = _client(env)
        _login(client, "owner")
        auth_core.set_user_quota(env["owner"]["id"], None, 10)
        # 先占用衍生空间（用量 >= 配额，拒绝新建会话）
        (userspace.derived_dir(env["owner"]["id"], "plots") / "p.bin").write_bytes(
            b"z" * 20
        )
        r = client.post(
            "/api/template-gen/generate",
            json={"description": "A simple report template", "language": "zh"},
            headers=csrf(client),
        )
        assert r.status_code == 200  # SSE 以流式响应返回
        assert '"type": "error"' in r.text
        assert "quota_exceeded" in r.text

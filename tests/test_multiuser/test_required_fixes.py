"""审查 Required#1-#5/#8 修复的回归测试。

覆盖内容：
    - R1 回收站恢复撞名：自动去重为 `xx (2)`，不再 500（IntegrityError 未捕获）
    - R2 上传扩展名前置校验：非法格式 400 invalid_archive，暂存目录零残留
    - R3 落位阶段兜底清理：move 中途失败不留下无 DB 行的孤儿项目目录
    - R4 自定义提示词（全局共享资源）写端点多用户模式仅管理员
    - R5 写盘后配额记账刷新入口 refresh_workspace_project_usage
"""

import asyncio
import io
import json
import uuid
import zipfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend.core import access_control
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.workspace_manager import get_workspace_manager
from tests.test_multiuser.conftest import (
    MINIMAL_CONFIG,
    _reset_config_state,
    build_mu_env,
    csrf,
)
from tests.test_multiuser.test_projects_api import (
    PASSWORD,
    _login,
    _make_project,
    _upload,
    _zip,
    build_mu_projects_app,
)


@pytest.fixture
def env(tmp_path):
    """多用户环境 + 一个活跃用户。"""
    env = build_mu_env(tmp_path)
    env["alice"] = auth_core.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(build_mu_projects_app())


# ── R1：恢复撞名去重 ──────────────────────────────────────────────────────


class TestRestoreNameConflict:
    def test_restore_dedupes_when_name_taken(self, env, client):
        """回收站期间同名活动项目出现，恢复应自动改名 xx (2) 而非 500。"""
        _login(client, "alice")
        pid_old = _make_project(env, env["alice"]["id"], "thesis", {"main.tex": "old"})
        r = client.delete(f"/api/projects/{pid_old}", headers=csrf(client))
        assert r.status_code == 200, r.text

        # 上传同名项目占用名称
        r = _upload(client, _zip({"main.tex": "new"}), name="thesis")
        assert r.status_code == 201, r.text

        r = client.post(f"/api/projects/{pid_old}/restore", headers=csrf(client))
        assert r.status_code == 200, f"restore failed: {r.status_code} {r.text[:300]}"

        # 列表中两个项目并存：原占用者保持 "thesis"，恢复者去重为 "thesis (2)"
        names = [p["name"] for p in client.get("/api/projects").json()["projects"]]
        assert sorted(names) == ["thesis", "thesis (2)"]

    def test_restore_keeps_name_when_free(self, env, client):
        """无撞名时恢复行为不变：保留原名。"""
        _login(client, "alice")
        pid = _make_project(env, env["alice"]["id"], "solo", {"main.tex": "x"})
        client.delete(f"/api/projects/{pid}", headers=csrf(client))
        r = client.post(f"/api/projects/{pid}/restore", headers=csrf(client))
        assert r.status_code == 200, r.text
        names = [p["name"] for p in client.get("/api/projects").json()["projects"]]
        assert names == ["solo"]


# ── R2：上传扩展名前置校验 ────────────────────────────────────────────────


class TestUploadSuffixPreflight:
    def test_unsupported_extension_maps_to_400_without_staging_leak(self, env, client):
        """非 zip/7z 上传返回 400 invalid_archive，且暂存区零残留。"""
        _login(client, "alice")
        r = client.post(
            "/api/projects",
            data={"name": "bad"},
            files={"archive": ("proj.tar.gz", b"not-an-archive", "application/gzip")},
            headers=csrf(client),
        )
        assert (
            r.status_code == 400
        ), f"expected 400, got {r.status_code}: {r.text[:200]}"
        assert r.json()["detail"]["error"] == "invalid_archive"
        assert list(env["staging_root"].iterdir()) == [], "staging dir leaked"


# ── R3：落位失败兜底清理 ──────────────────────────────────────────────────


class TestPlacementFailureCleanup:
    def test_move_failure_leaves_no_orphan_project_dir(
        self, env, tmp_path, monkeypatch
    ):
        """move_into_user_space 中途失败 → 用户空间/暂存区均无残留。"""
        from backend.modules.projects import service as projects_service

        def failing_move(extract_dir, project_root, user_id, project_id):
            # 模拟跨分区复制中途磁盘满：已部分落位后抛 OSError
            dest = userspace.project_dir(user_id, project_id)
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "partial.tex").write_text("partial", encoding="utf-8")
            raise OSError("disk full during move")

        monkeypatch.setattr(projects_service, "move_into_user_space", failing_move)

        own_client = TestClient(build_mu_projects_app(), raise_server_exceptions=False)
        _login(own_client, "alice")
        r = own_client.post(
            "/api/projects",
            files={"archive": ("proj.zip", _zip({"main.tex": "x"}), "application/zip")},
            headers=csrf(own_client),
        )
        assert r.status_code >= 500  # 非业务错误如实上抛

        projects_dir = userspace.user_root(env["alice"]["id"]) / "projects"
        assert list(projects_dir.iterdir()) == [], "orphan project dir left behind"
        assert list(env["staging_root"].iterdir()) == [], "staging dir leaked"


# ── R4：提示词写端点管理员门控 ────────────────────────────────────────────


def _build_prompt_env(tmp_path) -> dict:
    """带 features 提示词目录指向临时路径的多用户环境（避免污染仓库 data/）。"""
    data = json.loads(json.dumps(MINIMAL_CONFIG))
    data["multi_user"] = {
        "enabled": True,
        "db_path": str(tmp_path / "mu.db"),
        "users_root": str(tmp_path / "users") + "/",
        "staging_root": str(tmp_path / "staging") + "/",
    }
    data["features"] = {
        "expression_opt": {
            "custom_prompts_dir": str(tmp_path / "prompts" / "expression") + "/",
        },
        "writing_chat": {
            "custom_prompts_dir": str(tmp_path / "prompts" / "writing_chat") + "/",
        },
    }
    config_path = tmp_path / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    with mock.patch.dict("os.environ", {}, clear=True):
        from backend.core import config as cfg_module

        cfg_module.load_config(str(config_path))
    from backend.core.multiuser import db as db_module

    db_module.init_db()
    return {"tmp_path": tmp_path}


class TestPromptWriteAdminGating:
    """自定义提示词为全局共享资源：多用户模式写操作仅限管理员（R4）。"""

    @pytest.fixture
    def prompt_env(self, tmp_path):
        from tests.test_multiuser.test_feature_router_access import (
            build_mu_feature_app,
        )

        env = _build_prompt_env(tmp_path)
        auth_core.create_user("admin-u", PASSWORD, role="admin", status="active")
        auth_core.create_user("plain-u", PASSWORD, role="user", status="active")

        def client_for(username: str) -> TestClient:
            c = TestClient(build_mu_feature_app())
            r = c.post(
                "/api/auth/login",
                json={"username": username, "password": PASSWORD},
            )
            assert r.status_code == 200, r.text
            return c

        env["client_for"] = client_for
        yield env
        db_module.close_db()
        _reset_config_state()

    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("post", "/api/writing-chat/prompts", {"name": "p1", "content": "c"}),
            ("delete", "/api/writing-chat/prompts/p1", None),
            ("post", "/api/writing-chat/prompts/active", {"name": "p1"}),
            ("post", "/api/expression-opt/prompts", {"name": "p2", "content": "c"}),
            ("delete", "/api/expression-opt/prompts/p2", None),
            ("post", "/api/expression-opt/prompts/active", {"name": "p2"}),
        ],
    )
    def test_plain_user_forbidden(self, prompt_env, method, path, payload):
        c = prompt_env["client_for"]("plain-u")
        kwargs = {"json": payload} if payload is not None else {}
        r = getattr(c, method)(path, headers=csrf(c), **kwargs)
        assert (
            r.status_code == 403
        ), f"{method.upper()} {path}: {r.status_code} {r.text[:200]}"
        assert r.json()["detail"]["error"] in ("no_permission",)

    @pytest.mark.parametrize(
        "path,payload,list_path,name",
        [
            (
                "/api/writing-chat/prompts",
                {"name": "p1", "content": "c"},
                "/api/writing-chat/prompts",
                "p1",
            ),
            (
                "/api/expression-opt/prompts",
                {"name": "p2", "content": "c"},
                "/api/expression-opt/prompts",
                "p2",
            ),
        ],
    )
    def test_admin_allowed(self, prompt_env, path, payload, list_path, name):
        c = prompt_env["client_for"]("admin-u")
        r = c.post(path, json=payload, headers=csrf(c))
        assert r.status_code == 200, f"POST {path}: {r.text[:200]}"
        # 读取对所有登录用户保持开放
        other = prompt_env["client_for"]("plain-u")
        list_r = other.get(list_path)
        assert list_r.status_code == 200
        assert any(p["name"] == name for p in list_r.json()["prompts"])


# ── R5：写盘后记账刷新入口 ────────────────────────────────────────────────


class TestRefreshWorkspaceProjectUsage:
    def _make_env_with_project(self, tmp_path):
        env = build_mu_env(tmp_path)
        user = auth_core.create_user("u5", PASSWORD, role="user", status="active")
        pid = uuid.uuid4().hex
        root = userspace.project_dir(user["id"], pid)
        root.mkdir(parents=True, exist_ok=True)
        (root / "a.tex").write_text("hello world", encoding="utf-8")
        (root / "b.tex").write_text("second file!", encoding="utf-8")
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "deleted_at, created_at, updated_at) VALUES (?, ?, ?, 0, 'active', "
            "NULL, ?, ?)",
            (pid, user["id"], "P5", now, now),
        )
        return env, user, pid, root

    def test_refresh_updates_size_bytes_from_disk(self, tmp_path):
        env, user, pid, root = self._make_env_with_project(tmp_path)
        try:
            wm = get_workspace_manager()
            wm.bind(
                "ws-r5",
                user_id=user["id"],
                project_id=pid,
                owner_id=user["id"],
                root_path=str(root),
            )
            expected = (root / "a.tex").stat().st_size + (root / "b.tex").stat().st_size
            asyncio.run(access_control.refresh_workspace_project_usage("ws-r5"))
            row = db_module.db_query_one(
                "SELECT size_bytes FROM projects WHERE id = ?", (pid,)
            )
            assert row["size_bytes"] == expected
        finally:
            wm = get_workspace_manager()
            wm.remove("ws-r5")
            db_module.close_db()
            _reset_config_state()

    def test_unbound_workspace_is_noop(self, tmp_path):
        env, user, pid, root = self._make_env_with_project(tmp_path)
        try:
            # 未绑定的工作区：静默跳过，不抛错、不改账
            asyncio.run(access_control.refresh_workspace_project_usage("ws-none"))
            row = db_module.db_query_one(
                "SELECT size_bytes FROM projects WHERE id = ?", (pid,)
            )
            assert row["size_bytes"] == 0
        finally:
            db_module.close_db()
            _reset_config_state()

    def test_single_user_mode_is_noop(self, tmp_path):
        build_mu_env(tmp_path, enabled=False)
        try:
            # 单用户模式直接跳过（不查库、不抛错）
            asyncio.run(access_control.refresh_workspace_project_usage("ws-any"))
        finally:
            _reset_config_state()

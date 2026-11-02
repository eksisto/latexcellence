"""多用户模式 P3：项目 API 测试（上传 / 列表 / 下载 / 重命名 / 回收站 / 配额）。

覆盖内容：
    - GET /api/projects 列表（我的 + 被共享，过滤回收站）
    - POST /api/projects 上传创建（zip 两种布局、自动命名 xx (2)、超限 413）
    - PUT /api/projects/{id} 重命名（owner；冲突 409；非 owner 403）
    - DELETE /api/projects/{id} 进回收站
    - POST /api/projects/{id}/restore 恢复（超限禁止 413）
    - DELETE /api/projects/{id}/permanent 永久删除
    - GET /api/projects/{id}/download 打包下载（读权限）
    - apply-change 写回超限 → 413 且回滚
"""

import io
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware
from tests.test_multiuser.conftest import build_mu_env, csrf

PASSWORD = "password-123"


def build_mu_projects_app() -> FastAPI:
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


def _zip(name_content: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in name_content.items():
            z.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    """多用户环境 + 两个活跃用户。"""
    from tests.test_multiuser.conftest import _reset_config_state

    env = build_mu_env(tmp_path)
    env["alice"] = auth_core.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    env["bob"] = auth_core.create_user("bob", PASSWORD, role="user", status="active")
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(build_mu_projects_app())


@pytest.fixture
def alice_client(env, client) -> TestClient:
    _login(client, "alice")
    return client


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


def _upload(client: TestClient, payload: bytes, filename="proj.zip", name=None):
    data = {"name": name} if name is not None else None
    return client.post(
        "/api/projects",
        data=data,
        files={"archive": (filename, payload, "application/zip")},
        headers=csrf(client),
    )


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


# ── 列表 ─────────────────────────────────────────────────────────
class TestListProjects:
    def test_list_empty(self, env, alice_client):
        r = alice_client.get("/api/projects")
        assert r.status_code == 200
        assert r.json()["projects"] == []

    def test_list_owned_and_shared(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "Mine", {"main.tex": "hello"})
        # bob 的项目共享给 alice
        pid2 = _make_project(env, env["bob"]["id"], "Shared", {"main.tex": "x"})
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
            "VALUES (?, ?, 'viewer', ?)",
            (pid2, env["alice"]["id"], now),
        )
        r = alice_client.get("/api/projects")
        names = {p["project_id"]: p for p in r.json()["projects"]}
        assert set(names) == {pid, pid2}
        assert names[pid]["role"] == "owner"
        assert names[pid2]["role"] == "viewer"
        assert names[pid2]["owner_username"] == "bob"

    def test_list_filters_recycle_bin(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "Mine", {"main.tex": "hello"})
        db_module.db_execute(
            "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
            (db_module.utcnow(), pid),
        )
        r = alice_client.get("/api/projects")
        assert r.json()["projects"] == []

    def test_recycle_bin_lists_deleted_only(self, env, alice_client):
        """GET /api/projects/recycle-bin：仅返回当前用户已删除（回收站）项目。"""
        active = _make_project(env, env["alice"]["id"], "Active", {"a.tex": "x"})
        deleted = _make_project(env, env["alice"]["id"], "Deleted", {"b.tex": "y"})
        bob_deleted = _make_project(env, env["bob"]["id"], "BobTrashed", {"c.tex": "z"})
        for pid in (deleted, bob_deleted):
            db_module.db_execute(
                "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
                (db_module.utcnow(), pid),
            )
        r = alice_client.get("/api/projects/recycle-bin")
        assert r.status_code == 200
        names = {p["project_id"]: p for p in r.json()["projects"]}
        # 只含 alice 自己删除的项目，且不包含 bob 的回收站项目
        assert active not in names
        assert deleted in names
        assert bob_deleted not in names
        assert names[deleted]["name"] == "Deleted"

    def test_recycle_bin_requires_login(self, env, client):
        r = client.get("/api/projects/recycle-bin")
        assert r.status_code == 401

    def test_requires_login(self, env, client):
        r = client.get("/api/projects")
        assert r.status_code == 401


# ── 上传 ─────────────────────────────────────────────────────────
class TestUpload:
    def test_upload_zip_top_dir_normalizes(self, env, alice_client):
        payload = _zip({"mydoc/main.tex": "a", "mydoc/chap.tex": "b"})
        r = _upload(alice_client, payload, filename="mydoc.zip")
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        assert proj["name"] == "mydoc"
        root = userspace.project_dir(env["alice"]["id"], proj["project_id"])
        assert (root / "main.tex").exists()
        assert (root / "chap.tex").exists()

    def test_upload_with_custom_name(self, env, alice_client):
        payload = _zip({"main.tex": "a"})
        r = _upload(alice_client, payload, filename="a.zip", name="My 项目")
        assert r.status_code == 201, r.text
        assert r.json()["project"]["name"] == "My 项目"

    def test_same_name_auto_suffix(self, env, alice_client):
        payload = _zip({"main.tex": "a"})
        assert (
            _upload(alice_client, payload, filename="p.zip", name="Doc").status_code
            == 201
        )
        r2 = _upload(alice_client, payload, filename="p.zip", name="Doc")
        assert r2.status_code == 201, r2.text
        assert r2.json()["project"]["name"] == "Doc (2)"

    def test_upload_rejects_path_traversal(self, env, alice_client):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.txt", "x")
        r = _upload(alice_client, buf.getvalue(), filename="evil.zip")
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "archive_unsafe"

    def test_upload_over_quota(self, env, alice_client):
        # 把用户项目配额压到 10 字节，上传 100 字节压缩包 → 413
        auth_core.set_user_quota(env["alice"]["id"], 10, None)
        payload = _zip({"big.tex": "x" * 200})
        r = _upload(alice_client, payload, filename="big.zip")
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"

    def test_upload_requires_login(self, env, client):
        payload = _zip({"main.tex": "a"})
        r = client.post(
            "/api/projects",
            files={"archive": ("a.zip", payload, "application/zip")},
        )
        assert r.status_code == 401


# ── 重命名 ───────────────────────────────────────────────────────
class TestRename:
    def test_owner_rename(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "Old", {"main.tex": "x"})
        r = alice_client.put(
            f"/api/projects/{pid}", json={"name": "New"}, headers=csrf(alice_client)
        )
        assert r.status_code == 200, r.text
        assert r.json()["project"]["name"] == "New"

    def test_rename_conflict(self, env, alice_client):
        _make_project(env, env["alice"]["id"], "First", {"main.tex": "1"})
        pid = _make_project(env, env["alice"]["id"], "Second", {"main.tex": "2"})
        r = alice_client.put(
            f"/api/projects/{pid}", json={"name": "First"}, headers=csrf(alice_client)
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "name_conflict"

    def test_non_owner_cannot_rename(self, env, alice_client, client):
        pid = _make_project(env, env["alice"]["id"], "A", {"main.tex": "x"})
        _login(client, "bob")
        r = client.put(
            f"/api/projects/{pid}", json={"name": "Hijack"}, headers=csrf(client)
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"


# ── 回收站 ───────────────────────────────────────────────────────
class TestRecycleBin:
    def test_delete_restore_permanent(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "x"})
        r = alice_client.delete(f"/api/projects/{pid}", headers=csrf(alice_client))
        assert r.status_code == 200
        assert alice_client.get("/api/projects").json()["projects"] == []
        # 恢复
        r = alice_client.post(
            f"/api/projects/{pid}/restore", headers=csrf(alice_client)
        )
        assert r.status_code == 200
        assert [
            p["project_id"]
            for p in alice_client.get("/api/projects").json()["projects"]
        ] == [pid]
        # 再删 → 永久删
        alice_client.delete(f"/api/projects/{pid}", headers=csrf(alice_client))
        r = alice_client.delete(
            f"/api/projects/{pid}/permanent", headers=csrf(alice_client)
        )
        assert r.status_code == 200
        assert not userspace.project_dir(env["alice"]["id"], pid).exists()

    def test_restore_over_quota_forbidden(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "P", {"data.bin": "x" * 500})
        # 项目 500 字节，配额压制到 100 → 恢复被拒 413
        auth_core.set_user_quota(env["alice"]["id"], 100, None)
        alice_client.delete(f"/api/projects/{pid}", headers=csrf(alice_client))
        r = alice_client.post(
            f"/api/projects/{pid}/restore", headers=csrf(alice_client)
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        # 仍处于回收站
        assert alice_client.get("/api/projects").json()["projects"] == []

    def test_delete_non_owner_forbidden(self, env, alice_client, client):
        pid = _make_project(env, env["alice"]["id"], "A", {"main.tex": "x"})
        _login(client, "bob")
        r = client.delete(f"/api/projects/{pid}", headers=csrf(client))
        assert r.status_code == 403

    def test_restore_non_deleted_invalid(self, env, alice_client):
        pid = _make_project(env, env["alice"]["id"], "A", {"main.tex": "x"})
        r = alice_client.post(
            f"/api/projects/{pid}/restore", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_state"


# ── 下载 ─────────────────────────────────────────────────────────
class TestDownload:
    def test_download_owner_editor_viewer(self, env, alice_client, client):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "hello"})
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
            "VALUES (?, ?, 'editor', ?)",
            (pid, env["bob"]["id"], now),
        )
        _login(client, "bob")
        for c in (alice_client, client):
            r = c.get(f"/api/projects/{pid}/download")
            assert r.status_code == 200
            assert r.content.startswith(b"PK")

    def test_download_outsider_forbidden(self, env, alice_client, client):
        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "hello"})
        _login(client, "bob")
        r = client.get(f"/api/projects/{pid}/download")
        assert r.status_code == 403

    def test_download_failure_hides_absolute_paths(
        self, env, alice_client, monkeypatch
    ):
        """打包失败只回固定错误文案，OSError 携带的服务器路径仅进日志。"""
        from backend.modules.projects import service as projects_service

        pid = _make_project(env, env["alice"]["id"], "P", {"main.tex": "hello"})
        leak = "/tmp/latexcellence-hidden/abcdef/main.tex"

        def boom(source_dir, zip_path):
            raise OSError(f"[Errno 13] Permission denied: '{leak}'")

        monkeypatch.setattr(projects_service, "_build_zip", boom)
        r = alice_client.get(f"/api/projects/{pid}/download")
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "download_failed"
        assert "latexcellence-hidden" not in r.text


# ── 写回超限 ─────────────────────────────────────────────────────
class TestWriteQuota:
    def test_apply_change_over_quota_rolled_back(self, env, alice_client):
        # 小配额：项目本身 5 字节，写回 200 字节 → 超限回滚
        auth_core.set_user_quota(env["alice"]["id"], 100, None)
        root = userspace.project_dir(env["alice"]["id"], "wq")
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.tex").write_text("abcde", encoding="utf-8")
        pid = "wq"
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
            "created_at, updated_at) VALUES (?, ?, 'Tiny', 5, 'active', NULL, ?, ?)",
            (pid, env["alice"]["id"], now, now),
        )
        # 打开并绑定工作区
        r = alice_client.post(
            "/api/project/open",
            params={"workspace_id": "ws-quota"},
            json={"project_id": pid},
            headers=csrf(alice_client),
        )
        assert r.status_code == 200, r.text
        huge = "é" * 300  # 300 字符 UTF-8 → > 100 字节
        r = alice_client.post(
            "/api/project/apply-change",
            params={"workspace_id": "ws-quota"},
            json={
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_text": huge,
            },
            headers=csrf(alice_client),
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        # 文件被回滚，内容保持不变
        assert (root / "main.tex").read_text(encoding="utf-8") == "abcde"

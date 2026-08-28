"""多用户模式：上传自动 Git 初始化 + .git 配额豁免与独立上限。

覆盖内容：
    - zip 上传（无 .git）→ 自动出现 .git + Initial commit + .gitignore
    - 功能开关关闭时不自动初始化
    - 自带 .git 的包不重复 init（提交数不变、.gitignore 不被覆盖）
    - size_bytes 不含 .git 且与对账口径（project_size_on_disk）一致
    - .git 超独立上限 → 413 git_history_too_large，项目不落库、目录清场
"""

import asyncio
import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from backend.core import config as cfg_module
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import quota as quota_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware
from backend.modules.git_manage import service as git_manage_service
from backend.modules.projects import service as projects_service
from tests.test_multiuser.conftest import build_mu_env, csrf

PASSWORD = "password-123"


def build_mu_upload_app() -> FastAPI:
    """多用户测试应用：中间件 + 认证 + 项目路由（上传链路）。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.projects.router import router as projects_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(projects_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


def _zip_bytes(name_content: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in name_content.items():
            z.writestr(name, content)
    return buf.getvalue()


def _zip_dir_with_git(repo: Path) -> bytes:
    """把含 .git 的目录整体打包（模拟用户上传自带版本库的项目）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(repo.rglob("*")):
            if p.is_file():
                z.writestr(p.relative_to(repo).as_posix(), p.read_bytes())
    return buf.getvalue()


def _make_local_git_repo(repo: Path, files: dict[str, str], message: str) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    env_extra = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory"}
    env = {**os.environ, **env_extra, "GIT_CONFIG_VALUE_0": str(repo)}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), "add", "-A"],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo}",
            "-c",
            "user.name=Uploader",
            "-c",
            "user.email=up@example.com",
            "-C",
            str(repo),
            "commit",
            "-qm",
            message,
        ],
        check=True,
        env=env,
    )


def _git_commit_count(root: Path) -> int:
    r = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
    )
    return int(r.stdout.strip()) if r.returncode == 0 else 0


async def _upload_via_service(user_id: str, payload: bytes, filename: str) -> dict:
    """直接驱动整包上传服务并等待自动初始化后台任务结束（确定性）。"""
    uf = UploadFile(file=io.BytesIO(payload), filename=filename)
    result = await projects_service.upload_create_project({"id": user_id}, uf)
    await git_manage_service.wait_auto_init_tasks()
    return result


@pytest.fixture
def env(tmp_path):
    """多用户环境（sandbox=local 避免依赖 Docker）+ 一个活跃用户。"""
    from tests.test_multiuser.conftest import _reset_config_state

    env = build_mu_env(tmp_path, sandbox_extra={"mode": "local"})
    env["alice"] = auth_core.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    yield env
    db_module.close_db()
    _reset_config_state()


# ── 上传自动初始化 ───────────────────────────────────────────────
class TestUploadAutoInit:
    def test_zip_upload_auto_inits_git(self, env):
        payload = _zip_bytes({"main.tex": "hello\n", "chapters/a.tex": "A\n"})
        result = asyncio.run(
            _upload_via_service(env["alice"]["id"], payload, "proj.zip")
        )
        pid = result["project"]["project_id"]
        root = userspace.project_dir(env["alice"]["id"], pid)

        assert (root / ".git").is_dir()
        assert (root / ".gitignore").is_file()
        assert "*.aux" in (root / ".gitignore").read_text(encoding="utf-8")
        assert _git_commit_count(root) == 1
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--pretty=%s"],
            capture_output=True,
            text=True,
        )
        assert r.stdout.strip() == "Initial commit"

    def test_auto_init_disabled_by_feature_flag(self, env, monkeypatch):
        monkeypatch.setattr(
            git_manage_service.get_config().features.git_manage,
            "enabled",
            False,
        )
        payload = _zip_bytes({"main.tex": "hello\n"})
        result = asyncio.run(
            _upload_via_service(env["alice"]["id"], payload, "proj.zip")
        )
        pid = result["project"]["project_id"]
        root = userspace.project_dir(env["alice"]["id"], pid)
        assert not (root / ".git").exists()


# ── 自带 .git 的包 ───────────────────────────────────────────────
class TestUploadWithExistingGit:
    def test_existing_git_not_reinited(self, env, tmp_path):
        repo = tmp_path / "src"
        _make_local_git_repo(repo, {"main.tex": "v1\n"}, "original work")
        (repo / ".gitignore").write_text("custom\n", encoding="utf-8")
        (repo / "main.tex").write_text("v2\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Uploader",
                "-c",
                "user.email=up@example.com",
                "-C",
                str(repo),
                "commit",
                "-qm",
                "second",
            ],
            check=True,
            capture_output=True,
        )
        payload = _zip_dir_with_git(repo)

        result = asyncio.run(
            _upload_via_service(env["alice"]["id"], payload, "proj.zip")
        )
        pid = result["project"]["project_id"]
        root = userspace.project_dir(env["alice"]["id"], pid)

        assert (root / ".git").is_dir()
        # 不重复 init：提交数保持 2，原 .gitignore 原样保留
        assert _git_commit_count(root) == 2
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--pretty=%s"],
            capture_output=True,
            text=True,
        )
        assert r.stdout.splitlines() == ["second", "original work"]
        assert (root / ".gitignore").read_text(encoding="utf-8") == "custom\n"


# ── .git 配额豁免 ────────────────────────────────────────────────
class TestGitQuotaExemption:
    def test_size_bytes_excludes_git_and_matches_reconcile(self, env, tmp_path):
        repo = tmp_path / "src"
        _make_local_git_repo(repo, {"main.tex": "v1\n"}, "work")
        payload = _zip_dir_with_git(repo)

        result = asyncio.run(
            _upload_via_service(env["alice"]["id"], payload, "proj.zip")
        )
        pid = result["project"]["project_id"]
        root = userspace.project_dir(env["alice"]["id"], pid)

        row = db_module.db_query_one(
            "SELECT size_bytes FROM projects WHERE id = ?", (pid,)
        )
        assert row is not None
        expected = quota_module.dir_size(root, exclude_dirs={".git"})
        assert expected < quota_module.dir_size(root)  # .git 体积确实被排除
        assert row["size_bytes"] == expected
        # 对账口径一致（.git 豁免后不会把 size_bytes 改大）
        assert quota_module.project_size_on_disk(pid) == row["size_bytes"]


# ── .git 独立上限 ────────────────────────────────────────────────
class TestGitDirCap:
    def test_upload_git_over_cap_rejected_413(self, env, monkeypatch):
        """整包上传：.git 超上限 → 413 git_history_too_large，项目不落库。"""
        alice = env["alice"]
        repo_repo = env["tmp_path"] / "biggit"
        _make_local_git_repo(repo_repo, {"main.tex": "v1\n"}, "work")

        # cap 压到 1 字节：任何 .git 都超限（enforce_git_dir_cap 在 quota
        # 模块内实时调用 git_dir_cap_bytes，monkeypatch 生效）
        monkeypatch.setattr(quota_module, "git_dir_cap_bytes", lambda: 1)

        with TestClient(build_mu_upload_app()) as client:
            client.post(
                "/api/auth/login", json={"username": "alice", "password": PASSWORD}
            )
            r = client.post(
                "/api/projects",
                files={
                    "archive": (
                        "proj.zip",
                        _zip_dir_with_git(repo_repo),
                        "application/zip",
                    )
                },
                headers=csrf(client),
            )

        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "git_history_too_large"
        # 整包拒绝：无项目行、用户空间 projects/ 目录无残留
        row = db_module.db_query_one(
            "SELECT id FROM projects WHERE owner_id = ?", (alice["id"],)
        )
        assert row is None
        projects_dir = userspace.project_dir(alice["id"], "any").parent
        assert list(projects_dir.iterdir()) == []

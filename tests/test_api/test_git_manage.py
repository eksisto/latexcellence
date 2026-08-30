"""git_manage 模块测试。

覆盖内容：
    - service 层（真实临时 git 仓库）：status 未初始化/已初始化/脏状态/
      project_prefix 过滤、无提交仓库容错、init 成功/已初始化 409/
      预估超上限 413 并整体回滚、commit 成功/空提交
      400/上限 413/index.lock 409、log 分页与空仓库容错
    - 脱敏：多用户模式下 git 失败消息不泄漏仓库绝对路径
    - API 端点：message 422 / RuntimeError 400 / HTTPException 透传
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.modules.git_diff.service.process as git_diff_process
import backend.modules.git_manage.router as git_manage_router
import backend.modules.git_manage.service as git_manage_service
from backend.core.workspace_manager import (
    get_project_manager_for_workspace,
    get_workspace_manager,
)
from backend.modules.git_manage.models import GitStatusResult

require_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
)


def _git_env(repo: Path) -> dict:
    """构造带 safe.directory 的 git 子进程环境。"""
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(repo),
    }


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        env=_git_env(repo),
    )


def _make_git_repo(repo: Path, files: dict[str, str], message: str = "init") -> None:
    """在临时目录初始化 git 仓库并提交一批文件。"""
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=LaTeXcellence Test",
        "-c",
        "user.email=latexcellence@example.com",
        "commit",
        "-qm",
        message,
    )


@contextmanager
def _active_git_env(repo: Path):
    """临时为当前进程注入 safe.directory 配置，供 service 内部 git 调用使用。"""
    keys = ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")
    old = {key: os.environ.get(key) for key in keys}
    os.environ.update(_git_env(repo))
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _opened_workspace(root: Path):
    """以单用户模式打开临时目录为工作区（固定 id，finally 清理保证可复用）。"""
    ws_id = "ws"
    project = get_project_manager_for_workspace(ws_id)
    project.open(str(root))
    try:
        yield ws_id
    finally:
        get_workspace_manager().remove(ws_id)


# ── service 层：status ─────────────────────────────────────────────────────


class TestGetStatus:
    @require_git
    def test_uninitialized_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "main.tex").write_text("hello\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.initialized is False
            assert result.changes == []
            assert result.current_branch == ""
            assert result.last_commit is None
            assert result.git_dir_size_bytes == 0
            assert result.git_dir_max_bytes > 0

    @require_git
    def test_initialized_clean_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.initialized is True
            assert result.is_dirty is False
            assert result.changes == []
            assert result.current_branch != ""
            assert result.last_commit is not None
            assert result.last_commit.subject == "first"
            assert result.git_dir_size_bytes > 0

    @require_git
    def test_change_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(
                repo, {"main.tex": "v1\n", "chapters/a.tex": "A\n"}, message="first"
            )
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            (repo / "new.tex").write_text("new\n", encoding="utf-8")
            (repo / "chapters" / "a.tex").unlink()
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.is_dirty is True
            kinds = {c.path: c.kind for c in result.changes}
            assert kinds == {
                "main.tex": "modified",
                "new.tex": "added",
                "chapters/a.tex": "deleted",
            }

    @require_git
    def test_parent_repo_does_not_count_as_initialized(self):
        """项目根无自带 .git、仅落在父级仓库内 → 未初始化（可手动 init）。"""
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            _make_git_repo(
                parent,
                {"proj/main.tex": "v1\n", "outside.txt": "o\n"},
                message="first",
            )
            proj = parent / "proj"
            (proj / "main.tex").write_text("v2\n", encoding="utf-8")
            with _opened_workspace(proj), _active_git_env(parent):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.initialized is False
            assert result.changes == []

    @require_git
    def test_repo_without_commits_last_commit_none(self):
        """已 init 但无任何提交（如手动 git init 后未 commit）：status 走
        容错路径，last_commit=None 而非抛错。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "main.tex").write_text("hello\n", encoding="utf-8")
            _git(repo, "init", "-q")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.initialized is True
            assert result.last_commit is None
            assert result.is_dirty is True


# ── service 层：init ───────────────────────────────────────────────────────


class TestInitRepository:
    @require_git
    def test_init_creates_repo_gitignore_and_initial_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "main.tex").write_text("hello\n", encoding="utf-8")
            (repo / "main.aux").write_text("aux junk\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.init_repository("ws"))
                status = asyncio.run(git_manage_service.get_status("ws"))

            assert result.short_hash
            assert result.subject == "Initial commit"
            assert result.committed_count == 2  # main.tex + .gitignore（aux 被忽略）
            assert (repo / ".git").is_dir()
            gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
            assert "*.aux" in gitignore
            assert status.initialized is True
            assert status.is_dirty is False
            assert status.last_commit is not None

    @require_git
    def test_init_respects_existing_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".gitignore").write_text("custom\n", encoding="utf-8")
            (repo / "main.tex").write_text("hello\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                asyncio.run(git_manage_service.init_repository("ws"))
            assert (repo / ".gitignore").read_text(encoding="utf-8") == "custom\n"

    @require_git
    def test_init_on_initialized_repo_raises_409(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(git_manage_service.init_repository("ws"))
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail["error"] == "git_already_initialized"

    @require_git
    def test_init_over_cap_raises_413_and_rolls_back(self, monkeypatch):
        """初始提交预估超 .git 上限 → 413，init 整体回滚（可调大上限后重试）。"""
        monkeypatch.setattr(git_manage_service, "git_dir_cap_bytes", lambda: 10)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "main.tex").write_text("x" * 4096, encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(git_manage_service.init_repository("ws"))
            assert exc_info.value.status_code == 413
            assert exc_info.value.detail["error"] == "git_history_too_large"
            # 回滚：项目回到未初始化状态（.git 与本模块写入的 .gitignore 均清除）
            assert not (repo / ".git").exists()
            assert not (repo / ".gitignore").exists()

    @require_git
    def test_init_over_cap_keeps_existing_gitignore(self, monkeypatch):
        """上限回滚只清理本模块产物：项目原有 .gitignore 原样保留。"""
        monkeypatch.setattr(git_manage_service, "git_dir_cap_bytes", lambda: 10)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".gitignore").write_text("custom\n", encoding="utf-8")
            (repo / "main.tex").write_text("x" * 4096, encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException):
                    asyncio.run(git_manage_service.init_repository("ws"))
            assert not (repo / ".git").exists()
            assert (repo / ".gitignore").read_text(encoding="utf-8") == "custom\n"


# ── service 层：commit ─────────────────────────────────────────────────────


class TestCommitAll:
    @require_git
    def test_commit_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            (repo / "new.tex").write_text("brand new\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.commit_all("ws", "update main"))
                status = asyncio.run(git_manage_service.get_status("ws"))
                log = asyncio.run(git_manage_service.get_log("ws"))

            assert result.committed_count == 2
            assert result.subject == "update main"
            assert result.short_hash
            assert result.git_dir_size_bytes > 0
            assert status.is_dirty is False
            assert [e.subject for e in log] == ["update main", "first"]
            assert log[0].hash and log[0].short_hash
            assert log[0].author_name
            assert log[0].date_iso

    @require_git
    def test_commit_without_changes_raises_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(git_manage_service.commit_all("ws", "nothing"))
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail["error"] == "git_no_changes"

    @require_git
    def test_commit_over_cap_raises_413(self, monkeypatch):
        monkeypatch.setattr(git_manage_service, "git_dir_cap_bytes", lambda: 10)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "big.tex").write_text("x" * 4096, encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(git_manage_service.commit_all("ws", "too big"))
            assert exc_info.value.status_code == 413
            assert exc_info.value.detail["error"] == "git_history_too_large"
            # 上限拒绝不产生提交
            assert "too big" not in _git(repo, "log", "--pretty=%s").stdout

    @require_git
    def test_commit_with_index_lock_raises_409(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(git_manage_service.commit_all("ws", "locked"))
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail["error"] == "git_repository_locked"


# ── service 层：log ────────────────────────────────────────────────────────


class TestGetLog:
    @require_git
    def test_log_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="one")
            _make_git_repo(repo, {"main.tex": "v2\n"}, message="two")
            _make_git_repo(repo, {"main.tex": "v3\n"}, message="three")
            with _opened_workspace(repo), _active_git_env(repo):
                page = asyncio.run(git_manage_service.get_log("ws", limit=2, offset=0))
                page2 = asyncio.run(git_manage_service.get_log("ws", limit=2, offset=2))
            assert [e.subject for e in page] == ["three", "two"]
            assert [e.subject for e in page2] == ["one"]

    @require_git
    def test_log_empty_repo_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-q")
            with _opened_workspace(repo), _active_git_env(repo):
                entries = asyncio.run(git_manage_service.get_log("ws"))
            assert entries == []


# ── 脱敏 ───────────────────────────────────────────────────────────────────


class TestRedaction:
    @require_git
    def test_git_failure_message_redacted_in_multiuser_mode(self, monkeypatch):
        """多用户模式下 git 失败消息不泄漏仓库绝对路径。"""
        monkeypatch.setattr(
            git_diff_process,
            "get_config",
            lambda: SimpleNamespace(multi_user=SimpleNamespace(enabled=True)),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
            with _active_git_env(repo):
                with pytest.raises(RuntimeError) as exc_info:
                    asyncio.run(git_diff_process.run_git_checked(repo, ["add", "-A"]))
            message = str(exc_info.value)
            assert str(repo) not in message


# ── API 端点 ───────────────────────────────────────────────────────────────


class TestGitManageEndpoints:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_status_success(self, client, monkeypatch):
        fake = GitStatusResult(initialized=True, current_branch="main", is_dirty=False)
        monkeypatch.setattr(
            git_manage_router.service,
            "get_status",
            AsyncMock(return_value=fake),
        )
        response = client.get("/api/git-manage/status?workspace_id=ws-1")
        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert data["current_branch"] == "main"

    def test_status_runtime_error_returns_400(self, client, monkeypatch):
        monkeypatch.setattr(
            git_manage_router.service,
            "get_status",
            AsyncMock(side_effect=RuntimeError("No project opened")),
        )
        response = client.get("/api/git-manage/status?workspace_id=ws-1")
        assert response.status_code == 400

    def test_commit_empty_message_returns_422(self, client):
        response = client.post(
            "/api/git-manage/commit?workspace_id=ws-1", json={"message": "   "}
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "git_message_required"

    def test_commit_success(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitCommitResult

        fake = GitCommitResult(
            short_hash="abc1234",
            subject="save",
            committed_count=1,
            git_dir_size_bytes=2048,
        )
        mock = AsyncMock(return_value=fake)
        monkeypatch.setattr(git_manage_router.service, "commit_all", mock)
        response = client.post(
            "/api/git-manage/commit?workspace_id=ws-1", json={"message": "save"}
        )
        assert response.status_code == 200
        assert response.json()["short_hash"] == "abc1234"
        # message 已去空白再传给 service
        assert mock.await_args.args[1] == "save"

    def test_commit_service_error_passthrough(self, client, monkeypatch):
        """service 抛出的 HTTPException（409/413 等）原样透传。"""
        from fastapi import HTTPException

        monkeypatch.setattr(
            git_manage_router.service,
            "commit_all",
            AsyncMock(
                side_effect=HTTPException(
                    status_code=413,
                    detail={"error": "git_history_too_large", "message": "cap"},
                )
            ),
        )
        response = client.post(
            "/api/git-manage/commit?workspace_id=ws-1", json={"message": "big"}
        )
        assert response.status_code == 413
        assert response.json()["detail"]["error"] == "git_history_too_large"

    def test_log_success(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitLogEntry

        fake = [
            GitLogEntry(
                hash="a" * 40,
                short_hash="aaa1111",
                subject="first",
                author_name="tester",
                date_iso="2026-01-01T00:00:00+00:00",
            )
        ]
        monkeypatch.setattr(
            git_manage_router.service, "get_log", AsyncMock(return_value=fake)
        )
        response = client.get("/api/git-manage/log?workspace_id=ws-1&limit=2&offset=1")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"][0]["short_hash"] == "aaa1111"
        assert git_manage_router.service.get_log.await_args.kwargs == {
            "limit": 2,
            "offset": 1,
        }

    def test_init_success(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitCommitResult

        fake = GitCommitResult(
            short_hash="def5678",
            subject="Initial commit",
            committed_count=3,
            git_dir_size_bytes=1024,
        )
        monkeypatch.setattr(
            git_manage_router.service,
            "init_repository",
            AsyncMock(return_value=fake),
        )
        response = client.post("/api/git-manage/init?workspace_id=ws-1")
        assert response.status_code == 200
        assert response.json()["committed_count"] == 3

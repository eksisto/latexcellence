"""git_manage 模块测试。

覆盖内容：
    - service 层（真实临时 git 仓库）：status 未初始化/已初始化/脏状态/
      project_prefix 过滤、无提交仓库容错、init 成功/已初始化 409/
      预估超上限 413 并整体回滚、commit 成功/空提交
      400/上限 413/index.lock 409、log 分页与空仓库容错
    - 源码级 diff：修改/删除/未跟踪文件、干净文件、二进制、截断、
      commit 模式（只读）与非法哈希、无提交仓库
    - 选择性恢复：整文件（含删除找回、暂存态 index+worktree 一并回退）、
      按 hunk 部分 hunk 恢复、过期/伪造切片 409、未跟踪拒绝、
      无变更 400、非法路径 400、无基线 400、project_prefix 子目录场景
    - merge 提交：--first-parent 按第一父提交呈现变更清单
    - 脱敏：多用户模式下 git 失败消息不泄漏仓库绝对路径
    - API 端点：message 422 / RuntimeError 400 / HTTPException 透传 /
      file-diff / commit-files / restore 参数传递
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
    def test_marker_dir_not_listed_when_not_ignored(self):
        """项目自带 .gitignore 未排除 .latexcellence/ 时（init 模板不写入的
        场景），主文件标记不应作为变更出现在版本页，也不计入脏标记。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(
                repo,
                {"main.tex": "v1\n", ".gitignore": "*.aux\n"},
                message="first",
            )
            marker_dir = repo / ".latexcellence"
            marker_dir.mkdir()
            (marker_dir / "main_file").write_text("main.tex\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(git_manage_service.get_status("ws"))
            assert result.changes == []
            assert result.is_dirty is False

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


# ── service 层：源码级 diff ────────────────────────────────────────────────


class TestFileDiff:
    @require_git
    def test_modified_file_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "line1\nline2\nline3\n"}, message="first")
            (repo / "main.tex").write_text("line1\nNEW\nline3\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
            assert d.kind == "modified"
            assert d.restorable is True
            assert d.is_binary is False
            assert len(d.hunks) == 1
            hunk = d.hunks[0]
            assert hunk.header.startswith("@@ -")
            types = [line.type for line in hunk.lines]
            assert "add" in types and "del" in types and "context" in types
            added = [line.text for line in hunk.lines if line.type == "add"]
            assert added == ["NEW"]
            # raw 可回传：以 @@ 开头且包含行内容
            assert hunk.raw.startswith("@@ ")
            assert "+NEW\n" in hunk.raw
            assert d.additions == 1 and d.deletions == 1

    @require_git
    def test_untracked_file_full_add_diff_not_restorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "new.tex").write_text("brand\nnew\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="new.tex"))
            assert d.kind == "added"
            assert d.restorable is False
            types = {line.type for h in d.hunks for line in h.lines}
            assert types == {"add"}

    @require_git
    def test_deleted_file_diff_restorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n", "gone.tex": "bye\n"}, message="f")
            (repo / "gone.tex").unlink()
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="gone.tex"))
            assert d.kind == "deleted"
            assert d.restorable is True
            assert d.hunks and all(
                line.type == "del" for h in d.hunks for line in h.lines
            )

    @require_git
    def test_clean_file_returns_empty_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
            assert d.kind == ""
            assert d.hunks == []
            assert d.restorable is False

    @require_git
    def test_binary_file_has_no_hunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"img.png": "\x89PNG\r\n\x1a\n\x00\x01"}, message="f")
            (repo / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x02")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="img.png"))
            assert d.kind == "modified"
            assert d.is_binary is True
            assert d.hunks == []
            assert d.restorable is True  # 仍可整文件恢复

    @require_git
    def test_large_diff_truncated_at_hunk_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"big.txt": "x\n"}, message="first")
            (repo / "big.txt").write_text(
                "".join(f"line{i} changed\n" for i in range(3000)),
                encoding="utf-8",
            )
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="big.txt"))
            assert d.truncated is True
            # 单 hunk 超限整体丢弃：返回的 hunks 均为完整 hunk（raw 可回传）
            for hunk in d.hunks:
                assert hunk.raw.startswith("@@ ")

    @require_git
    def test_commit_mode_readonly_and_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            _make_git_repo(repo, {"main.tex": "v2\n"}, message="second")
            full = _git(repo, "rev-parse", "HEAD").stdout.strip()
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(
                    git_manage_service.get_file_diff("ws", path="main.tex", commit=full)
                )
                with pytest.raises(Exception) as exc_info:
                    asyncio.run(
                        git_manage_service.get_file_diff(
                            "ws", path="main.tex", commit="zzzz"
                        )
                    )
            assert d.kind == "modified"
            assert d.restorable is False
            assert d.commit_hash == full
            assert exc_info.value.detail["error"] == "git_commit_not_found"

    @require_git
    def test_commit_mode_short_hash(self):
        """短哈希直接可用（git show 自身校验，省去前置 rev-parse）。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            short = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(
                    git_manage_service.get_file_diff(
                        "ws", path="main.tex", commit=short
                    )
                )
            assert d.kind == "added"  # root commit 全文新增
            assert d.restorable is False
            assert d.commit_hash == short

    @require_git
    def test_file_diff_in_project_prefix_subdir(self):
        """项目是仓库子目录：cwd=项目根 + cwd 相对 pathspec 即可定位。"""
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            _make_git_repo(
                parent,
                {"sub/main.tex": "aaa\nbbb\nccc\n", "out.txt": "o\n"},
                message="f",
            )
            sub = parent / "sub"
            (sub / "main.tex").write_text("aaa\nXXX\nccc\n", encoding="utf-8")
            (sub / "inner.tex").write_text("inner\n", encoding="utf-8")
            with _opened_workspace(sub), _active_git_env(parent):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
                du = asyncio.run(
                    git_manage_service.get_file_diff("ws", path="inner.tex")
                )
            assert d.kind == "modified"
            assert d.restorable is True
            assert du.kind == "added"
            assert du.restorable is False

    @require_git
    def test_tracked_change_without_commits_not_restorable(self):
        """已跟踪变更但仓库无提交（unborn HEAD）：无基线可比可恢复。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "main.tex").write_text("v1\n", encoding="utf-8")
            _git(repo, "init", "-q")
            _git(repo, "add", "-A")  # 暂存后 XY=A（非 ??），但无 HEAD
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
            assert d.kind == "added"
            assert d.restorable is False
            # diff HEAD 失败即无基线：按全文新增展示内容
            assert d.hunks and all(
                line.type == "add" for h in d.hunks for line in h.lines
            )

    @require_git
    def test_invalid_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                for bad in (
                    "../outside.tex",
                    ".git/config",
                    ".latexcellence/main_file",
                    "",
                ):
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(git_manage_service.get_file_diff("ws", path=bad))
                    assert exc_info.value.status_code == 400
                    assert exc_info.value.detail["error"] == "git_file_path_invalid"

    @require_git
    def test_commit_files_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n", "old.tex": "o\n"}, message="f")
            # 单个提交同时包含修改 / 新增 / 删除三种 kind
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            (repo / "new.tex").write_text("n\n", encoding="utf-8")
            (repo / "old.tex").unlink()
            _git(repo, "add", "-A")
            _git(
                repo,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "second",
            )
            full = _git(repo, "rev-parse", "HEAD").stdout.strip()
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(
                    git_manage_service.get_commit_files("ws", commit=full)
                )
            kinds = {f.path: f.kind for f in result.files}
            assert kinds == {
                "main.tex": "modified",
                "new.tex": "added",
                "old.tex": "deleted",
            }
            assert result.hash == full

    @require_git
    def test_commit_files_merge_commit_first_parent(self):
        """merge 提交默认输出空 combined diff：--first-parent 按第一父
        提交呈现变更，不会被误判为「无变更」。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="base")
            base_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            _git(repo, "checkout", "-qb", "side")
            (repo / "side.tex").write_text("s\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(
                repo,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "side",
            )
            _git(repo, "checkout", "-q", base_branch)
            _git(
                repo,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@t",
                "merge",
                "-q",
                "--no-edit",
                "side",
            )
            merge_hash = _git(repo, "rev-parse", "HEAD").stdout.strip()
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(
                    git_manage_service.get_commit_files("ws", commit=merge_hash)
                )
            assert {f.path for f in result.files} == {"side.tex"}
            assert result.files[0].kind == "added"


# ── service 层：选择性恢复 ─────────────────────────────────────────────────


class TestRestoreChanges:
    def _restore_request(self, path, mode="file", hunks=None):
        from backend.modules.git_manage.models import GitRestoreRequest

        return GitRestoreRequest(path=path, mode=mode, hunks=hunks or [])

    @require_git
    def test_restore_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(
                    git_manage_service.restore_changes(
                        "ws", request=self._restore_request("main.tex")
                    )
                )
                status = asyncio.run(git_manage_service.get_status("ws"))
            assert result.mode == "file"
            assert (repo / "main.tex").read_text(encoding="utf-8") == "v1\n"
            assert status.is_dirty is False

    @require_git
    def test_restore_deleted_file_recovers_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n", "gone.tex": "bye\n"}, message="f")
            (repo / "gone.tex").unlink()
            with _opened_workspace(repo), _active_git_env(repo):
                asyncio.run(
                    git_manage_service.restore_changes(
                        "ws", request=self._restore_request("gone.tex")
                    )
                )
            assert (repo / "gone.tex").read_text(encoding="utf-8") == "bye\n"

    @require_git
    def test_restore_selected_hunk_only(self):
        """两个独立 hunk 只恢复第一个：line2 回退、line18 保留新值。"""
        old = "".join(f"line{i}\n" for i in range(1, 21))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": old}, message="first")
            new_lines = old.splitlines(keepends=True)
            new_lines[1] = "CHANGED_HEAD\n"  # hunk 1（第 2 行）
            new_lines[17] = "CHANGED_TAIL\n"  # hunk 2（第 18 行，间隔超出 3 行上下文）
            (repo / "main.tex").write_text("".join(new_lines), encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
                assert len(d.hunks) == 2
                asyncio.run(
                    git_manage_service.restore_changes(
                        "ws",
                        request=self._restore_request(
                            "main.tex", mode="hunks", hunks=[d.hunks[0].raw]
                        ),
                    )
                )
                status = asyncio.run(git_manage_service.get_status("ws"))
            content = (repo / "main.tex").read_text(encoding="utf-8")
            assert "line2\n" in content
            assert "CHANGED_HEAD" not in content
            assert "CHANGED_TAIL" in content  # 未勾选的 hunk 保留
            assert status.is_dirty is True

    @require_git
    def test_restore_stale_hunks_rejected_409(self):
        """diff 展示后工作区又被改动：过期切片恢复 → 409 fail-closed。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\nv2\nv3\n"}, message="first")
            (repo / "main.tex").write_text("v1\nCHANGED\nv3\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
                (repo / "main.tex").write_text("totally\nmoved on\n", encoding="utf-8")
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(
                        git_manage_service.restore_changes(
                            "ws",
                            request=self._restore_request(
                                "main.tex", mode="hunks", hunks=[d.hunks[0].raw]
                            ),
                        )
                    )
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail["error"] == "git_worktree_changed"
            # 破坏性操作未发生：工作区保持最后一次写入的内容
            assert (repo / "main.tex").read_text(
                encoding="utf-8"
            ) == "totally\nmoved on\n"

    @require_git
    def test_restore_untracked_rejected_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "new.tex").write_text("n\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(
                        git_manage_service.restore_changes(
                            "ws", request=self._restore_request("new.tex")
                        )
                    )
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail["error"] == "git_file_untracked"

    @require_git
    def test_restore_clean_file_rejected_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(
                        git_manage_service.restore_changes(
                            "ws", request=self._restore_request("main.tex")
                        )
                    )
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail["error"] == "git_no_changes"

    @require_git
    def test_restore_invalid_path_rejected_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(
                        git_manage_service.restore_changes(
                            "ws", request=self._restore_request("../evil.tex")
                        )
                    )
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail["error"] == "git_file_path_invalid"

    @require_git
    def test_restore_empty_hunks_rejected_422(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            with _opened_workspace(repo), _active_git_env(repo):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(
                        git_manage_service.restore_changes(
                            "ws",
                            request=self._restore_request(
                                "main.tex", mode="hunks", hunks=[]
                            ),
                        )
                    )
            assert exc_info.value.status_code == 422
            assert exc_info.value.detail["error"] == "git_patch_invalid"

    @require_git
    def test_restore_in_project_prefix_subdir(self):
        """项目是仓库子目录：hunk 恢复的 patch 路径基准正确落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            _make_git_repo(
                parent,
                {"sub/main.tex": "aaa\nbbb\nccc\n", "out.txt": "o\n"},
                message="f",
            )
            sub = parent / "sub"
            (sub / "main.tex").write_text("aaa\nXXX\nccc\n", encoding="utf-8")
            with _opened_workspace(sub), _active_git_env(parent):
                d = asyncio.run(git_manage_service.get_file_diff("ws", path="main.tex"))
                asyncio.run(
                    git_manage_service.restore_changes(
                        "ws",
                        request=self._restore_request(
                            "main.tex", mode="hunks", hunks=[h.raw for h in d.hunks]
                        ),
                    )
                )
            assert (sub / "main.tex").read_text(encoding="utf-8") == "aaa\nbbb\nccc\n"
            # patch 临时文件已清理
            assert not (sub / ".latexcellence" / "tmp").exists() or not any(
                (sub / ".latexcellence" / "tmp").iterdir()
            )

    @require_git
    def test_restore_staged_added_resets_index_and_worktree(self):
        """staged-added（HEAD 无此文件）整文件恢复：index 条目与工作区
        文件一并清除，仓库回到干净——只回退工作区会残留 A→AD 的
        「已恢复但仍在变更列表」失实状态。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "new.tex").write_text("n\n", encoding="utf-8")
            _git(repo, "add", "new.tex")  # 模拟 commit 失败后残留的暂存态
            with _opened_workspace(repo), _active_git_env(repo):
                result = asyncio.run(
                    git_manage_service.restore_changes(
                        "ws", request=self._restore_request("new.tex")
                    )
                )
                status = asyncio.run(git_manage_service.get_status("ws"))
            assert result.mode == "file"
            assert not (repo / "new.tex").exists()
            assert status.is_dirty is False

    @require_git
    def test_restore_staged_modified_resets_index_and_worktree(self):
        """staged-modified 整文件恢复：暂存区一并回退，不再残留 M 状态。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="first")
            (repo / "main.tex").write_text("v2\n", encoding="utf-8")
            _git(repo, "add", "main.tex")
            with _opened_workspace(repo), _active_git_env(repo):
                asyncio.run(
                    git_manage_service.restore_changes(
                        "ws", request=self._restore_request("main.tex")
                    )
                )
                status = asyncio.run(git_manage_service.get_status("ws"))
            assert (repo / "main.tex").read_text(encoding="utf-8") == "v1\n"
            assert status.is_dirty is False


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

    def test_file_diff_endpoint(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitFileDiffResult

        fake = GitFileDiffResult(path="main.tex", kind="modified", restorable=True)
        mock = AsyncMock(return_value=fake)
        monkeypatch.setattr(git_manage_router.service, "get_file_diff", mock)
        response = client.get(
            "/api/git-manage/file-diff?workspace_id=ws-1&path=main.tex"
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "modified"
        assert mock.await_args.kwargs == {"path": "main.tex", "commit": ""}

        response = client.get(
            "/api/git-manage/file-diff?workspace_id=ws-1&path=main.tex&commit=abc1234"
        )
        assert response.status_code == 200
        assert mock.await_args.kwargs == {"path": "main.tex", "commit": "abc1234"}

    def test_commit_files_endpoint(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitCommitFilesResult

        fake = GitCommitFilesResult(hash="a" * 40, files=[], truncated=False)
        mock = AsyncMock(return_value=fake)
        monkeypatch.setattr(git_manage_router.service, "get_commit_files", mock)
        response = client.get(
            "/api/git-manage/commit-files?workspace_id=ws-1&commit=abc1234"
        )
        assert response.status_code == 200
        assert response.json()["hash"] == "a" * 40
        assert mock.await_args.kwargs == {"commit": "abc1234"}

    def test_restore_endpoint(self, client, monkeypatch):
        from backend.modules.git_manage.models import GitRestoreResult

        fake = GitRestoreResult(path="main.tex", mode="hunks", restored_hunks=2)
        mock = AsyncMock(return_value=fake)
        monkeypatch.setattr(git_manage_router.service, "restore_changes", mock)
        response = client.post(
            "/api/git-manage/restore?workspace_id=ws-1",
            json={"path": "main.tex", "mode": "hunks", "hunks": ["@@ -1,1 +1,1 @@\n"]},
        )
        assert response.status_code == 200
        assert response.json()["restored_hunks"] == 2
        request_arg = mock.await_args.kwargs["request"]
        assert request_arg.path == "main.tex"
        assert request_arg.mode == "hunks"
        assert request_arg.hunks == ["@@ -1,1 +1,1 @@\n"]

    def test_restore_endpoint_invalid_mode_422(self, client):
        response = client.post(
            "/api/git-manage/restore?workspace_id=ws-1",
            json={"path": "main.tex", "mode": "bogus"},
        )
        assert response.status_code == 422

"""
git_diff 模块纯函数单元测试。

覆盖内容：
    - _normalize_include_path 格式规范化
    - _validate_project_path 路径安全校验（委托 path_guard）
    - _split_document 文档分块（preamble / body / end）
    - _diff_lines 行级差异标记
    - _is_readonly_git_args 只读 git 子命令判定（沙箱租约豁免）
    - _wrap_diff_block 浮动体拆包与空行保留
    - _resolve_include_path / _read_revision_file 路径穿越防护
    - _list_commit_revisions 无提交仓库容错
    - _prune_old_job_dirs 临时目录清理
    - _flatten_main_document git / worktree 模式依赖展开
    - _prepare_target_project 编译目录准备
    - list_git_revisions / _validate_revision
    - render_diff_pdf 成功 / 失败 / 配置默认引擎
    - API 端点（/options、/refs、/render-pdf）
    - _build_diff_document 完整差异文档生成
    - _resolve_color_hex 颜色格式转换
    - _build_diff_preamble 自定义颜色注入
    - _count_latexdiff_changes latexdiff 输出统计
    - get_pdf_path job_id 校验
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import backend.modules.git_diff.router as git_diff_router
import backend.modules.git_diff.service as git_diff_service
import backend.modules.git_diff.service.constants as git_diff_constants
import backend.modules.git_diff.service.diff as git_diff_diff
import backend.modules.git_diff.service.flatten as git_diff_flatten
import backend.modules.git_diff.service.process as git_diff_process
import backend.modules.git_diff.service.render as git_diff_render
import backend.modules.git_diff.service.revisions as git_diff_revisions
from backend.core.workspace_manager import (
    get_project_manager_for_workspace,
    get_workspace_manager,
)
from backend.modules.git_diff.service import (
    WORKTREE_REF,
    DiffDocument,
    DiffHighlightResult,
    FlattenContext,
    FlattenedDocument,
    GitRefsResult,
    GitRevision,
    ProcessResult,
    ProjectContext,
    TexCompileResult,
    _build_diff_document,
    _build_diff_preamble,
    _count_latexdiff_changes,
    _diff_lines,
    _flatten_main_document,
    _list_commit_revisions,
    _normalize_include_path,
    _prepare_target_project,
    _prune_old_job_dirs,
    _read_revision_file,
    _resolve_color_hex,
    _resolve_include_path,
    _split_document,
    _strip_blank_lines,
    _validate_project_path,
    _validate_revision,
    _wrap_diff_block,
    get_pdf_path,
    list_git_revisions,
)
from backend.utils.sandbox import SandboxMount


def _git_env(repo: Path) -> dict:
    """构造带 safe.directory 的 git 子进程环境。"""
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(repo),
    }


def _make_git_repo(repo: Path, files: dict[str, str], message: str = "init") -> None:
    """在临时目录初始化 git 仓库并提交一批文件。"""
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    env = _git_env(repo)
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
            "user.name=LaTeXcellence Test",
            "-c",
            "user.email=latexcellence@example.com",
            "-C",
            str(repo),
            "commit",
            "-qm",
            message,
        ],
        check=True,
        env=env,
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


def _git_diff_config_stub(
    engine: str = "difflib",
    markup: str = "UNDERLINE",
    math: str = "fine",
    color_add: str = "#D7E8B1",
    color_del: str = "#FFA3A3",
) -> SimpleNamespace:
    """构造 features.git_diff 配置桩。"""
    return SimpleNamespace(
        features=SimpleNamespace(
            git_diff=SimpleNamespace(
                default_engine=engine,
                default_markup_type=markup,
                default_math_markup=math,
                default_color_add=color_add,
                default_color_del=color_del,
            )
        )
    )


# ── _normalize_include_path ────────────────────────────────────────────────


class TestNormalizeIncludePath:
    """路径格式规范化测试。"""

    def test_simple_relative(self):
        assert _normalize_include_path("main.tex") == "main.tex"

    def test_subdirectory(self):
        assert _normalize_include_path("chapters/intro.tex") == "chapters/intro.tex"

    def test_backslash_converted(self):
        assert _normalize_include_path("chapters\\intro.tex") == "chapters/intro.tex"

    def test_strips_whitespace(self):
        assert _normalize_include_path("  main.tex  ") == "main.tex"

    def test_empty_returns_none(self):
        assert _normalize_include_path("") is None
        assert _normalize_include_path("   ") is None

    def test_absolute_preserved(self):
        assert _normalize_include_path("/etc/passwd") == "/etc/passwd"

    def test_parent_traversal_preserved(self):
        assert _normalize_include_path("../secret.tex") == "../secret.tex"
        assert (
            _normalize_include_path("chapters/../secret.tex")
            == "chapters/../secret.tex"
        )


# ── _validate_project_path ─────────────────────────────────────────────────


class TestValidateProjectPath:
    """路径安全校验测试（委托 path_guard）。"""

    @pytest.fixture
    def project_root(self, tmp_path):
        (tmp_path / "main.tex").write_text("test", encoding="utf-8")
        subdir = tmp_path / "chapters"
        subdir.mkdir()
        (subdir / "intro.tex").write_text("intro", encoding="utf-8")
        return tmp_path

    def test_valid_relative(self, project_root):
        result = _validate_project_path(project_root, "main.tex")
        assert result == "main.tex"

    def test_valid_subdir(self, project_root):
        result = _validate_project_path(project_root, "chapters/intro.tex")
        assert result == "chapters/intro.tex"

    def test_require_tex_pass(self, project_root):
        result = _validate_project_path(project_root, "main.tex", require_tex=True)
        assert result == "main.tex"

    def test_require_tex_reject(self, project_root):
        with pytest.raises(ValueError, match="must end with"):
            _validate_project_path(project_root, "refs.bib", require_tex=True)

    def test_empty_rejected(self, project_root):
        with pytest.raises(ValueError):
            _validate_project_path(project_root, "")

    def test_absolute_rejected(self, project_root):
        import os

        # 使用 os.path.isabs 一致的平台绝对路径
        abs_path = "C:/secret.tex" if os.name == "nt" else "/etc/passwd"
        with pytest.raises(ValueError, match="must not be an absolute path"):
            _validate_project_path(project_root, abs_path)

    def test_dotdot_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_project_path(project_root, "../secret.tex")

    def test_escape_root_rejected(self, project_root):
        outside = project_root.parent / "outside.tex"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(ValueError, match="must not contain"):
            _validate_project_path(project_root, "../outside.tex")


# ── _split_document ────────────────────────────────────────────────────────


class TestSplitDocument:
    """文档分块测试。"""

    def test_normal_document(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{amsmath}" + "\n"
            r"\begin{document}" + "\n"
            r"Hello world." + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert begin == r"\begin{document}"
        assert "Hello world." in body
        assert end == r"\end{document}"

    def test_no_begin_document(self):
        content = "just text"
        preamble, begin, body, end, tail = _split_document(content)
        assert preamble == ""
        assert begin == ""
        assert body == "just text"
        assert end == ""

    def test_no_end_document(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"body without end"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert begin == r"\begin{document}"
        assert "body without end" in body
        assert end == ""

    def test_begin_document_in_comment_ignored(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{amsmath}" + "\n"
            r"%  \begin{document} 到 \end{document} 之间的内容会输出到 PDF" + "\n"
            r"\begin{document}" + "\n"
            r"Hello world." + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert r"\usepackage{amsmath}" in preamble
        assert begin == r"\begin{document}"
        assert "Hello world." in body
        assert end == r"\end{document}"
        assert body.count(r"\begin{document}") == 0

    def test_end_document_in_comment_ignored(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"% comment with \end{document} inside" + "\n"
            r"body text" + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert begin == r"\begin{document}"
        assert "body text" in body
        assert "% comment with" in body
        assert end == r"\end{document}"

    def test_escaped_percent_not_treated_as_comment(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"100\% \end{document} is not a real end" + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert begin == r"\begin{document}"
        assert end == r"\end{document}"
        assert r"100\%" in body


# ── _diff_lines ────────────────────────────────────────────────────────────


class TestDiffLines:
    """行级差异标记测试。"""

    def test_identical_lines(self):
        base = ["line1", "line2"]
        target = ["line1", "line2"]
        output, added, deleted = _diff_lines(base, target)
        assert output == ["line1", "line2"]
        assert added == 0
        assert deleted == 0

    def test_pure_insert(self):
        base = ["line1"]
        target = ["line1", "line2"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 1
        assert deleted == 0
        assert "\\begin{LXDiffAdded}" in output
        assert "line2" in output
        assert "\\end{LXDiffAdded}" in output

    def test_pure_delete(self):
        base = ["line1", "line2"]
        target = ["line1"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 0
        assert deleted == 1
        assert "\\begin{LXDiffDeleted}" in output
        assert "line2" in output
        assert "\\end{LXDiffDeleted}" in output

    def test_replace(self):
        base = ["old line"]
        target = ["new line"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 1
        assert deleted == 1
        assert "\\begin{LXDiffDeleted}" in output
        assert "\\begin{LXDiffAdded}" in output
        assert "old line" in output
        assert "new line" in output

    def test_empty_inputs(self):
        output, added, deleted = _diff_lines([], [])
        assert output == []
        assert added == 0
        assert deleted == 0


# ── _wrap_diff_block 浮动体处理 ───────────────────────────────────────────


class TestWrapDiffBlockFloats:
    """浮动体不能放进 framed 颜色框，需拆到包裹外并保持独立段落。"""

    def test_float_kept_outside_wrapper(self):
        lines = [
            "Added text before.",
            r"\begin{table}[htbp]",
            r"\caption{Summary}",
            r"\end{table}",
            "Added text after.",
        ]
        result = _wrap_diff_block("LXDiffAdded", lines)
        text = "\n".join(result)

        assert r"\begin{LXDiffAdded}" in text
        assert r"\end{LXDiffAdded}" in text
        assert r"\begin{table}" in text
        assert r"\end{table}" in text
        # 浮动体本身不得出现在高亮包裹内
        assert r"\begin{LXDiffAdded}" + "\n" + r"\begin{table}" not in text
        # 浮动体前后留空行，处于独立段落，避免 "Float(s) lost"
        assert r"\end{LXDiffAdded}" + "\n\n" + r"\begin{table}" in text
        assert r"\end{table}" + "\n\n" + r"\begin{LXDiffAdded}" in text

    def test_float_only_block_has_no_empty_wrapper(self):
        lines = [
            r"\begin{table}[h]",
            r"\caption{Summary}",
            r"\end{table}",
        ]
        result = _wrap_diff_block("LXDiffAdded", lines)
        assert r"\begin{LXDiffAdded}" not in result
        assert r"\end{LXDiffAdded}" not in result
        assert result == [
            r"\begin{table}",
            "[h]",
            r"\caption{Summary}",
            r"\end{table}",
            "",
        ]

    def test_inner_environments_follow_float_outside_wrapper(self):
        lines = [
            r"\begin{table}[h]",
            r"\begin{tabular}{cc}",
            "a & b",
            r"\end{tabular}",
            r"\end{table}",
        ]
        result = _wrap_diff_block("LXDiffAdded", lines)
        assert r"\begin{LXDiffAdded}" not in result
        assert r"\begin{tabular}" in result
        assert r"\end{tabular}" in result

    def test_blank_lines_preserved_inside_wrapper(self):
        lines = ["Paragraph one.", "", "Paragraph two."]
        result = _wrap_diff_block("LXDiffAdded", lines)
        assert result == [
            r"\begin{LXDiffAdded}",
            "Paragraph one.",
            "",
            "Paragraph two.",
            r"\end{LXDiffAdded}",
        ]

    def test_deleted_float_also_unwrapped(self):
        lines = [
            r"\begin{figure}",
            r"\includegraphics{old.png}",
            r"\end{figure}",
        ]
        result = _wrap_diff_block("LXDiffDeleted", lines)
        assert r"\begin{LXDiffDeleted}" not in result
        assert r"\begin{figure}" in result
        assert r"\includegraphics{old.png}" in result


# ── 路径安全与边界情况 ────────────────────────────────────────────────────


class TestIncludePathSafety:
    """include 解析必须拒绝绝对路径与目录穿越，且不破坏正常解析。"""

    @staticmethod
    def _make_context(tmp_dir: str):
        root = Path(tmp_dir) / "proj"
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.tex").write_text("x", encoding="utf-8")
        project = ProjectContext(
            root=root,
            git_root=Path(tmp_dir),
            project_prefix=PurePosixPath("proj"),
        )
        return project, FlattenContext(ref=WORKTREE_REF, project=project)

    def test_dotdot_include_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project, ctx = self._make_context(tmp)
            resolved = asyncio.run(
                _resolve_include_path(ctx, "main.tex", "../secret.tex")
            )
            assert resolved is None

    def test_absolute_include_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project, ctx = self._make_context(tmp)
            evil = "C:/windows/win.ini" if os.name == "nt" else "/etc/passwd"
            resolved = asyncio.run(_resolve_include_path(ctx, "main.tex", evil))
            assert resolved is None

    def test_worktree_read_cannot_escape_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "secret.tex").write_text("secret", encoding="utf-8")
            _project, ctx = self._make_context(tmp)
            content = asyncio.run(
                _read_revision_file(
                    ctx.project, WORKTREE_REF, "../secret.tex", required=False
                )
            )
            assert content is None

    def test_normal_include_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir(exist_ok=True)
            (proj / "main.tex").write_text(r"\input{sub}", encoding="utf-8")
            (proj / "sub.tex").write_text("sub", encoding="utf-8")
            project = ProjectContext(
                root=proj,
                git_root=tmp_path,
                project_prefix=PurePosixPath("proj"),
            )
            ctx = FlattenContext(ref=WORKTREE_REF, project=project)
            resolved = asyncio.run(_resolve_include_path(ctx, "main.tex", "sub"))
            assert resolved == "sub.tex"


class TestListCommitRevisions:
    """全新仓库（无提交）不应报错，返回空列表。"""

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_empty_repo_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "GIT_CONFIG_VALUE_0": str(repo),
            }
            subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)

            old_count = os.environ.get("GIT_CONFIG_COUNT")
            os.environ["GIT_CONFIG_COUNT"] = "1"
            os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
            os.environ["GIT_CONFIG_VALUE_0"] = str(repo)
            try:
                revisions = asyncio.run(_list_commit_revisions(repo))
            finally:
                if old_count is None:
                    for key in (
                        "GIT_CONFIG_COUNT",
                        "GIT_CONFIG_KEY_0",
                        "GIT_CONFIG_VALUE_0",
                    ):
                        os.environ.pop(key, None)
            assert revisions == []


class TestReadonlyGitArgs:
    """只读 git 子命令判定（每用户沙箱租约豁免依据）。"""

    def test_readonly_subcommands(self):
        assert git_diff_process._is_readonly_git_args(
            ["status", "--porcelain=v1", "-z", "-uall"]
        )
        assert git_diff_process._is_readonly_git_args(
            ["log", "-1", "--pretty=format:%h%x00%s"]
        )
        assert git_diff_process._is_readonly_git_args(["rev-parse", "--show-toplevel"])
        assert git_diff_process._is_readonly_git_args(
            ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"]
        )
        assert git_diff_process._is_readonly_git_args(["branch", "--show-current"])
        assert git_diff_process._is_readonly_git_args(["for-each-ref", "refs/heads"])
        assert git_diff_process._is_readonly_git_args(["show", "HEAD:main.tex"])
        assert git_diff_process._is_readonly_git_args(
            ["archive", "--format=zip", "--output", "/tmp/a.zip", "HEAD"]
        )

    def test_mutating_subcommands(self):
        assert not git_diff_process._is_readonly_git_args(["init"])
        assert not git_diff_process._is_readonly_git_args(["add", "-A"])
        # 子命令的参数值（commit message 恰为只读子命令名）不能误判：
        # 首个命中的已知子命令是 commit 本身
        assert not git_diff_process._is_readonly_git_args(
            ["-c", "user.name=x", "-c", "user.email=y", "commit", "-m", "status"]
        )

    def test_unknown_subcommand_conservative(self):
        # 未识别的调用保守按非只读处理（照常占用租约，宁多不漏）
        assert not git_diff_process._is_readonly_git_args(["worktree", "list"])
        assert not git_diff_process._is_readonly_git_args([])

    @pytest.mark.asyncio
    async def test_run_git_passes_lease_exempt_flag(self, monkeypatch):
        seen: dict = {}

        class FakeRunner:
            async def execute(self, spec, *, user_id=None, lease_exempt=False):
                seen["lease_exempt"] = lease_exempt
                return SimpleNamespace(
                    returncode=0, stdout="", stderr="", timed_out=False
                )

        monkeypatch.setattr(
            git_diff_process, "get_sandbox_runner", lambda: FakeRunner()
        )
        monkeypatch.setattr(git_diff_process, "current_sandbox_user_id", lambda: "u1")

        await git_diff_process.run_git(Path("/tmp"), ["status", "--porcelain"])
        assert seen["lease_exempt"] is True

        await git_diff_process.run_git(Path("/tmp"), ["add", "-A"])
        assert seen["lease_exempt"] is False

    @pytest.mark.asyncio
    async def test_run_git_passes_mounts_to_exec_spec(self, monkeypatch):
        seen: dict = {}

        class FakeRunner:
            async def execute(self, spec, *, user_id=None, lease_exempt=False):
                seen["spec"] = spec
                return SimpleNamespace(
                    returncode=0, stdout="", stderr="", timed_out=False
                )

        monkeypatch.setattr(
            git_diff_process, "get_sandbox_runner", lambda: FakeRunner()
        )
        monkeypatch.setattr(git_diff_process, "current_sandbox_user_id", lambda: None)

        # 传 mounts 时透传进 ExecSpec（docker 后端据此把 cwd 外的落盘
        # 目录挂进容器，如 git archive 的 --output 目标）
        job_dir = Path("/tmp/latexcellence_git_diff") / ("a" * 32)
        await git_diff_process.run_git(
            Path("/repo"),
            [
                "archive",
                "--format=zip",
                "--output",
                str(job_dir / "target.zip"),
                "HEAD",
            ],
            mounts=[SandboxMount(host_path=str(job_dir))],
        )
        assert [m.host_path for m in seen["spec"].mounts] == [str(job_dir)]

        # 未传 mounts 的常规调用不带任何额外挂载（保持 cwd-only 契约）
        await git_diff_process.run_git(Path("/repo"), ["status", "--porcelain"])
        assert seen["spec"].mounts == []


class TestPruneJobDirs:
    """临时 job 目录只保留最新若干份。"""

    def test_keeps_newest_and_removes_old(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monkeypatch.setattr(git_diff_constants, "PDF_OUTPUT_ROOT", root)
            for i in range(25):
                job = root / f"{i:032x}"
                job.mkdir()
                os.utime(job, (i, i))
            _prune_old_job_dirs()
            remaining = sorted(p.name for p in root.iterdir())
            assert len(remaining) == 20
            assert f"{0:032x}" not in remaining


# ── _build_diff_document ───────────────────────────────────────────────────


class TestBuildDiffDocument:
    """完整差异文档生成测试。"""

    def test_diff_document_is_valid_latex(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Old content." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"New content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, target)
        assert diff.added_lines == 1
        assert diff.deleted_lines == 1
        assert r"\documentclass{article}" in diff.content
        assert r"\begin{document}" in diff.content
        assert r"\end{document}" in diff.content
        assert "LXDiffAdded" in diff.content
        assert "LXDiffDeleted" in diff.content
        assert "New content." in diff.content
        assert "Old content." in diff.content

    def test_no_changes(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Same content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, base)
        assert diff.added_lines == 0
        assert diff.deleted_lines == 0
        assert "\\begin{LXDiffAdded}" not in diff.content
        assert "\\begin{LXDiffDeleted}" not in diff.content

    def test_preamble_from_target(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{oldpkg}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{newpkg}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, target)
        assert r"\usepackage{newpkg}" in diff.content
        assert r"\usepackage{oldpkg}" not in diff.content

    def test_diff_preamble_injected(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, base)
        assert r"\definecolor{LXDiffAddBg}" in diff.content
        assert r"\definecolor{LXDiffDelBg}" in diff.content
        assert r"\usepackage{xcolor}" in diff.content
        assert r"\usepackage{framed}" in diff.content


# ── get_pdf_path ───────────────────────────────────────────────────────────


class TestGetPdfPath:
    """PDF 路径校验测试。"""

    def test_invalid_job_id_rejected(self):
        with pytest.raises(ValueError):
            get_pdf_path("not-a-hex-id")

    def test_short_job_id_rejected(self):
        with pytest.raises(ValueError):
            get_pdf_path("abc123")

    def test_valid_job_id_format(self):
        with pytest.raises(FileNotFoundError):
            get_pdf_path("a" * 32)

    def test_valid_job_id_returns_existing_pdf(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / ("b" * 32)
            job.mkdir()
            (job / "diff-highlight.pdf").write_bytes(b"%PDF-1.4 fake")
            monkeypatch.setattr(git_diff_constants, "PDF_OUTPUT_ROOT", root)
            pdf_path = get_pdf_path("b" * 32)
            assert pdf_path.name == "diff-highlight.pdf"
            assert pdf_path.exists()


# ── _resolve_color_hex ──────────────────────────────────────────────────────


class TestResolveColorHex:
    """颜色格式转换测试。"""

    def test_empty_uses_fallback(self):
        assert _resolve_color_hex("", "D7E8B1") == "D7E8B1"

    def test_hash_prefix_stripped(self):
        assert _resolve_color_hex("#FF0000", "D7E8B1") == "FF0000"

    def test_three_char_expanded(self):
        assert _resolve_color_hex("#F00", "D7E8B1") == "FF0000"

    def test_six_char_plain(self):
        assert _resolve_color_hex("00FF00", "D7E8B1") == "00FF00"

    def test_lowercase_uppercased(self):
        assert _resolve_color_hex("#abcdef", "D7E8B1") == "ABCDEF"

    def test_invalid_hex_uses_fallback(self):
        assert _resolve_color_hex("ZZZZZZ", "D7E8B1") == "D7E8B1"

    def test_wrong_length_uses_fallback(self):
        assert _resolve_color_hex("FF00", "D7E8B1") == "D7E8B1"


# ── _build_diff_preamble ────────────────────────────────────────────────────


class TestBuildDiffPreamble:
    """差异 preamble 构建测试。"""

    def test_default_colors(self):
        preamble = _build_diff_preamble()
        assert "D7E8B1" in preamble
        assert "FFA3A3" in preamble
        assert r"\usepackage{xcolor}" in preamble
        assert r"\usepackage{framed}" in preamble

    def test_custom_colors(self):
        preamble = _build_diff_preamble("FF0000", "00FF00")
        assert "FF0000" in preamble
        assert "00FF00" in preamble
        assert "D7E8B1" not in preamble
        assert "FFA3A3" not in preamble


# ── _build_diff_document with colors ────────────────────────────────────────


class TestBuildDiffDocumentColors:
    """差异文档自定义颜色测试。"""

    def test_custom_colors_propagated(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Old." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"New." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(
            base, target, color_add="FF0000", color_del="00FF00"
        )
        assert "FF0000" in diff.content
        assert "00FF00" in diff.content
        assert "LXDiffAdded" in diff.content
        assert "LXDiffDeleted" in diff.content


# ── 依赖展开与编译目录准备 ─────────────────────────────────────────────────


class TestFlattenMainDocument:
    """git 与 worktree 两种模式的依赖展开。"""

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_git_mode_expands_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(
                repo,
                {
                    "main.tex": (
                        "\\documentclass{article}\n"
                        "\\begin{document}\n"
                        "Intro\\input{chapters/a}\n"
                        "\\end{document}\n"
                    ),
                    "chapters/a.tex": "Chapter A content\n",
                },
            )
            context = ProjectContext(
                root=repo,
                git_root=repo,
                project_prefix=PurePosixPath(""),
            )
            with _active_git_env(repo):
                doc = asyncio.run(_flatten_main_document("HEAD", context, "main.tex"))
            assert "Chapter A content" in doc.content
            assert doc.files == ["chapters/a.tex", "main.tex"]

    def test_worktree_mode_expands_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "chapters").mkdir()
            (repo / "main.tex").write_text(
                "\\begin{document}\n\\input{chapters/a}\n\\end{document}\n",
                encoding="utf-8",
            )
            (repo / "chapters" / "a.tex").write_text(
                "worktree content", encoding="utf-8"
            )
            context = ProjectContext(
                root=repo,
                git_root=repo,
                project_prefix=PurePosixPath(""),
            )
            doc = asyncio.run(_flatten_main_document(WORKTREE_REF, context, "main.tex"))
            assert "worktree content" in doc.content
            assert doc.files == ["chapters/a.tex", "main.tex"]


class TestPrepareTargetProject:
    """编译目录准备：commit 模式 git archive 解包、worktree 模式复制过滤。"""

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_commit_mode_extracts_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            _make_git_repo(
                repo,
                {
                    "main.tex": "\\begin{document}x\\end{document}\n",
                    "chapters/a.tex": "A\n",
                },
            )
            context = ProjectContext(
                root=repo,
                git_root=repo,
                project_prefix=PurePosixPath(""),
            )
            with _active_git_env(repo):
                project_dir = asyncio.run(
                    _prepare_target_project("HEAD", context, tmp_path / "job")
                )
            assert (project_dir / "main.tex").exists()
            assert (project_dir / "chapters" / "a.tex").exists()

    def test_commit_mode_mounts_job_dir_for_archive(self, monkeypatch):
        """commit 模式 git archive 必须把 job_dir 显式挂载进沙箱。

        docker 后端只 bind mount cwd 与显式 mounts，容器 /tmp 是独立
        tmpfs——漏挂会让 git archive 报 "could not open ... for writing"。
        """
        captured: dict = {}

        async def fake_run_git_checked(cwd, args, timeout=30, mounts=None):
            captured["args"] = args
            captured["mounts"] = mounts
            # 模拟 git archive 产物，供后续解包流程使用
            out = Path(args[args.index("--output") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr("main.tex", "\\begin{document}x\\end{document}\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(git_diff_process, "run_git_checked", fake_run_git_checked)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            context = ProjectContext(
                root=repo,
                git_root=repo,
                project_prefix=PurePosixPath(""),
            )
            job_dir = tmp_path / "job"
            project_dir = asyncio.run(_prepare_target_project("HEAD", context, job_dir))
            assert captured["mounts"] == [SandboxMount(host_path=str(job_dir))]
            assert "--output" in captured["args"]
            assert (project_dir / "main.tex").exists()

    def test_worktree_mode_ignores_vcs_and_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            (proj / "main.tex").write_text("x", encoding="utf-8")
            (proj / "refs.bib").write_text("@book{x, title={X}}", encoding="utf-8")
            (proj / "main.aux").write_text("aux", encoding="utf-8")
            (proj / "main.log").write_text("log", encoding="utf-8")
            (proj / ".git").mkdir()
            (proj / "node_modules").mkdir()

            context = ProjectContext(
                root=proj,
                git_root=tmp_path,
                project_prefix=PurePosixPath("proj"),
            )
            project_dir = asyncio.run(
                _prepare_target_project(WORKTREE_REF, context, tmp_path / "job")
            )
            names = {p.name for p in project_dir.rglob("*")}
            assert (project_dir / "main.tex").exists()
            assert (project_dir / "refs.bib").exists()
            assert ".git" not in names
            assert "node_modules" not in names
            assert "main.aux" not in names
            assert "main.log" not in names


class TestListGitRevisions:
    """revision 列表：worktree + commit + tag + 脏状态。"""

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_lists_worktree_commit_and_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _make_git_repo(repo, {"main.tex": "v1\n"}, message="one")
            _make_git_repo(repo, {"main.tex": "v2\n"}, message="two")
            env = _git_env(repo)
            subprocess.run(
                ["git", "-c", f"safe.directory={repo}", "-C", str(repo), "tag", "v1"],
                check=True,
                env=env,
            )
            (repo / "dirty.tex").write_text("dirty", encoding="utf-8")

            ws_id = "test-git-diff-" + uuid.uuid4().hex
            project = get_project_manager_for_workspace(ws_id)
            try:
                project.open(str(repo))
                with _active_git_env(repo):
                    result = asyncio.run(list_git_revisions(ws_id))
            finally:
                get_workspace_manager().remove(ws_id)

            kinds = {revision.kind for revision in result.revisions}
            assert {"worktree", "commit", "tag"} <= kinds
            assert result.is_dirty is True


class TestValidateRevision:
    """revision 校验。"""

    def test_worktree_ref_skips_check(self):
        assert asyncio.run(_validate_revision(Path(), WORKTREE_REF)) is None

    def test_empty_ref_rejected(self):
        with pytest.raises(ValueError, match="Git revision"):
            asyncio.run(_validate_revision(Path(), ""))

    def test_option_like_ref_rejected(self, monkeypatch):
        monkeypatch.setattr(git_diff_process, "run_git", AsyncMock())
        with pytest.raises(ValueError, match="must not start with '-'"):
            asyncio.run(_validate_revision(Path(), "--output=x"))

    @pytest.mark.parametrize(
        "ref",
        [
            "HEAD~1",
            "feature..other",
            "abc1234^",
            "foo:bar",
            "foo\\bar",
            "..hidden",
            "bad ref",
        ],
    )
    def test_rev_syntax_rejected(self, monkeypatch, ref):
        monkeypatch.setattr(git_diff_process, "run_git", AsyncMock())
        with pytest.raises(ValueError, match="Invalid Git revision"):
            asyncio.run(_validate_revision(Path(), ref))

    def test_invalid_ref_rejected(self, monkeypatch):
        monkeypatch.setattr(
            git_diff_process,
            "run_git",
            AsyncMock(
                return_value=ProcessResult(
                    returncode=128, stdout="", stderr="fatal: bad revision"
                )
            ),
        )
        with pytest.raises(ValueError, match="Invalid Git revision"):
            asyncio.run(_validate_revision(Path(), "definitely-not-a-ref"))

    @pytest.mark.parametrize(
        "ref",
        [
            "abc1234",
            "0123456789abcdef0123456789abcdef01234567",
            "main",
            "feature/x-y.1",
        ],
    )
    def test_valid_ref_accepted(self, monkeypatch, ref):
        monkeypatch.setattr(
            git_diff_process,
            "run_git",
            AsyncMock(
                return_value=ProcessResult(returncode=0, stdout="resolved", stderr="")
            ),
        )
        assert asyncio.run(_validate_revision(Path(), ref)) is None


# ── render_diff_pdf 主流程 ─────────────────────────────────────────────────


class TestRenderDiffPdf:
    """render_diff_pdf 成功 / 失败 / 配置默认引擎。"""

    @staticmethod
    def _patch_common(monkeypatch, tmp_path: Path, project_dir: Path, config):
        monkeypatch.setattr(git_diff_constants, "PDF_OUTPUT_ROOT", tmp_path / "jobs")
        monkeypatch.setattr(git_diff_render, "get_config", lambda: config)
        monkeypatch.setattr(git_diff_revisions, "get_project_context", AsyncMock())
        monkeypatch.setattr(
            git_diff_render, "_validate_project_path", Mock(return_value="main.tex")
        )
        monkeypatch.setattr(git_diff_revisions, "_validate_revision", AsyncMock())
        monkeypatch.setattr(
            git_diff_flatten,
            "_flatten_main_document",
            AsyncMock(
                side_effect=[
                    FlattenedDocument(content="base doc", files=["main.tex"]),
                    FlattenedDocument(content="target doc", files=["main.tex"]),
                ]
            ),
        )

        async def fake_prepare(_target_ref, _context, job_dir: Path):
            # 真实实现会在 job_dir 中创建项目目录
            job_dir.mkdir(parents=True, exist_ok=True)
            return project_dir

        monkeypatch.setattr(git_diff_render, "_prepare_target_project", fake_prepare)

    def test_success_copies_pdf(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_dir = tmp_path / "project"
            project_dir.mkdir()
            self._patch_common(
                monkeypatch, tmp_path, project_dir, _git_diff_config_stub()
            )
            monkeypatch.setattr(
                git_diff_diff,
                "_build_diff_document",
                Mock(
                    return_value=DiffDocument(
                        content="diff", added_lines=3, deleted_lines=1
                    )
                ),
            )

            async def fake_compile(_dir, rel):
                pdf = project_dir / Path(rel).with_suffix(".pdf")
                pdf.write_bytes(b"%PDF-1.4 fake")
                return TexCompileResult(success=True, filtered_output="", raw_output="")

            monkeypatch.setattr(git_diff_render, "_compile_diff", fake_compile)

            result = asyncio.run(
                git_diff_service.render_diff_pdf("main.tex", "HEAD~1", "HEAD")
            )
            assert result.success is True
            assert result.added_lines == 3
            assert result.deleted_lines == 1
            job_dir = tmp_path / "jobs" / result.job_id
            assert (job_dir / "diff-highlight.pdf").read_bytes().startswith(b"%PDF")
            assert result.pdf_url == f"/api/git-diff/pdf/{result.job_id}"

    def test_failure_returns_translated_errors(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_dir = tmp_path / "project"
            project_dir.mkdir()
            self._patch_common(
                monkeypatch, tmp_path, project_dir, _git_diff_config_stub()
            )
            monkeypatch.setattr(
                git_diff_diff,
                "_build_diff_document",
                Mock(
                    return_value=DiffDocument(
                        content="diff", added_lines=1, deleted_lines=1
                    )
                ),
            )

            async def fake_compile(_dir, _rel):
                return TexCompileResult(
                    success=False, filtered_output="boom", raw_output="boom"
                )

            monkeypatch.setattr(git_diff_render, "_compile_diff", fake_compile)
            monkeypatch.setattr(
                git_diff_render,
                "translate_compile_errors",
                AsyncMock(return_value=("中文解释", ["修复建议"])),
            )

            result = asyncio.run(
                git_diff_service.render_diff_pdf("main.tex", "HEAD~1", "HEAD")
            )
            assert result.success is False
            assert result.explanation == "中文解释"
            assert result.fixes == ["修复建议"]

    def test_config_default_engine_applied(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_dir = tmp_path / "project"
            project_dir.mkdir()
            self._patch_common(
                monkeypatch,
                tmp_path,
                project_dir,
                _git_diff_config_stub(engine="latexdiff", markup="CTRADITIONAL"),
            )
            latexdiff_mock = AsyncMock(return_value=("diff", 5, 2))
            monkeypatch.setattr(
                git_diff_diff, "_build_latexdiff_document", latexdiff_mock
            )

            async def fake_compile(_dir, rel):
                pdf = project_dir / Path(rel).with_suffix(".pdf")
                pdf.write_bytes(b"%PDF-1.4 fake")
                return TexCompileResult(success=True, filtered_output="", raw_output="")

            monkeypatch.setattr(git_diff_render, "_compile_diff", fake_compile)

            result = asyncio.run(
                git_diff_service.render_diff_pdf("main.tex", "HEAD~1", "HEAD")
            )
            assert result.success is True
            assert result.diff_engine == "latexdiff"
            assert result.added_lines == 5
            assert latexdiff_mock.await_count == 1
            assert latexdiff_mock.await_args.kwargs["markup_type"] == "CTRADITIONAL"


class TestLatexdiffMissing:
    """latexdiff 未安装时给出可读错误。"""

    def test_missing_command_raises_helpful_error(self, monkeypatch):
        async def missing(*args, **kwargs):
            raise FileNotFoundError("latexdiff")

        monkeypatch.setattr(git_diff_diff, "_run_latexdiff", missing)
        with pytest.raises(RuntimeError, match="latexdiff command not found"):
            asyncio.run(git_diff_service._build_latexdiff_document("old", "new"))


# ── API 端点 ───────────────────────────────────────────────────────────────


class TestGitDiffEndpoints:
    """/options、/refs、/render-pdf 三个端点的成功与错误路径。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_options_returns_config_defaults(self, client, monkeypatch):
        monkeypatch.setattr(
            git_diff_router,
            "get_config",
            lambda: _git_diff_config_stub(
                engine="latexdiff", markup="CTRADITIONAL", math="coarse"
            ),
        )
        response = client.get("/api/git-diff/options")
        assert response.status_code == 200
        data = response.json()
        assert data["default_engine"] == "latexdiff"
        assert data["default_markup_type"] == "CTRADITIONAL"
        assert data["default_math_markup"] == "coarse"
        assert "UNDERLINE" in data["markup_types"]
        assert "fine" in data["math_markups"]

    def test_refs_success(self, client, monkeypatch):
        fake = GitRefsResult(
            git_root="C:/repo",
            revisions=[
                GitRevision(value=WORKTREE_REF, label="worktree", kind="worktree")
            ],
        )
        monkeypatch.setattr(
            git_diff_router, "list_git_revisions", AsyncMock(return_value=fake)
        )
        response = client.get("/api/git-diff/refs?workspace_id=ws-1")
        assert response.status_code == 200
        assert response.json()["revisions"][0]["kind"] == "worktree"

    def test_refs_runtime_error_returns_400(self, client, monkeypatch):
        monkeypatch.setattr(
            git_diff_router,
            "list_git_revisions",
            AsyncMock(side_effect=RuntimeError("No project opened")),
        )
        response = client.get("/api/git-diff/refs?workspace_id=ws-1")
        assert response.status_code == 400

    def test_render_pdf_success(self, client, monkeypatch):
        fake = DiffHighlightResult(
            success=True, job_id="a" * 32, pdf_url="/api/git-diff/pdf/aaa"
        )
        monkeypatch.setattr(
            git_diff_router, "render_diff_pdf", AsyncMock(return_value=fake)
        )
        response = client.post(
            "/api/git-diff/render-pdf?workspace_id=ws-1",
            json={"main_file": "main.tex", "base_ref": "a", "target_ref": "b"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_render_pdf_value_error_returns_400(self, client, monkeypatch):
        monkeypatch.setattr(
            git_diff_router,
            "render_diff_pdf",
            AsyncMock(side_effect=ValueError("Invalid Git revision: x")),
        )
        response = client.post(
            "/api/git-diff/render-pdf?workspace_id=ws-1",
            json={"main_file": "main.tex", "base_ref": "a", "target_ref": "b"},
        )
        assert response.status_code == 400

    def test_render_pdf_file_not_found_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(
            git_diff_router,
            "render_diff_pdf",
            AsyncMock(side_effect=FileNotFoundError("File not found in revision")),
        )
        response = client.post(
            "/api/git-diff/render-pdf?workspace_id=ws-1",
            json={"main_file": "main.tex", "base_ref": "a", "target_ref": "b"},
        )
        assert response.status_code == 404


# ── _count_latexdiff_changes ────────────────────────────────────────────────


class TestCountLatexdiffChanges:
    """latexdiff 输出变更统计测试。"""

    def test_no_changes(self):
        added, deleted = _count_latexdiff_changes("just plain text")
        assert added == 0
        assert deleted == 0

    def test_count_additions(self):
        content = (
            r"normal line" + "\n"
            r"\DIFadd{new content}" + "\n"
            r"\DIFadd{another addition}" + "\n"
        )
        added, deleted = _count_latexdiff_changes(content)
        assert added == 2
        assert deleted == 0

    def test_count_deletions(self):
        content = r"normal line" + "\n" r"\DIFdel{old content}" + "\n"
        added, deleted = _count_latexdiff_changes(content)
        assert added == 0
        assert deleted == 1

    def test_count_mixed(self):
        content = (
            r"\DIFdel{removed}" + "\n"
            r"kept line" + "\n"
            r"\DIFadd{added}" + "\n"
            r"\DIFDELTAMARK" + "\n"
        )
        added, deleted = _count_latexdiff_changes(content)
        assert added == 1
        assert deleted == 2


# ── _strip_blank_lines ───────────────────────────────────────────────────


class TestStripBlankLines:
    """空白行清理测试。"""

    def test_empty_content(self):
        assert _strip_blank_lines("") == ""

    def test_no_blank_lines(self):
        content = "line1\nline2\nline3"
        assert _strip_blank_lines(content) == content

    def test_single_blank_line_removed(self):
        assert _strip_blank_lines("a\n\nb") == "a\nb"

    def test_consecutive_blank_lines_removed(self):
        assert _strip_blank_lines("a\n\n\n\nb") == "a\nb"

    def test_blank_lines_with_whitespace_only(self):
        assert _strip_blank_lines("a\n   \n  \t\nb") == "a\nb"

    def test_blank_lines_inside_math_mode(self):
        content = "$$\n\nE = mc^2\n\n$$"
        assert _strip_blank_lines(content) == "$$\nE = mc^2\n$$"

    def test_blank_lines_inside_textbf(self):
        content = "\\textbf{hello\n\nworld}"
        assert _strip_blank_lines(content) == "\\textbf{hello\nworld}"

    def test_blank_lines_inside_lstdefinelanguage(self):
        content = (
            r"\lstdefinelanguage{DIFcode}{" + "\n"
            "  moredelim=...\n"
            "\n"
            "  moredelim=...\n"
            "\n"
            "}"
        )
        expected = (
            r"\lstdefinelanguage{DIFcode}{" + "\n"
            "  moredelim=...\n"
            "  moredelim=...\n"
            "}"
        )
        assert _strip_blank_lines(content) == expected

    def test_latexdiff_output_with_blank_lines(self):
        content = (
            r"\DIFadd{" + "\n"
            "new line\n"
            "\n"
            "}\n"
            "unchanged\n"
            r"\DIFdel{" + "\n"
            "old line\n"
            "\n"
            "}"
        )
        expected = (
            r"\DIFadd{" + "\n"
            "new line\n"
            "}\n"
            "unchanged\n"
            r"\DIFdel{" + "\n"
            "old line\n"
            "}"
        )
        assert _strip_blank_lines(content) == expected

    def test_preserves_normal_content(self):
        content = "\\begin{document}\nHello world.\n\\end{document}"
        assert _strip_blank_lines(content) == content


# ── /api/git-diff/pdf 端点 ─────────────────────────────────────────────────


class TestDownloadPdfEndpoint:
    """/api/git-diff/pdf/{job_id} 端点测试。

    验证 PDF 以 ``Content-Disposition: inline`` 返回，便于浏览器 iframe
    内联预览，而不是触发自动下载。
    """

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_pdf_served_inline(self, client, tmp_path):
        """PDF 存在时返回 200 且 Content-Disposition 为 inline。"""

        job_id = "a" * 32
        pdf_dir = tmp_path / "diff_pdfs"
        pdf_dir.mkdir()
        pdf_path = pdf_dir / f"{job_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake diff pdf")

        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            return_value=pdf_path,
        ):
            response = client.get(
                f"/api/git-diff/pdf/{job_id}", params={"workspace_id": "ws-default"}
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        disposition = response.headers.get("content-disposition", "")
        assert "inline" in disposition
        assert "attachment" not in disposition
        assert b"%PDF" in response.content

    def test_pdf_invalid_job_id_returns_400(self, client):
        """非法 job_id 返回 400。"""
        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            side_effect=ValueError("invalid job id"),
        ):
            response = client.get(
                "/api/git-diff/pdf/not-a-hex-id",
                params={"workspace_id": "ws-default"},
            )

        assert response.status_code == 400

    def test_pdf_not_found_returns_404(self, client):
        """PDF 不存在时返回 404。"""
        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            side_effect=FileNotFoundError("missing"),
        ):
            response = client.get(
                f"/api/git-diff/pdf/{'a' * 32}",
                params={"workspace_id": "ws-default"},
            )

        assert response.status_code == 404

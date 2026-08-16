r"""
Git 差异高亮 PDF 生成服务。

该模块只读取当前打开项目和 Git 对象，将目标版本复制到临时目录，
再把主文件及其 \input / \include / \subfile 依赖展开后生成带底纹的
差异 LaTeX 文档，最后在临时目录中编译 PDF，不写回用户项目。
"""

import asyncio
import difflib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath


from pydantic import BaseModel, ConfigDict

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import (
    run_tex_compile,
    terminate_process_tree,
    TexCompileResult,
)

logger = get_logger(__name__)

WORKTREE_REF = "__WORKTREE__"
PDF_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "latexcellence_git_diff"
_MAX_KEPT_JOB_DIRS = 20

_INPUT_PATTERN = re.compile(
    r"\\(?P<cmd>input|include|subfile)\s*\{(?P<path>[^{}\n]+)\}"
)
# Git revision 白名单格式：完整/短提交哈希，或普通分支/标签名。
# 分支/标签名禁止 `..`、`@`、`~`、`^`、`:`、`\`、`//` 等会改变
# revision 解析语义或注入选项的字符（git 本身也禁止这些字符入名）。
_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REF_FORBIDDEN_SUBSTRINGS = ("..", "@", "~", "^", ":", "\\", "//", "/.", "./")
_BEGIN_DOCUMENT = r"\begin{document}"
_END_DOCUMENT = r"\end{document}"

# 浮动体环境：LaTeX 的 table/figure 等环境要求 outer par mode，
# 不能出现在 LXDiffAdded / LXDiffDeleted（framed 颜色框）内部，
# 否则编译会报 "Not in outer par mode"。
# 生成差异时这些环境整体不包裹高亮，只高亮其周围的普通文本。
_FLOAT_ENV_NAMES = frozenset(
    {
        "table",
        "table*",
        "figure",
        "figure*",
        "algorithm",
        "algorithm*",
        "wrapfigure",
        "wraptable",
        "sidewaystable",
        "sidewaysfigure",
    }
)

# 行内 \begin{...} / \end{...} 环境边界，用于把浮动体从高亮包裹中拆出。
# 必须带捕获组：re.split 只有在存在捕获组时才会把匹配到的 token 一并返回。
_ENV_TOKEN_RE = re.compile(r"(\\(?:begin|end)\{[^{}\n]+\})")

# latexdiff 支持的标记类型
LATEXDIFF_TYPES = [
    "UNDERLINE",
    "CTRADITIONAL",
    "CFONT",
    "CHANGEBAR",
    "CCHANGEBAR",
    "CULINECHBAR",
    "FONTSTRIKE",
    "INVISIBLE",
]

# latexdiff 支持的数学公式标记粒度
LATEXDIFF_MATH_MARKUPS = ["fine", "coarse", "off", "whole"]

_DIFF_PREAMBLE_TEMPLATE = r"""
% LaTeXcellence Git diff highlight support.
\usepackage{{xcolor}}
\usepackage{{framed}}
\definecolor{{LXDiffAddBg}}{{HTML}}{{{color_add}}}
\definecolor{{LXDiffDelBg}}{{HTML}}{{{color_del}}}
\newenvironment{{LXDiffAdded}}{{
  \def\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffAddBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{
  \endMakeFramed%
}}
\newenvironment{{LXDiffDeleted}}{{
  \def\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffDelBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{
  \endMakeFramed%
}}
"""

_DEFAULT_COLOR_ADD_HEX = "D7E8B1"
_DEFAULT_COLOR_DEL_HEX = "FFA3A3"


class GitRevision(BaseModel):
    """前端可选择的 Git revision。"""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    kind: str
    short_hash: str = ""
    subject: str = ""


class GitRefsResult(BaseModel):
    """Git revision 列表。"""

    model_config = ConfigDict(extra="forbid")

    git_root: str
    current_branch: str = ""
    is_dirty: bool = False
    revisions: list[GitRevision]


class DiffHighlightResult(BaseModel):
    """差异高亮 PDF 生成结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    job_id: str = ""
    pdf_url: str = ""
    main_file: str = ""
    base_ref: str = ""
    target_ref: str = ""
    diff_engine: str = "difflib"
    added_lines: int = 0
    deleted_lines: int = 0
    included_files: list[str] = []
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
    fixes: list[str] = []


@dataclass
class ProcessResult:
    """子进程执行结果。"""

    returncode: int
    stdout: str
    stderr: str


@dataclass
class ProjectContext:
    """当前打开项目和 Git 根目录上下文。"""

    root: Path
    git_root: Path
    project_prefix: PurePosixPath


@dataclass
class FlattenContext:
    """展开 LaTeX 依赖时共享的状态。"""

    ref: str
    project: ProjectContext
    visited: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


@dataclass
class FlattenedDocument:
    """展开后的主文档。"""

    content: str
    files: list[str]


@dataclass
class DiffDocument:
    """带差异标记的 LaTeX 文档。"""

    content: str
    added_lines: int
    deleted_lines: int


async def list_git_revisions(workspace_id: str = "") -> GitRefsResult:
    """列出当前项目可选的 Git revision。"""

    context = await _get_project_context(workspace_id)
    current_branch = await _get_optional_git_output(
        context.git_root,
        ["branch", "--show-current"],
    )
    status = await _get_optional_git_output(
        context.git_root,
        ["status", "--porcelain"],
    )

    revisions = [
        GitRevision(
            value=WORKTREE_REF,
            label="Current worktree",
            kind="worktree",
            subject="Contains uncommitted changes and untracked files",
        )
    ]
    revisions.extend(await _list_commit_revisions(context.git_root))
    revisions.extend(
        await _list_ref_revisions(context.git_root, "refs/heads", "branch")
    )
    revisions.extend(await _list_ref_revisions(context.git_root, "refs/tags", "tag"))

    deduped = []
    seen = set()
    for revision in revisions:
        key = (revision.kind, revision.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(revision)

    return GitRefsResult(
        git_root=str(context.git_root),
        current_branch=current_branch.strip(),
        is_dirty=bool(status.strip()),
        revisions=deduped,
    )


async def render_diff_pdf(
    main_file: str,
    base_ref: str,
    target_ref: str,
    workspace_id: str = "",
    *,
    diff_engine: str | None = None,
    markup_type: str | None = None,
    math_markup: str | None = None,
    color_add: str = "",
    color_del: str = "",
    exclude_safecmd: str = "",
    exclude_textcmd: str = "",
) -> DiffHighlightResult:
    """生成两个 Git 状态之间的差异高亮 PDF。"""

    context = await _get_project_context(workspace_id)
    main_rel = _validate_project_path(context.root, main_file, require_tex=True)

    await _validate_revision(context.git_root, base_ref)
    await _validate_revision(context.git_root, target_ref)

    base_flattened = await _flatten_main_document(base_ref, context, main_rel)
    target_flattened = await _flatten_main_document(target_ref, context, main_rel)

    cfg = get_config()
    if diff_engine is None:
        diff_engine = cfg.features.git_diff.default_engine
    if markup_type is None:
        markup_type = cfg.features.git_diff.default_markup_type
    if math_markup is None:
        math_markup = cfg.features.git_diff.default_math_markup
    if not color_add:
        color_add = cfg.features.git_diff.default_color_add
    if not color_del:
        color_del = cfg.features.git_diff.default_color_del

    use_latexdiff = diff_engine == "latexdiff"

    if use_latexdiff:
        diff_content, added_lines, deleted_lines = await _build_latexdiff_document(
            base_flattened.content,
            target_flattened.content,
            markup_type=markup_type,
            math_markup=math_markup,
            exclude_safecmd=exclude_safecmd,
            exclude_textcmd=exclude_textcmd,
        )
    else:
        diff_doc = _build_diff_document(
            base_flattened.content,
            target_flattened.content,
            color_add=color_add,
            color_del=color_del,
        )
        diff_content = diff_doc.content
        added_lines = diff_doc.added_lines
        deleted_lines = diff_doc.deleted_lines

    job_id = uuid.uuid4().hex
    job_dir = PDF_OUTPUT_ROOT / job_id
    project_dir = await _prepare_target_project(target_ref, context, job_dir)
    diff_rel = _diff_main_path(main_rel, job_id)
    diff_path = project_dir / Path(diff_rel)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_content, encoding="utf-8")

    logger.info(
        f"Rendering Git diff PDF: {base_ref}..{target_ref} main={main_rel} job={job_id} "
        f"engine={diff_engine}"
    )

    compile_result = await _compile_diff(project_dir, diff_rel)
    pdf_source = diff_path.with_suffix(".pdf")
    await asyncio.to_thread(_prune_old_job_dirs)

    included_files = sorted(set(base_flattened.files) | set(target_flattened.files))

    if not compile_result.success or not pdf_source.exists():
        logger.warning(f"Git diff PDF compilation failed for job={job_id}")
        explanation, fixes = await translate_compile_errors(
            compile_result.raw_output,
            filtered_output=compile_result.filtered_output,
        )
        if not explanation:
            explanation = "Diff PDF generated but compilation failed. Please refer to the raw output to diagnose the issue."
        return DiffHighlightResult(
            success=False,
            job_id=job_id,
            main_file=main_rel,
            base_ref=base_ref,
            target_ref=target_ref,
            diff_engine=diff_engine,
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            included_files=included_files,
            stdout=compile_result.raw_output,
            stderr=compile_result.filtered_output,
            explanation=explanation,
            fixes=fixes,
        )

    public_pdf = job_dir / "diff-highlight.pdf"
    shutil.copy2(pdf_source, public_pdf)

    return DiffHighlightResult(
        success=True,
        job_id=job_id,
        pdf_url=f"/api/git-diff/pdf/{job_id}",
        main_file=main_rel,
        base_ref=base_ref,
        target_ref=target_ref,
        diff_engine=diff_engine,
        added_lines=added_lines,
        deleted_lines=deleted_lines,
        included_files=included_files,
        stdout=compile_result.raw_output,
        stderr=compile_result.filtered_output,
    )


def get_pdf_path(job_id: str) -> Path:
    """返回已生成 PDF 的路径。"""

    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid job id")
    pdf_path = PDF_OUTPUT_ROOT / job_id / "diff-highlight.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError("Diff PDF not found")
    return pdf_path


def _prune_old_job_dirs() -> None:
    """清理过期的差异 PDF job 目录，只保留最近生成的若干份。

    每次渲染都会把整个项目复制进系统临时目录，若不清理会持续占用磁盘。
    成功与失败的 job 目录都统一按生成时间保留最新的若干份。
    """
    if not PDF_OUTPUT_ROOT.exists():
        return
    entries = [
        entry
        for entry in PDF_OUTPUT_ROOT.iterdir()
        if entry.is_dir() and re.fullmatch(r"[a-f0-9]{32}", entry.name)
    ]
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    for stale in entries[_MAX_KEPT_JOB_DIRS:]:
        shutil.rmtree(stale, ignore_errors=True)
        logger.info(f"Pruned stale git diff job dir: {stale.name}")


async def _get_project_context(workspace_id: str = "") -> ProjectContext:
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise RuntimeError("No project opened")

    root = Path(project.root_path).resolve()
    git_root_output = await _run_git_checked(root, ["rev-parse", "--show-toplevel"])
    git_root = Path(git_root_output.stdout.strip()).resolve()

    try:
        relative_prefix = root.relative_to(git_root)
    except ValueError as exc:
        raise RuntimeError("Opened project is not inside its Git repository") from exc

    prefix = PurePosixPath(relative_prefix.as_posix())
    if prefix.as_posix() == ".":
        prefix = PurePosixPath("")

    return ProjectContext(root=root, git_root=git_root, project_prefix=prefix)


async def _list_commit_revisions(git_root: Path) -> list[GitRevision]:
    result = await _run_git(
        git_root,
        ["log", "--pretty=format:%H%x00%h%x00%s", "-n", "50"],
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 全新仓库还没有任何提交，返回空列表（只保留 worktree 可选）
        if "does not have any commits yet" in stderr:
            return []
        raise RuntimeError(stderr or result.stdout.strip() or "Git log failed")

    revisions = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) < 3:
            continue
        full_hash, short_hash, subject = parts[0], parts[1], parts[2]
        revisions.append(
            GitRevision(
                value=full_hash,
                label=f"{short_hash} {subject}",
                kind="commit",
                short_hash=short_hash,
                subject=subject,
            )
        )
    return revisions


async def _list_ref_revisions(
    git_root: Path, ref_prefix: str, kind: str
) -> list[GitRevision]:
    result = await _run_git_checked(
        git_root,
        [
            "for-each-ref",
            ref_prefix,
            "--format=%(refname:short)%00%(objectname)%00%(objectname:short)%00%(subject)",
        ],
    )
    revisions = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) < 4:
            continue
        name, full_hash, short_hash, subject = parts[0], parts[1], parts[2], parts[3]
        revisions.append(
            GitRevision(
                value=name,
                label=f"{name} ({short_hash})",
                kind=kind,
                short_hash=short_hash,
                subject=subject or full_hash,
            )
        )
    return revisions


async def _validate_revision(git_root: Path, ref: str) -> None:
    """校验 Git revision：只接受工作区标记、哈希或普通分支/标签名。

    拒绝任意 commit-ish（HEAD~1、refs/... 语法、range 等），并使用
    ``--end-of-options`` 隔离参数，防止以 ``-`` 开头的输入被当作 git 选项。
    """
    if ref == WORKTREE_REF:
        return
    if not ref or ref.strip() != ref:
        raise ValueError("Git revision must not be empty or padded with spaces")
    if ref.startswith("-"):
        raise ValueError("Git revision must not start with '-'")
    if not (_COMMIT_HASH_RE.fullmatch(ref) or _REF_NAME_RE.fullmatch(ref)):
        raise ValueError(f"Invalid Git revision: {ref}")
    if ref.endswith(("/", ".")) or any(
        token in ref for token in _REF_FORBIDDEN_SUBSTRINGS
    ):
        raise ValueError(f"Invalid Git revision: {ref}")
    result = await _run_git(
        git_root,
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"Invalid Git revision: {ref}")


async def _flatten_main_document(
    ref: str,
    project: ProjectContext,
    main_rel: str,
) -> FlattenedDocument:
    context = FlattenContext(ref=ref, project=project)
    content = await _flatten_file(context, main_rel)
    return FlattenedDocument(content=content, files=sorted(context.files))


async def _flatten_file(context: FlattenContext, file_rel: str) -> str:
    if file_rel in context.visited:
        return ""
    context.visited.add(file_rel)
    context.files.add(file_rel)

    content = await _read_revision_file(
        context.project,
        context.ref,
        file_rel,
        required=True,
    )
    if content is None:
        raise FileNotFoundError(f"File not found in revision {context.ref}: {file_rel}")

    expanded_lines = []
    for line in content.splitlines():
        expanded = await _expand_input_line(context, file_rel, line)
        expanded_lines.extend(expanded.splitlines() or [""])
    return "\n".join(expanded_lines)


async def _expand_input_line(
    context: FlattenContext, current_file: str, line: str
) -> str:
    stripped = line.lstrip()
    if stripped.startswith("%") or "\\" not in line:
        return line

    result_parts = []
    cursor = 0
    matched = False

    for match in _INPUT_PATTERN.finditer(line):
        include_path = match.group("path").strip()
        resolved = await _resolve_include_path(context, current_file, include_path)
        if resolved is None:
            continue

        matched = True
        result_parts.append(line[cursor : match.start()])
        nested = await _flatten_file(context, resolved)
        if match.group("cmd") == "include":
            nested = f"\\clearpage\n{nested}\n\\clearpage"
        result_parts.append(nested)
        cursor = match.end()

    if not matched:
        return line

    result_parts.append(line[cursor:])
    return "".join(result_parts)


async def _resolve_include_path(
    context: FlattenContext,
    current_file: str,
    include_path: str,
) -> str | None:
    normalized = _normalize_include_path(include_path)
    if normalized is None:
        return None

    path = PurePosixPath(normalized)
    # 拒绝绝对路径和目录穿越：include 只允许项目内相对路径
    if path.is_absolute() or PureWindowsPath(normalized).is_absolute():
        return None
    if ".." in path.parts:
        return None
    if path.suffix == "":
        path = path.with_suffix(".tex")

    current_parent = PurePosixPath(current_file).parent
    candidates = [
        (current_parent / path).as_posix(),
        path.as_posix(),
    ]

    seen = set()
    for candidate in candidates:
        clean_candidate = _normalize_include_path(candidate)
        if clean_candidate is None or clean_candidate in seen:
            continue
        seen.add(clean_candidate)
        content = await _read_revision_file(
            context.project,
            context.ref,
            clean_candidate,
            required=False,
        )
        if content is not None:
            return clean_candidate

    return None


async def _read_revision_file(
    project: ProjectContext,
    ref: str,
    project_rel: str,
    required: bool,
) -> str | None:
    if ref == WORKTREE_REF:
        try:
            path = resolve_project_path(project.root, project_rel)
        except ValueError:
            # 非法路径（含 ..、绝对路径、符号链接逃逸）按“文件不存在”处理
            if required:
                raise
            return None
        if not path.exists() or not path.is_file():
            if required:
                raise FileNotFoundError(f"File not found: {project_rel}")
            return None
        return path.read_text(encoding="utf-8")

    repo_rel = _repo_path(project, project_rel)
    result = await _run_git(
        project.git_root,
        ["show", f"{ref}:{repo_rel}"],
        timeout=30,
    )
    if result.returncode != 0:
        if required:
            raise FileNotFoundError(f"File not found in revision {ref}: {project_rel}")
        return None
    return result.stdout


def _resolve_color_hex(color: str, fallback: str) -> str:
    """将用户颜色输入（HEX #xxx / #xxxxxx 或直接 hex）统一为 6 位大写 HEX。"""
    if not color:
        return fallback
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = c[0] * 2 + c[1] * 2 + c[2] * 2
    if len(c) != 6:
        return fallback
    try:
        int(c, 16)
    except ValueError:
        return fallback
    return c.upper()


def _build_diff_preamble(color_add: str = "", color_del: str = "") -> str:
    """用指定颜色构建差异标记 LaTeX preamble。"""
    add_hex = _resolve_color_hex(color_add, _DEFAULT_COLOR_ADD_HEX)
    del_hex = _resolve_color_hex(color_del, _DEFAULT_COLOR_DEL_HEX)
    return _DIFF_PREAMBLE_TEMPLATE.format(color_add=add_hex, color_del=del_hex)


def _strip_blank_lines(content: str) -> str:
    """删除 LaTeX 内容中的所有空白行。

    latexdiff 在差异合并时会向受限上下文（数学模式、\\lstdefinelanguage、
    \\textbf{} 等 braced argument）内插入空白行。空白行在 TeX 中等价于
    ``\\par``，而在受限上下文中 ``\\par`` 是非法的，会导致
    ``Paragraph ended before...`` 等编译错误。

    删除全部空白行可彻底消除此类问题，同时保留所有 diff 标记
    （\\DIFadd / \\DIFdel）和文档结构。段落间距会略有变化，
    但对差异预览 PDF 的可读性影响极小。
    """
    return "\n".join(line for line in content.splitlines() if line.strip() != "")


async def _build_latexdiff_document(
    base_content: str,
    target_content: str,
    *,
    markup_type: str = "UNDERLINE",
    math_markup: str = "fine",
    exclude_safecmd: str = "",
    exclude_textcmd: str = "",
) -> tuple[str, int, int]:
    """使用 latexdiff 引擎生成语义级差异文档。

    Returns:
        (diff_content, added_lines, deleted_lines)
    """
    with tempfile.TemporaryDirectory(prefix="latexcellence_latexdiff_") as tmp_dir:
        tmp = Path(tmp_dir)
        old_file = tmp / "old.tex"
        new_file = tmp / "new.tex"
        old_file.write_text(base_content, encoding="utf-8")
        new_file.write_text(target_content, encoding="utf-8")

        try:
            result = await _run_latexdiff(
                old_file,
                new_file,
                markup_type=markup_type,
                math_markup=math_markup,
                exclude_safecmd=exclude_safecmd,
                exclude_textcmd=exclude_textcmd,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"latexdiff command not found. Please confirm that TeX Live is installed and that latexdiff is in your PATH: {exc}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            msg = (
                stderr
                or "latexdiff exited with a non-zero exit code, but no error output was produced"
            )
            if not result.stdout.strip():
                raise RuntimeError(f"latexdiff failed: {msg}")
            logger.warning(
                f"latexdiff exited with a non-zero exit code, but there is still some output, which may be incomplete: {msg}"
            )

        diff_content = _strip_blank_lines(result.stdout)
        added, deleted = _count_latexdiff_changes(diff_content)
        return diff_content, added, deleted


def _build_diff_document(
    base_content: str,
    target_content: str,
    color_add: str = "",
    color_del: str = "",
) -> DiffDocument:
    target_preamble, _target_begin, target_body, target_end, target_tail = (
        _split_document(target_content)
    )
    _base_preamble, _base_begin, base_body, _base_end, _base_tail = _split_document(
        base_content
    )

    body_lines, added_lines, deleted_lines = _diff_lines(
        base_body.splitlines(),
        target_body.splitlines(),
    )

    preamble = _inject_diff_preamble(target_preamble, color_add, color_del)
    end_document = target_end or _END_DOCUMENT
    content = (
        "\n".join(
            [
                preamble.rstrip(),
                _BEGIN_DOCUMENT,
                *body_lines,
                end_document,
                target_tail.lstrip("\n"),
            ]
        ).rstrip()
        + "\n"
    )

    return DiffDocument(
        content=content,
        added_lines=added_lines,
        deleted_lines=deleted_lines,
    )


def _split_document(content: str) -> tuple[str, str, str, str, str]:
    begin_index = _find_document_boundary(content, _BEGIN_DOCUMENT, find_last=False)
    if begin_index < 0:
        return "", "", content, "", ""

    begin_end = begin_index + len(_BEGIN_DOCUMENT)
    end_index = _find_document_boundary(content, _END_DOCUMENT, find_last=True)
    if end_index > begin_end:
        return (
            content[:begin_index],
            content[begin_index:begin_end],
            content[begin_end:end_index],
            content[end_index : end_index + len(_END_DOCUMENT)],
            content[end_index + len(_END_DOCUMENT) :],
        )

    return (
        content[:begin_index],
        content[begin_index:begin_end],
        content[begin_end:],
        "",
        "",
    )


def _find_document_boundary(content: str, boundary: str, find_last: bool) -> int:
    """在 content 中查找 boundary 的位置，跳过注释行内的匹配。

    LaTeX 注释以 ``%`` 开始到行尾，其中出现的 ``\\begin{document}`` 或
    ``\\end{document}`` 不应被识别为文档边界。
    """

    offset = 0
    found = -1
    for line in content.split("\n"):
        unescaped = line.replace(r"\%", "")
        comment_pos = unescaped.find("%")
        search_region = line if comment_pos < 0 else line[:comment_pos]

        search_start = 0
        while True:
            pos = search_region.find(boundary, search_start)
            if pos < 0:
                break
            found = offset + pos
            if not find_last:
                return found
            search_start = pos + 1

        offset += len(line) + 1

    return found


def _diff_lines(
    base_lines: list[str], target_lines: list[str]
) -> tuple[list[str], int, int]:
    matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
    output = []
    added_lines = 0
    deleted_lines = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            output.extend(target_lines[j1:j2])
            continue
        if tag in ("delete", "replace"):
            deleted = base_lines[i1:i2]
            output.extend(_wrap_diff_block("LXDiffDeleted", deleted))
            deleted_lines += len(deleted)
        if tag in ("insert", "replace"):
            added = target_lines[j1:j2]
            output.extend(_wrap_diff_block("LXDiffAdded", added))
            added_lines += len(added)

    return output, added_lines, deleted_lines


def _wrap_diff_block(environment: str, lines: list[str]) -> list[str]:
    """将差异行包裹为 LXDiffAdded / LXDiffDeleted 高亮块。

    framed 颜色框不允许浮动体（table/figure 等）出现，否则编译报
    "Not in outer par mode"。因此遇到浮动体环境时，会把高亮块从
    浮动体前后拆开：浮动体本身（含其全部内容）保持不包裹，
    前后的普通文本仍正常高亮。浮动体必须位于独立段落中（前后留空行），
    否则紧贴 framed 框时 TeX 会报 "Float(s) lost"。
    """

    if not lines:
        return []
    content = lines if any(line.strip() for line in lines) else ["~"]

    output: list[str] = []
    buffer: list[str] = []
    float_stack: list[str] = []

    def in_float() -> bool:
        return bool(float_stack)

    def flush_buffer() -> None:
        if not buffer:
            return
        output.extend([f"\\begin{{{environment}}}", *buffer, f"\\end{{{environment}}}"])
        buffer.clear()

    def ensure_blank_separator() -> None:
        if output and output[-1] != "":
            output.append("")

    for line in content:
        if not line.strip():
            # 空白行原样保留（包裹内是段落分隔；浮动体内保持原样）
            if in_float():
                output.append(line)
            else:
                buffer.append(line)
            continue

        for fragment in _ENV_TOKEN_RE.split(line):
            if not fragment:
                continue
            if not _ENV_TOKEN_RE.fullmatch(fragment):
                # 普通文本：浮动体内直接输出，否则先暂存待包裹
                if in_float():
                    output.append(fragment)
                else:
                    buffer.append(fragment)
                continue

            token = fragment
            if token.startswith("\\begin"):
                name = token[len("\\begin{") : -1]
                if name in _FLOAT_ENV_NAMES:
                    if not in_float():
                        flush_buffer()
                        ensure_blank_separator()
                    float_stack.append(name)
                elif in_float():
                    # 浮动体内部的环境（如 tabular）随浮动体一起不包裹
                    pass
                else:
                    buffer.append(token)
                    continue
                output.append(token)
                continue

            # \end{...}
            name = token[len("\\end{") : -1]
            if name in _FLOAT_ENV_NAMES:
                if float_stack and float_stack[-1] == name:
                    float_stack.pop()
                elif name in float_stack:
                    float_stack.remove(name)
                output.append(token)
                if not in_float():
                    # 浮动体结束后回到普通段落，与后续包裹之间留空行
                    flush_buffer()
                    output.append("")
            elif in_float():
                output.append(token)
            else:
                buffer.append(token)

    flush_buffer()
    return output


def _inject_diff_preamble(
    preamble: str,
    color_add: str = "",
    color_del: str = "",
) -> str:
    diff_preamble = _build_diff_preamble(color_add, color_del)
    return preamble.rstrip() + "\n" + diff_preamble.strip() + "\n"


async def _prepare_target_project(
    target_ref: str,
    context: ProjectContext,
    job_dir: Path,
) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    if target_ref == WORKTREE_REF:
        project_dir = job_dir / "project"
        shutil.copytree(context.root, project_dir, ignore=_ignore_copy_names)
        return project_dir

    archive_path = job_dir / "target.zip"
    archive_args = [
        "archive",
        "--format=zip",
        "--output",
        str(archive_path),
        target_ref,
    ]
    prefix = context.project_prefix.as_posix()
    if prefix:
        archive_args.extend(["--", prefix])
    await _run_git_checked(context.git_root, archive_args, timeout=60)

    extract_dir = job_dir / "archive"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_dir)

    project_dir = extract_dir / Path(prefix) if prefix else extract_dir
    if not project_dir.exists():
        raise FileNotFoundError("Project directory not found in target revision")
    return project_dir


def _ignore_copy_names(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    blocked_names = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
    generated_suffixes = {
        ".aux",
        ".bbl",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".out",
        ".synctex.gz",
        ".toc",
    }
    for name in names:
        if name in blocked_names:
            ignored.add(name)
            continue
        if any(name.endswith(suffix) for suffix in generated_suffixes):
            ignored.add(name)
    return ignored


async def _run_latexdiff(
    old_path: Path,
    new_path: Path,
    markup_type: str = "UNDERLINE",
    math_markup: str = "fine",
    exclude_safecmd: str = "",
    exclude_textcmd: str = "",
) -> ProcessResult:
    """调用 latexdiff 生成语义级差异文档。"""
    cmd = [
        "latexdiff",
        f"--type={markup_type}",
        f"--math-markup={math_markup}",
        "--allow-spaces",
    ]
    if exclude_safecmd:
        cmd.append(f"--exclude-safecmd={exclude_safecmd}")
    if exclude_textcmd:
        cmd.append(f"--exclude-textcmd={exclude_textcmd}")
    cmd.extend([str(old_path), str(new_path)])
    return await _run_process(cmd, cwd=old_path.parent, timeout=120)


def _count_latexdiff_changes(diff_content: str) -> tuple[int, int]:
    """统计 latexdiff 输出中的新增和删除行数。

    latexdiff 用 ``\\DIFdel`` / ``\\DIFadd`` 等命令标记变更，
    这里按行统计含这些标记的行数。
    """
    added = 0
    deleted = 0
    for line in diff_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if r"\DIFdel" in stripped or r"\DIFDELTAMARK" in stripped:
            deleted += 1
        if r"\DIFadd" in stripped:
            added += 1
    return added, deleted


async def _compile_diff(project_dir: Path, diff_rel: str) -> TexCompileResult:
    cfg = get_config()
    timeout = cfg.features.compile.latexmk_timeout
    return await run_tex_compile(diff_rel, project_dir, timeout)


async def _run_git_checked(
    cwd: Path,
    args: list[str],
    timeout: int = 30,
) -> ProcessResult:
    result = await _run_git(cwd, args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "Git command failed"
        )
    return result


async def _run_git(cwd: Path, args: list[str], timeout: int = 30) -> ProcessResult:
    return await _run_process(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        timeout=timeout,
    )


async def _run_process(cmd: list[str], cwd: Path, timeout: int) -> ProcessResult:
    proc_kwargs: dict = {}
    if os.name == "nt":
        proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        proc_kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **proc_kwargs,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await terminate_process_tree(process)
        stdout, stderr = await process.communicate()
        return ProcessResult(
            returncode=-1,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=(
                stderr.decode("utf-8", errors="replace")
                + f"\nCommand timed out after {timeout} seconds."
            ),
        )

    return ProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


async def _get_optional_git_output(cwd: Path, args: list[str]) -> str:
    result = await _run_git(cwd, args, timeout=30)
    if result.returncode != 0:
        return ""
    return result.stdout


def _validate_project_path(root: Path, value: str, *, require_tex: bool = False) -> str:
    normalized = _normalize_include_path(value)
    if normalized is None:
        raise ValueError("Invalid project-relative path")
    kwargs: dict = {}
    if require_tex:
        kwargs["allowed_suffixes"] = (".tex",)
    resolve_project_path(root, normalized, **kwargs)
    return normalized


def _normalize_include_path(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    return normalized


def _repo_path(project: ProjectContext, project_rel: str) -> str:
    if project.project_prefix.as_posix():
        return (project.project_prefix / PurePosixPath(project_rel)).as_posix()
    return PurePosixPath(project_rel).as_posix()


def _diff_main_path(main_rel: str, job_id: str) -> str:
    main_path = PurePosixPath(main_rel)
    name = f"{main_path.stem}-diff-{job_id[:8]}{main_path.suffix}"
    return (main_path.parent / name).as_posix()

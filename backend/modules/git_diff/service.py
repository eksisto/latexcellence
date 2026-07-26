r"""
Git 差异高亮 PDF 生成服务。

该模块只读取当前打开项目和 Git 对象，将目标版本复制到临时目录，
再把主文件及其 \input / \include / \subfile 依赖展开后生成带底纹的
差异 LaTeX 文档，最后在临时目录中编译 PDF，不写回用户项目。
"""

import asyncio
import difflib
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


from pydantic import BaseModel, ConfigDict

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path

logger = get_logger(__name__)

WORKTREE_REF = "__WORKTREE__"
PDF_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "latexcellence_git_diff"

_INPUT_PATTERN = re.compile(
    r"\\(?P<cmd>input|include|subfile)\s*\{(?P<path>[^{}\n]+)\}"
)
_BEGIN_DOCUMENT = r"\begin{document}"
_END_DOCUMENT = r"\end{document}"

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
\newenvironment{{LXDiffAdded}}{{%
  \def\\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffAddBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{%
  \endMakeFramed%
}}
\newenvironment{{LXDiffDeleted}}{{%
  \def\\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffDelBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{%
  \endMakeFramed%
}}
"""

_DEFAULT_COLOR_ADD_HEX = "DFF6DD"
_DEFAULT_COLOR_DEL_HEX = "FFE3E3"


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
            label="当前工作区",
            kind="worktree",
            subject="包含尚未提交的修改和未跟踪文件",
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
    diff_engine: str = "difflib",
    markup_type: str = "UNDERLINE",
    math_markup: str = "fine",
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

    included_files = sorted(set(base_flattened.files) | set(target_flattened.files))

    if compile_result.returncode != 0 or not pdf_source.exists():
        logger.warning(
            f"Git diff PDF compilation failed for job={job_id}: {compile_result.returncode}"
        )
        explanation, fixes = await translate_compile_errors(
            compile_result.stdout,
            compile_result.stderr,
        )
        if not explanation:
            explanation = "差异 LaTeX 已生成，但 PDF 编译失败。请查看原始输出定位问题。"
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
            stdout=compile_result.stdout,
            stderr=compile_result.stderr,
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
        stdout=compile_result.stdout,
        stderr=compile_result.stderr,
    )


def get_pdf_path(job_id: str) -> Path:
    """返回已生成 PDF 的路径。"""

    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid job id")
    pdf_path = PDF_OUTPUT_ROOT / job_id / "diff-highlight.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError("Diff PDF not found")
    return pdf_path


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
    result = await _run_git_checked(
        git_root,
        ["log", "--pretty=format:%H%x00%h%x00%s", "-n", "50"],
    )
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
    if ref == WORKTREE_REF:
        return
    if not ref or ref.strip() != ref:
        raise ValueError("Git revision must not be empty or padded with spaces")
    result = await _run_git(
        git_root,
        ["rev-parse", "--verify", f"{ref}^{{commit}}"],
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
        path = project.root / Path(project_rel)
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

        result = await _run_latexdiff(
            old_file,
            new_file,
            markup_type=markup_type,
            math_markup=math_markup,
            exclude_safecmd=exclude_safecmd,
            exclude_textcmd=exclude_textcmd,
        )

        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"latexdiff failed: {result.stderr.strip() or 'empty output'}"
            )

        diff_content = result.stdout
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
    if not lines:
        return []
    content = lines if any(line.strip() for line in lines) else ["~"]
    return [f"\\begin{{{environment}}}", *content, f"\\end{{{environment}}}"]


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


async def _compile_diff(project_dir: Path, diff_rel: str) -> ProcessResult:
    cfg = get_config()
    timeout = cfg.features.compile.latexmk_timeout
    return await _run_process(
        [
            "latexmk",
            "-xelatex",
            "-interaction=nonstopmode",
            str(Path(diff_rel)),
        ],
        cwd=project_dir,
        timeout=timeout,
    )


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
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
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

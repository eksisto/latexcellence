"""Git 差异高亮 PDF 渲染编排。"""

import asyncio
import re
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import TexCompileResult, run_tex_compile

from . import constants
from . import diff as diff_mod
from . import flatten as flatten_mod
from . import process as process_mod
from . import revisions as revisions_mod
from .models import DiffDocument, DiffHighlightResult, FlattenedDocument, ProjectContext

logger = get_logger(__name__)


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

    context = await revisions_mod.get_project_context(workspace_id)
    main_rel = _validate_project_path(context.root, main_file, require_tex=True)

    await revisions_mod._validate_revision(context.git_root, base_ref)
    await revisions_mod._validate_revision(context.git_root, target_ref)

    base_flattened = await flatten_mod._flatten_main_document(
        base_ref, context, main_rel
    )
    target_flattened = await flatten_mod._flatten_main_document(
        target_ref, context, main_rel
    )

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
        diff_content, added_lines, deleted_lines = (
            await diff_mod._build_latexdiff_document(
                base_flattened.content,
                target_flattened.content,
                markup_type=markup_type,
                math_markup=math_markup,
                exclude_safecmd=exclude_safecmd,
                exclude_textcmd=exclude_textcmd,
            )
        )
    else:
        diff_doc = diff_mod._build_diff_document(
            base_flattened.content,
            target_flattened.content,
            color_add=color_add,
            color_del=color_del,
        )
        diff_content = diff_doc.content
        added_lines = diff_doc.added_lines
        deleted_lines = diff_doc.deleted_lines

    job_id = uuid.uuid4().hex
    job_dir = constants.PDF_OUTPUT_ROOT / job_id
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
    pdf_path = constants.PDF_OUTPUT_ROOT / job_id / "diff-highlight.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError("Diff PDF not found")
    return pdf_path


def _prune_old_job_dirs() -> None:
    """清理过期的差异 PDF job 目录，只保留最近生成的若干份。

    每次渲染都会把整个项目复制进系统临时目录，若不清理会持续占用磁盘。
    成功与失败的 job 目录都统一按生成时间保留最新的若干份。
    """
    if not constants.PDF_OUTPUT_ROOT.exists():
        return
    entries = [
        entry
        for entry in constants.PDF_OUTPUT_ROOT.iterdir()
        if entry.is_dir() and re.fullmatch(r"[a-f0-9]{32}", entry.name)
    ]
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    for stale in entries[constants._MAX_KEPT_JOB_DIRS :]:
        shutil.rmtree(stale, ignore_errors=True)
        logger.info(f"Pruned stale git diff job dir: {stale.name}")


async def _prepare_target_project(
    target_ref: str,
    context: ProjectContext,
    job_dir: Path,
) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    if target_ref == constants.WORKTREE_REF:
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
    await process_mod.run_git_checked(context.git_root, archive_args, timeout=60)

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


async def _compile_diff(project_dir: Path, diff_rel: str) -> TexCompileResult:
    cfg = get_config()
    timeout = cfg.features.compile.latexmk_timeout
    return await run_tex_compile(diff_rel, project_dir, timeout)


def _validate_project_path(root: Path, value: str, *, require_tex: bool = False) -> str:
    normalized = flatten_mod._normalize_include_path(value)
    if normalized is None:
        raise ValueError("Invalid project-relative path")
    kwargs: dict = {}
    if require_tex:
        kwargs["allowed_suffixes"] = (".tex",)
    resolve_project_path(root, normalized, **kwargs)
    return normalized


def _diff_main_path(main_rel: str, job_id: str) -> str:
    main_path = PurePosixPath(main_rel)
    name = f"{main_path.stem}-diff-{job_id[:8]}{main_path.suffix}"
    return (main_path.parent / name).as_posix()

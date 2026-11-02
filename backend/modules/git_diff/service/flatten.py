"""LaTeX 依赖递归展开（git 与 worktree 模式）。"""

import asyncio
from pathlib import PurePosixPath, PureWindowsPath

from backend.utils.path_guard import resolve_project_path

from . import process
from .constants import _INPUT_PATTERN, WORKTREE_REF
from .models import FlattenContext, FlattenedDocument, ProjectContext


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
        # worktree 分支逐文件同步读盘，入线程池避免阻塞事件循环
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    repo_rel = _repo_path(project, project_rel)
    result = await process.run_git(
        project.git_root,
        ["show", f"{ref}:{repo_rel}"],
        timeout=30,
    )
    if result.returncode != 0:
        if required:
            raise FileNotFoundError(f"File not found in revision {ref}: {project_rel}")
        return None
    return result.stdout


def _normalize_include_path(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    return normalized


def _repo_path(project: ProjectContext, project_rel: str) -> str:
    if project.project_prefix.as_posix():
        return (project.project_prefix / PurePosixPath(project_rel)).as_posix()
    return PurePosixPath(project_rel).as_posix()

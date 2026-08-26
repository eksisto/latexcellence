"""
项目文件浏览工具管理器。

职责：
    1. 定义 list_files / read_file_content / get_section_structure / search_across_files 的工具 schema
    2. 提供各工具的异步执行函数（executor），供 ToolRegistry 分发
    3. 管理全局单例，并在初始化时注册到 ToolRegistry

工作区上下文：
    所有 executor 通过 arguments 中的 _workspace_id 内部参数获取 ProjectManager，
    _workspace_id 由调用方（如 writing_chat/service/）在调用 execute() 前注入，
    不暴露给 LLM。
"""

import asyncio
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.utils.path_guard import resolve_project_path
from backend.utils.tool_result_protocol import format_result_status

# ── 章节命令优先级 ────────────────────────────────────────────────────────

_SECTION_LEVELS: dict[str, int] = {
    "part": 0,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
    "subparagraph": 6,
}

_SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
    r"(?:\[.*?\])?"
    r"\{(.*?)\}"
)

# 允许读取/搜索的项目文本文件后缀（防止 LLM 读取 config.json / .env 等敏感文件）
_READ_SUFFIXES = (".tex", ".bib", ".cls", ".sty", ".md", ".markdown", ".txt")
# search_across_files 结果条数上限
_MAX_SEARCH_RESULTS = 200
# search_across_files 统计匹配总数时的扫描上限（避免超大项目全量扫描）
_MAX_SEARCH_TOTAL_CAP = 1000
# list_files 单次返回的文件数上限
_MAX_LIST_RESULTS = 200
# read_file_content 未显式指定 end_line 时的单次行数预算
_READ_MAX_LINES = 200
# read_file_content 单行显示长度上限（超长行截断保护上下文）
_READ_MAX_LINE_CHARS = 500


def _normalize_glob_pattern(pattern: str, default: str) -> str:
    """校验并规范化 glob 模式，拒绝绝对路径与 '..' 越界。

    Path.rglob 支持 '..' 段并会匹配到根目录之外的文件，因此这里在
    LLM 传入的模式进入 rglob 之前做白名单校验（正斜杠归一化后检查）。
    """
    if not pattern or not pattern.strip():
        return default
    normalized = pattern.strip().replace("\\", "/")
    if "\x00" in normalized:
        raise ValueError("Invalid pattern: contains illegal characters")
    if normalized.startswith("/"):
        raise ValueError("Invalid pattern: absolute paths are not allowed")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Invalid pattern: absolute paths are not allowed")
    if Path(normalized).is_absolute() or PureWindowsPath(normalized).is_absolute():
        raise ValueError("Invalid pattern: absolute paths are not allowed")
    if ".." in normalized.split("/"):
        raise ValueError("Invalid pattern: '..' segments are not allowed")
    return normalized


def _clamp_search_results(value: Any, default: int = 50) -> int:
    """把 LLM 传入的结果数限制在 [1, _MAX_SEARCH_RESULTS] 内。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, _MAX_SEARCH_RESULTS))


def _is_inside_project(root: Path, candidate: Path) -> bool:
    """确认匹配文件真实位于项目根内且不是符号链接（防 rglob 越界兜底）。"""
    try:
        return not candidate.is_symlink() and candidate.resolve().is_relative_to(
            root.resolve()
        )
    except OSError:
        return False


# ── 管理器 ──────────────────────────────────────────────────────────────────


class ProjectToolsManager:
    """项目文件浏览工具管理器（单例）。"""

    def __init__(self) -> None:
        self._logger = get_logger("project_tools")

    # ── tool schema ────────────────────────────────────────────────────────

    @staticmethod
    def get_tools_schema() -> list[dict[str, Any]]:
        """
        返回可注入 LLM tools 参数的 Function Calling schema 列表。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": (
                        "列出项目中所有匹配 glob 模式的文件（默认 *.tex）。"
                        "返回相对项目根目录的路径列表。"
                        "当你需要了解项目包含哪些文件时使用。"
                        "文件过多时结果会被截断并在末尾标注 truncated 状态，"
                        "此时请用更精确的 pattern 缩小范围。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": (
                                    "Glob 过滤模式，如 '**/*.tex'（默认）、"
                                    "'**/chapter*.tex'、'**/*.bib'"
                                ),
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file_content",
                    "description": (
                        "读取项目内指定文本文件的内容（可指定行范围）。"
                        "行号从 1 开始。省略 end_line 时单次最多读取 200 行，"
                        "超出的部分会截断并在末尾标注 truncated 状态与下一段起始行，"
                        "按 next_step 提示以 start_line 继续读取。"
                        "当你需要查看某个文件的具体内容时使用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "相对于项目根目录的文件路径，"
                                    "如 'chapters/chapter3.tex'。仅支持 .tex/.bib/"
                                    ".cls/.sty/.md/.txt 文本文件"
                                ),
                            },
                            "start_line": {
                                "type": "integer",
                                "description": "起始行号（1-indexed，包含），默认 1",
                            },
                            "end_line": {
                                "type": "integer",
                                "description": (
                                    "结束行号（1-indexed，包含），省略则按 200 行预算读取"
                                ),
                            },
                        },
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_section_structure",
                    "description": (
                        "获取文档的 \\chapter / \\section / \\subsection 等章节树。"
                        "可指定文件（如 'chapters/chapter3.tex'），省略则扫描全部 .tex 文件。"
                        "返回每个章节的层级、标题、所在文件和行号。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "可选：限定扫描单个文件，"
                                    "如 'chapters/chapter3.tex'。省略则扫描全部 .tex 文件。"
                                ),
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_across_files",
                    "description": (
                        "跨项目文件搜索正则表达式模式。"
                        "用于查找 \\\\label{fig:...} 定义位置、\\\\cite 引用、"
                        "或任何 LaTeX 命令/文本模式的分布。"
                        "只在 .tex/.bib/.cls/.sty/.md/.txt 文本文件中搜索。"
                        "结果过多时会被截断并在末尾标注 truncated 状态与总匹配数，"
                        "此时请用更精确的 pattern 或 file_pattern 缩小范围，"
                        "或调高 max_results 后重试。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": (
                                    "正则表达式。搜索 LaTeX 命令时需转义反斜杠，"
                                    "如 '\\\\label\\{' 或 '\\\\cite\\{.*'"
                                ),
                            },
                            "file_pattern": {
                                "type": "string",
                                "description": (
                                    "可选：限定搜索范围的文件 glob，"
                                    "如 '**/*.tex'（默认）、'**/chaps/*.tex'"
                                ),
                            },
                            "max_results": {
                                "type": "integer",
                                "description": (
                                    "返回的最大匹配条数，默认 50，上限 200。"
                                    "结果截断时可用更精确的 pattern 缩小范围。"
                                ),
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
        ]


# ── executor 函数 ──────────────────────────────────────────────────────────


async def _list_files_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    # 目录遍历（rglob）为阻塞 I/O，入线程池调度（审查遗留项 B2）
    return await asyncio.to_thread(_list_files_sync, tool_name, arguments)


def _list_files_sync(tool_name: str, arguments: dict[str, Any]) -> str:
    """列出项目文件（默认 *.tex）。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    pattern = arguments.get("pattern", "**/*.tex")
    try:
        pattern = _normalize_glob_pattern(pattern, "**/*.tex")
    except ValueError as e:
        return f"无效的搜索模式：{e}"
    root = Path(pm.root_path)
    try:
        matched = [
            p
            for p in sorted(root.rglob(pattern))
            if p.is_file() and _is_inside_project(root, p)
        ]
    except Exception as e:
        return f"Error searching for '{pattern}': {e}"

    if not matched:
        return f"项目中没有匹配 '{pattern}' 的文件。"

    rel = [str(p.relative_to(root)) for p in matched]
    total = len(rel)
    truncated = total > _MAX_LIST_RESULTS
    shown = rel[:_MAX_LIST_RESULTS]
    body = f"文件列表（共 {total} 个文件）：\n" + "\n".join(f"- {f}" for f in shown)
    if truncated:
        body += f"\n… 另有 {total - _MAX_LIST_RESULTS} 个文件未显示。"
    footer = format_result_status(
        shown=len(shown),
        total=total,
        truncated=truncated,
        next_step=(
            "使用更精确的 pattern 缩小范围（如 'chapters/*.tex' 或 "
            "'**/chapter*.tex'），或调用 search_across_files 定位具体内容。"
        ),
    )
    return body + "\n" + footer


async def _read_file_content_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    # 文件读取为阻塞 I/O，入线程池调度（审查遗留项 B2）
    return await asyncio.to_thread(_read_file_content_sync, tool_name, arguments)


def _read_file_content_sync(tool_name: str, arguments: dict[str, Any]) -> str:
    """读取指定文件内容。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    if not file_path:
        return "错误：缺少 file_path 参数。"

    try:
        full_path = resolve_project_path(
            Path(pm.root_path),
            file_path,
            allowed_suffixes=_READ_SUFFIXES,
        )
    except Exception as e:
        return f"路径验证失败：{e}"

    if not full_path.exists():
        return f"文件不存在：{file_path}"
    if not full_path.is_file():
        return f"路径不是文件：{file_path}"

    try:
        lines = full_path.read_text(encoding="utf-8").split("\n")
    except Exception as e:
        return f"读取文件失败：{e}"

    total = len(lines)
    start = max(1, arguments.get("start_line", 1))
    end = arguments.get("end_line", total)
    if end is None:
        end = total
    end = min(end, total)

    if start > total:
        return f"起始行 {start} 超出文件总行数 {total}。"

    truncated = False
    if arguments.get("end_line") is None and (end - start + 1) > _READ_MAX_LINES:
        end = start + _READ_MAX_LINES - 1
        truncated = True

    selected = lines[start - 1 : end]
    line_width = len(str(end))
    line_truncated = False
    numbered_lines: list[str] = []
    display_lines: list[str] = []
    for i, line in enumerate(selected):
        if len(line) > _READ_MAX_LINE_CHARS:
            line = line[:_READ_MAX_LINE_CHARS] + "…"
            line_truncated = True
        display_lines.append(line)
        numbered_lines.append(f"{i + start:>{line_width}} | {line}")
    numbered = "\n".join(numbered_lines)

    body = (
        f"文件：{file_path}（共 {total} 行，显示 {start}–{end} 行，"
        f"约 {sum(len(line) for line in display_lines)} 字符）\n"
        f"{'─' * 60}\n{numbered}"
    )

    if truncated:
        footer = format_result_status(
            shown=len(selected),
            total=total,
            truncated=True,
            next_step=(
                f"文件较大，仅显示 {start}–{end} 行。继续读取后续内容："
                f"以 start_line={end + 1} 再次调用 read_file_content。"
            ),
            extra=["note: 长行已按 500 字符截断。"] if line_truncated else None,
        )
    else:
        footer = format_result_status(
            shown=len(selected),
            total=total,
            truncated=False,
            next_step="如需查看其他部分，用 start_line / end_line 指定行范围。",
            extra=["note: 长行已按 500 字符截断。"] if line_truncated else None,
        )
    return body + "\n" + footer


async def _get_section_structure_executor(
    tool_name: str, arguments: dict[str, Any]
) -> str:
    # 多文件 rglob + 逐文件全文扫描为阻塞 I/O，入线程池调度（B2）
    return await asyncio.to_thread(_get_section_structure_sync, tool_name, arguments)


def _get_section_structure_sync(tool_name: str, arguments: dict[str, Any]) -> str:
    """获取文档章节树。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    root = Path(pm.root_path)

    # 收集要扫描的 .tex 文件
    tex_files: list[Path] = []
    if file_path:
        try:
            fp = resolve_project_path(root, file_path, allowed_suffixes=(".tex",))
        except Exception as e:
            return f"路径验证失败：{e}"
        if not fp.exists():
            return f"文件不存在：{file_path}"
        tex_files = [fp]
    else:
        tex_files = sorted(root.rglob("*.tex"))

    entries: list[dict[str, Any]] = []
    for fp in tex_files:
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(fp.relative_to(root))
        for m in _SECTION_RE.finditer(content):
            cmd = m.group(1)
            title = m.group(2).strip()
            line_no = content[: m.start()].count("\n") + 1
            entries.append(
                {
                    "file": rel,
                    "line": line_no,
                    "level": _SECTION_LEVELS.get(cmd, 2),
                    "command": cmd,
                    "title": title,
                }
            )

    if not entries:
        return "未找到任何章节命令。"

    entries.sort(key=lambda e: (e["file"], e["line"]))
    lines_out: list[str] = []
    for e in entries:
        indent = "  " * (e["level"] - 1) if e["level"] > 1 else ""
        lines_out.append(
            f"{indent}{e['command']} {{{e['title']}}}  " f"— {e['file']}:{e['line']}"
        )
    return "章节结构：\n" + "\n".join(lines_out)


async def _search_across_files_executor(
    tool_name: str, arguments: dict[str, Any]
) -> str:
    # 多文件 rglob + 逐行正则扫描为阻塞 I/O，入线程池调度（B2）
    return await asyncio.to_thread(_search_across_files_sync, tool_name, arguments)


def _search_across_files_sync(tool_name: str, arguments: dict[str, Any]) -> str:
    """跨文件搜索。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    pattern_str = arguments.get("pattern", "")
    if not pattern_str:
        return "错误：缺少 pattern 参数。"
    file_pattern = arguments.get("file_pattern", "**/*.tex")
    try:
        file_pattern = _normalize_glob_pattern(file_pattern, "**/*.tex")
    except ValueError as e:
        return f"无效的文件搜索模式：{e}"
    max_results = _clamp_search_results(arguments.get("max_results", 50))

    try:
        pat = re.compile(pattern_str)
    except re.error as e:
        return f"正则表达式无效：{e}"

    root = Path(pm.root_path)
    try:
        matched_files = [
            p
            for p in sorted(root.rglob(file_pattern))
            if p.is_file()
            and p.suffix.lower() in _READ_SUFFIXES
            and _is_inside_project(root, p)
        ]
    except Exception as e:
        return f"搜索文件失败：{e}"

    if not matched_files:
        return f"没有匹配 '{file_pattern}' 的文件。"

    results: list[str] = []
    total_matches = 0
    total_capped = False
    for fp in matched_files:
        try:
            lines = fp.read_text(encoding="utf-8").split("\n")
        except Exception:
            continue
        rel = str(fp.relative_to(root))
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                total_matches += 1
                if len(results) < max_results:
                    stripped = line.strip()
                    if len(stripped) > 200:
                        stripped = stripped[:200] + "…"
                    results.append(f"{rel}:{i}  {stripped}")
                if total_matches >= _MAX_SEARCH_TOTAL_CAP:
                    total_capped = True
                    break
        if total_capped:
            break

    if not results:
        return f"未找到匹配 '{pattern_str}' 的内容。"

    truncated = total_capped or len(results) < total_matches
    total_text = None if total_capped else total_matches
    total_display = f"≥{_MAX_SEARCH_TOTAL_CAP}" if total_capped else str(total_matches)
    body = (
        f"搜索结果（显示 {len(results)} 条，共 {total_display} 条匹配，"
        f"{len(matched_files)} 个文件）：\n" + "\n".join(results)
    )
    if truncated:
        next_step = (
            "结果被截断：用更精确的 pattern 或 file_pattern 缩小范围，"
            "或调高 max_results 后重试。"
        )
    else:
        next_step = "如需更广的匹配，可调高 max_results 或换用其他 pattern。"
    footer = format_result_status(
        shown=len(results),
        total=total_text,
        truncated=truncated,
        next_step=next_step,
    )
    return body + "\n" + footer


# ── 辅助 ────────────────────────────────────────────────────────────────────


def _require_open_project(workspace_id: str) -> Any:
    """获取 ProjectManager 并检查项目是否打开。返回 pm 或错误字符串。"""
    try:
        pm = get_project_manager_for_workspace(workspace_id)
    except Exception as e:
        return f"获取工作区失败：{e}"
    if not pm.is_open:
        return "错误：当前未打开任何项目。请先打开一个 LaTeX 项目。"
    return pm


# ── 全局单例与注册 ──────────────────────────────────────────────────────────

_project_tools_manager: ProjectToolsManager | None = None


def get_project_tools_manager() -> ProjectToolsManager:
    """
    获取全局唯一的 ProjectToolsManager 实例。
    首次调用时自动将项目浏览工具注册到全局 ToolRegistry。
    """
    global _project_tools_manager
    if _project_tools_manager is None:
        _project_tools_manager = ProjectToolsManager()

        from backend.core.tool_registry import get_tool_registry

        registry = get_tool_registry()
        schemas = _project_tools_manager.get_tools_schema()
        executors: dict[str, Any] = {
            "list_files": _list_files_executor,
            "read_file_content": _read_file_content_executor,
            "get_section_structure": _get_section_structure_executor,
            "search_across_files": _search_across_files_executor,
        }
        for schema in schemas:
            name = schema["function"]["name"]
            executor = executors.get(name)
            if executor:
                registry.register(
                    name=name,
                    schema=schema,
                    executor=executor,
                    config_path="features.function_calling.project_tools.enabled",
                )
                logger = get_logger("project_tools")
                logger.info(f"Project tool registered: {name}")

    return _project_tools_manager


def reset_project_tools_manager() -> None:
    """重置全局单例（用于测试或配置热重载）。"""
    global _project_tools_manager
    _project_tools_manager = None

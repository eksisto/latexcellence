"""
项目文件浏览工具管理器。

职责：
    1. 定义 list_files / read_file_content / get_section_structure / search_across_files 的工具 schema
    2. 提供各工具的异步执行函数（executor），供 ToolRegistry 分发
    3. 管理全局单例，并在初始化时注册到 ToolRegistry

工作区上下文：
    所有 executor 通过 arguments 中的 _workspace_id 内部参数获取 ProjectManager，
    _workspace_id 由调用方（如 writing_chat/service.py）在调用 execute() 前注入，
    不暴露给 LLM。
"""

import re
from pathlib import Path
from typing import Any

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.utils.path_guard import resolve_project_path

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
                        "读取项目内指定文件的内容（可指定行范围）。"
                        "行号从 1 开始。省略 end_line 将读到文件末尾。"
                        "当你需要查看某个文件的具体内容时使用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "相对于项目根目录的文件路径，"
                                    "如 'chapters/chapter3.tex'"
                                ),
                            },
                            "start_line": {
                                "type": "integer",
                                "description": "起始行号（1-indexed，包含），默认 1",
                            },
                            "end_line": {
                                "type": "integer",
                                "description": "结束行号（1-indexed，包含），省略则读到末尾",
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
                                "description": "返回的最大匹配条数，默认 50",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
        ]


# ── executor 函数 ──────────────────────────────────────────────────────────


async def _list_files_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    """列出项目文件（默认 *.tex）。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    pattern = arguments.get("pattern", "**/*.tex")
    root = Path(pm.root_path)
    try:
        matched = [p for p in sorted(root.rglob(pattern)) if p.is_file()]
    except Exception as e:
        return f"Error searching for '{pattern}': {e}"

    if not matched:
        return f"项目中没有匹配 '{pattern}' 的文件。"

    rel = [str(p.relative_to(root)) for p in matched]
    return "文件列表：\n" + "\n".join(f"- {f}" for f in rel)


async def _read_file_content_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    """读取指定文件内容。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    if not file_path:
        return "错误：缺少 file_path 参数。"

    try:
        full_path = resolve_project_path(Path(pm.root_path), file_path)
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

    selected = lines[start - 1 : end]
    line_width = len(str(end))
    numbered = "\n".join(
        f"{i + start:>{line_width}} | {line}" for i, line in enumerate(selected)
    )
    return (
        f"文件：{file_path}（共 {total} 行，显示 {start}–{end} 行）\n"
        f"{'─' * 60}\n{numbered}"
    )


async def _get_section_structure_executor(
    tool_name: str, arguments: dict[str, Any]
) -> str:
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
    """跨文件搜索。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    pattern_str = arguments.get("pattern", "")
    if not pattern_str:
        return "错误：缺少 pattern 参数。"
    file_pattern = arguments.get("file_pattern", "**/*.tex")
    max_results = arguments.get("max_results", 50)

    try:
        pat = re.compile(pattern_str)
    except re.error as e:
        return f"正则表达式无效：{e}"

    root = Path(pm.root_path)
    try:
        matched_files = [p for p in sorted(root.rglob(file_pattern)) if p.is_file()]
    except Exception as e:
        return f"搜索文件失败：{e}"

    if not matched_files:
        return f"没有匹配 '{file_pattern}' 的文件。"

    results: list[str] = []
    for fp in matched_files:
        try:
            lines = fp.read_text(encoding="utf-8").split("\n")
        except Exception:
            continue
        rel = str(fp.relative_to(root))
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                stripped = line.strip()
                if len(stripped) > 200:
                    stripped = stripped[:200] + "…"
                results.append(f"{rel}:{i}  {stripped}")
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    if not results:
        return f"未找到匹配 '{pattern_str}' 的内容。"

    return (
        f"搜索结果（共 {len(results)} 条，显示前 {min(len(results), max_results)} 条）：\n"
        + "\n".join(results)
    )


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
                    config_path="features.project_tools.enabled",
                )
                logger = get_logger("project_tools")
                logger.info(f"Project tool registered: {name}")

    return _project_tools_manager


def reset_project_tools_manager() -> None:
    """重置全局单例（用于测试或配置热重载）。"""
    global _project_tools_manager
    _project_tools_manager = None

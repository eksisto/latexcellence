"""
段落编辑工具管理器。

Function Calling 工具，允许 LLM 直接修改 .tex 文件中的段落以及撤销修改。
从"建议者"升级为"执行者"。

所有 executor 通过 arguments 中的 _workspace_id 内部参数获取 ProjectManager，
_workspace_id 由调用方（如 writing_chat/service/）在调用 execute() 前注入，
不暴露给 LLM。
"""

from pathlib import Path
from typing import Any

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.utils.file_ops import compute_md5
from backend.utils.latex_json_guard import check_latex_text
from backend.utils.path_guard import resolve_project_path


class EditToolsManager:
    """段落编辑工具管理器（单例）。"""

    def __init__(self) -> None:
        self._logger = get_logger("edit_tools")

    @staticmethod
    def get_tools_schema() -> list[dict[str, Any]]:
        """
        返回可注入 LLM tools 参数的 Function Calling schema 列表。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "edit_paragraph",
                    "description": (
                        "Modify the content of a paragraph in the specified .tex file. "
                        "Calling this tool modifies the source file directly; the file is "
                        "automatically backed up before modification and the change can be undone. "
                        "Confirm the current file content with read_file_content before using this tool. "
                        "new_content must be the complete paragraph LaTeX source (not plain text); "
                        "join multiple logical lines with \\n. Keep the paragraph's semantic meaning intact."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "The .tex file path relative to the project root, "
                                    "e.g. 'chapters/chapter3.tex'"
                                ),
                            },
                            "paragraph_index": {
                                "type": "integer",
                                "description": (
                                    "Paragraph index (0-based). Paragraphs are separated by blank lines; "
                                    "each \\item inside an itemize/enumerate environment "
                                    "also counts as a paragraph."
                                ),
                            },
                            "new_content": {
                                "type": "string",
                                "description": (
                                    "The complete LaTeX content of the paragraph after modification. "
                                    "\\n represents a newline; use \\n to join multiple lines of the same paragraph. "
                                    "In a JSON string, every backslash in a LaTeX command must be written "
                                    "as two backslashes (\\\\), otherwise JSON escaping will silently corrupt "
                                    "it (e.g. \\rho becomes a carriage return)."
                                ),
                            },
                        },
                        "required": ["file_path", "paragraph_index", "new_content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "undo_last_edit",
                    "description": (
                        "Revert the most recent edit to the specified file. "
                        "Use this when a paragraph was modified incorrectly or you want to "
                        "restore the pre-edit state. It can be called multiple times to go "
                        "back further (supports multi-step undo)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "The .tex file path relative to the project root, "
                                    "e.g. 'chapters/chapter3.tex'"
                                ),
                            },
                        },
                        "required": ["file_path"],
                    },
                },
            },
        ]


# ── executor 函数 ──────────────────────────────────────────────────────────


async def _edit_paragraph_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    """修改指定段落的 LaTeX 内容。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    paragraph_index = arguments.get("paragraph_index")
    new_content = arguments.get("new_content", "")

    if not file_path:
        return "Error: missing file_path parameter."
    if paragraph_index is None:
        return "Error: missing paragraph_index parameter."
    if not new_content:
        return "Error: missing new_content parameter."

    # 写盘前 LaTeX 内容校验：拦截 JSON 反斜杠转义损坏（如 \\rho 被解析成
    # 回车 + ho），让 LLM 自纠而不是把损坏内容写进 .tex
    latex_error = check_latex_text(new_content)
    if latex_error:
        get_logger("edit_tools").warning(
            f"edit_paragraph rejected corrupt LaTeX content: {latex_error}"
        )
        return (
            f"Error: new_content failed LaTeX content validation: {latex_error}. "
            f"In a JSON string, every backslash must be written as two backslashes (\\\\), "
            f"otherwise JSON parses it as an escape character and corrupts the source. "
            f"Please fix it and retry."
        )

    # 验证段落索引有效性
    try:
        paragraphs = pm.get_paragraphs(file_path)
    except Exception as e:
        return f"Failed to read file paragraphs: {e}"

    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return (
            f"Paragraph index {paragraph_index} is invalid; the file has {len(paragraphs)} paragraphs "
            f"(valid range 0-{len(paragraphs) - 1})."
        )

    orig_text = paragraphs[paragraph_index].text

    # 获取当前文件 MD5 作为乐观锁
    try:
        full_path = resolve_project_path(
            Path(pm.root_path), file_path, allowed_suffixes=(".tex",)
        )
        md5 = compute_md5(full_path)
    except Exception as e:
        return f"Failed to compute file MD5: {e}"

    try:
        pm.apply_change(file_path, paragraph_index, new_content, expected_md5=md5)
    except Exception as e:
        return f"Edit failed: {e}"

    # 返回修改摘要供 LLM 引用
    orig_preview = orig_text[:80].replace("\n", "\\n")
    new_preview = new_content[:80].replace("\n", "\\n")
    return (
        f"✅ Successfully edited paragraph [{paragraph_index}] in {file_path}.\n"
        f"Original paragraph (first 80 chars): {orig_preview}{'…' if len(orig_text) > 80 else ''}\n"
        f"New paragraph (first 80 chars): {new_preview}{'…' if len(new_content) > 80 else ''}\n"
        f"Call undo_last_edit if you need to revert."
    )


async def _undo_last_edit_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    """撤销对指定文件的最近一次修改。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    if not file_path:
        return "Error: missing file_path parameter."

    try:
        ok = pm.undo(file_path)
    except Exception as e:
        return f"Undo failed: {e}"

    if not ok:
        return f"File {file_path} has no operations to undo."

    return (
        f"✅ Successfully reverted the edit to {file_path}. "
        f"You can call undo_last_edit again to revert to an even earlier version."
    )


# ── 辅助 ────────────────────────────────────────────────────────────────────


def _require_open_project(workspace_id: str) -> Any:
    """获取 ProjectManager 并检查项目是否打开。返回 pm 或错误字符串。"""
    try:
        pm = get_project_manager_for_workspace(workspace_id)
    except Exception as e:
        return f"Failed to get workspace: {e}"
    if not pm.is_open:
        return "Error: no project is currently open. Please open a LaTeX project first."
    return pm


# ── 全局单例与注册 ──────────────────────────────────────────────────────────

_edit_tools_manager: EditToolsManager | None = None


def get_edit_tools_manager() -> EditToolsManager:
    """
    获取全局唯一的 EditToolsManager 实例。
    首次调用时自动将编辑工具注册到全局 ToolRegistry。
    """
    global _edit_tools_manager
    if _edit_tools_manager is None:
        _edit_tools_manager = EditToolsManager()

        from backend.core.tool_registry import get_tool_registry

        registry = get_tool_registry()
        schemas = _edit_tools_manager.get_tools_schema()
        executors: dict[str, Any] = {
            "edit_paragraph": _edit_paragraph_executor,
            "undo_last_edit": _undo_last_edit_executor,
        }
        for schema in schemas:
            name = schema["function"]["name"]
            executor = executors.get(name)
            if executor:
                registry.register(
                    name=name,
                    schema=schema,
                    executor=executor,
                    config_path="features.function_calling.edit_tools.enabled",
                )
                logger = get_logger("edit_tools")
                logger.info(f"Edit tool registered: {name}")

    return _edit_tools_manager


def reset_edit_tools_manager() -> None:
    """重置全局单例（用于测试或配置热重载）。"""
    global _edit_tools_manager
    _edit_tools_manager = None

"""
段落编辑工具管理器。

Function Calling 工具，允许 LLM 直接修改 .tex 文件中的段落以及撤销修改。
从"建议者"升级为"执行者"。

所有 executor 通过 arguments 中的 _workspace_id 内部参数获取 ProjectManager，
_workspace_id 由调用方（如 writing_chat/service.py）在调用 execute() 前注入，
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
                        "修改指定 .tex 文件中某个段落的内容。"
                        "调用此工具将直接修改源文件，修改前会自动备份，支持撤销。"
                        "使用前请先通过 read_file_content 确认文件当前内容。"
                        "new_content 是完整的段落 LaTeX 源码（非纯文本），"
                        "多个逻辑行用 \\n 连接。注意保持段落语义完整。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "相对于项目根目录的 .tex 文件路径，"
                                    "如 'chapters/chapter3.tex'"
                                ),
                            },
                            "paragraph_index": {
                                "type": "integer",
                                "description": (
                                    "段落索引（从 0 开始）。段落按空行分割，"
                                    "每个 \\item（itemize/enumerate 环境）"
                                    "内的项目也算一个段落。"
                                ),
                            },
                            "new_content": {
                                "type": "string",
                                "description": (
                                    "修改后的段落完整 LaTeX 内容。"
                                    "\\n 表示换行，请用 \\n 连接同一段落的多行。"
                                    "LaTeX 命令中的每个反斜杠在 JSON 字符串里"
                                    "必须写成两个反斜杠（\\\\），否则会被 JSON "
                                    "转义静默损坏（如 \\rho 会变成回车）。"
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
                        "撤销对指定文件最近一次的修改。"
                        "当修改错了段落或想回退到修改前的状态时使用。"
                        "可以多次调用以退回更早的版本（支持多步撤销）。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": (
                                    "相对于项目根目录的 .tex 文件路径，"
                                    "如 'chapters/chapter3.tex'"
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
        return "错误：缺少 file_path 参数。"
    if paragraph_index is None:
        return "错误：缺少 paragraph_index 参数。"
    if not new_content:
        return "错误：缺少 new_content 参数。"

    # 写盘前 LaTeX 内容校验：拦截 JSON 反斜杠转义损坏（如 \\rho 被解析成
    # 回车 + ho），让 LLM 自纠而不是把损坏内容写进 .tex
    latex_error = check_latex_text(new_content)
    if latex_error:
        get_logger("edit_tools").warning(
            f"edit_paragraph rejected corrupt LaTeX content: {latex_error}"
        )
        return (
            f"错误：new_content 未通过 LaTeX 内容校验——{latex_error}。"
            f"JSON 字符串里的每个反斜杠必须写成两个反斜杠（\\\\），"
            f"否则会被 JSON 解析成转义符损坏源码。请修正后重试。"
        )

    # 验证段落索引有效性
    try:
        paragraphs = pm.get_paragraphs(file_path)
    except Exception as e:
        return f"读取文件段落失败：{e}"

    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return (
            f"段落索引 {paragraph_index} 无效，文件共有 {len(paragraphs)} 个段落 "
            f"（索引范围 0-{len(paragraphs) - 1}）。"
        )

    orig_text = paragraphs[paragraph_index].text

    # 获取当前文件 MD5 作为乐观锁
    try:
        full_path = resolve_project_path(
            Path(pm.root_path), file_path, allowed_suffixes=(".tex",)
        )
        md5 = compute_md5(full_path)
    except Exception as e:
        return f"计算文件 MD5 失败：{e}"

    try:
        pm.apply_change(file_path, paragraph_index, new_content, expected_md5=md5)
    except Exception as e:
        return f"修改失败：{e}"

    # 返回修改摘要供 LLM 引用
    orig_preview = orig_text[:80].replace("\n", "\\n")
    new_preview = new_content[:80].replace("\n", "\\n")
    return (
        f"✅ 成功修改 {file_path} 段落 [{paragraph_index}]。\n"
        f"原段落（前 80 字符）：{orig_preview}{'…' if len(orig_text) > 80 else ''}\n"
        f"新段落（前 80 字符）：{new_preview}{'…' if len(new_content) > 80 else ''}\n"
        f"如需撤销可调用 undo_last_edit。"
    )


async def _undo_last_edit_executor(tool_name: str, arguments: dict[str, Any]) -> str:
    """撤销对指定文件的最近一次修改。"""
    workspace_id = arguments.pop("_workspace_id", "")
    pm = _require_open_project(workspace_id)
    if isinstance(pm, str):
        return pm

    file_path = arguments.get("file_path", "")
    if not file_path:
        return "错误：缺少 file_path 参数。"

    try:
        ok = pm.undo(file_path)
    except Exception as e:
        return f"撤销失败：{e}"

    if not ok:
        return f"文件 {file_path} 没有可撤销的操作。"

    return (
        f"✅ 成功撤销对 {file_path} 的修改。可以继续调用 undo_last_edit 退回更早版本。"
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

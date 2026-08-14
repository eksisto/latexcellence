"""Agent 私有文件工具共享模块。

公共 Function Calling 契约与通用执行逻辑集中在此；功能模块只需提供各自的
路径/内容校验与存储后端。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

MAX_READ_CHARS = 8000


@dataclass(slots=True)
class AgentFileToolFailure:
    """文件工具返回的后端校验/存储失败结果。"""

    text: str
    summary: str | None = None
    event: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentFileWrite:
    """一次成功的逻辑工作区写入。"""

    path: str
    size: int
    text: str | None = None
    summary: str | None = None
    event: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentFileRead:
    """从当前逻辑工作区读取到的文本。"""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class AgentFileToolNames:
    """单个 Agent 使用的公共工具名称。"""

    write: str = "write_file"
    read: str = "read_file"
    list: str = "list_files"
    values: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "values",
            frozenset({self.write, self.read, self.list}),
        )


@dataclass(frozen=True, slots=True)
class AgentFileToolBehavior:
    """共享执行逻辑中保留的模块级行为差异。"""

    list_header: str = "Files in workspace:"
    empty_list_text: str = "(workspace is empty)"
    empty_list_summary: str = "Empty"
    include_files_in_list_event: bool = False
    include_write_size_in_event: bool = True
    preserve_read_line_endings: bool = True


def build_agent_file_tools_schema(
    *,
    names: AgentFileToolNames = AgentFileToolNames(),
    write_description: str | None = None,
    read_description: str | None = None,
    list_description: str | None = None,
    list_properties: dict[str, Any] | None = None,
    write_path_description: str | None = None,
    write_content_description: str | None = None,
    line_range_description: str | None = None,
    supports_line_range: bool = False,
) -> list[dict[str, Any]]:
    """返回一份全新的规范 Agent 文件工具 schema。"""
    read_properties: dict[str, Any] = {"file_path": {"type": "string"}}
    if supports_line_range:
        read_properties["line_range"] = {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 2,
            "maxItems": 2,
        }
        if line_range_description is not None:
            read_properties["line_range"]["description"] = line_range_description

    write_path: dict[str, Any] = {"type": "string"}
    if write_path_description is not None:
        write_path["description"] = write_path_description
    write_content: dict[str, Any] = {"type": "string"}
    if write_content_description is not None:
        write_content["description"] = write_content_description
    return [
        {
            "type": "function",
            "function": {
                "name": names.write,
                "description": write_description
                or (
                    "Create or overwrite a text file in the current Agent "
                    "workspace. Supply the complete final content; parent "
                    "directories are created when the workspace allows it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": write_path,
                        "content": write_content,
                    },
                    "required": ["file_path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": names.read,
                "description": read_description
                or "Read a UTF-8 text file from the current Agent workspace."
                + (
                    " Use line_range to read an inclusive 1-based line range."
                    if supports_line_range
                    else ""
                ),
                "parameters": {
                    "type": "object",
                    "properties": read_properties,
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": names.list,
                "description": list_description
                or (
                    "List files visible in the current Agent workspace with "
                    "their relative paths and byte sizes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": list_properties or {},
                },
            },
        },
    ]


def normalize_line_range(
    value: Any,
    *,
    coerce: bool = False,
    allow_out_of_bounds: bool = False,
) -> tuple[int, int] | None:
    """校验规范的 ``[start, end]`` 行号区间参数。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("line_range must be a two-item array of integers")
    start_value, end_value = value
    if coerce:
        try:
            start, end = int(start_value), int(end_value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "line_range must be a two-item array of integers"
            ) from error
    else:
        if type(start_value) is not int or type(end_value) is not int:
            raise ValueError("line_range must be a two-item array of integers")
        start, end = start_value, end_value
    if not allow_out_of_bounds and (start < 1 or end < start):
        raise ValueError("read_file line_range must satisfy 1 <= start <= end")
    return start, end


def select_line_range(
    content: str,
    line_range: tuple[int, int] | None,
    *,
    preserve_line_endings: bool = True,
) -> str:
    """按闭区间选取内容，可配置是否保留行尾符。"""
    if preserve_line_endings:
        if line_range is None:
            return content
        start, end = line_range
        return "".join(content.splitlines(keepends=True)[start - 1 : end])

    lines = content.splitlines()
    if line_range is not None:
        start, end = line_range
        lines = lines[start - 1 : end]
    return "\n".join(lines)


class AgentFileToolExecutor(ABC):
    """自主 Agent 文件工具的规范执行层。"""

    _file_tool_names = AgentFileToolNames()
    _file_tool_behavior = AgentFileToolBehavior()
    _supports_line_range = False

    async def execute_file_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if tool_name == self._file_tool_names.write:
            return await self._write_file(args)
        if tool_name == self._file_tool_names.read:
            return await asyncio.to_thread(self._read_file, args)
        if tool_name == self._file_tool_names.list:
            return await asyncio.to_thread(self._list_files, args)
        raise ValueError(f"Unknown file tool: {tool_name}")

    async def _write_file(
        self,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        if not isinstance(file_path, str):
            return self._failure_event(
                self._file_tool_names.write,
                AgentFileToolFailure("file_path must be a string"),
            )
        if not isinstance(content, str):
            return self._failure_event(
                self._file_tool_names.write,
                AgentFileToolFailure("content must be a string"),
            )

        result = await asyncio.to_thread(
            self._write_agent_file,
            file_path,
            content,
        )
        if isinstance(result, AgentFileToolFailure):
            return self._failure_event(self._file_tool_names.write, result)

        summary = result.summary or f"Wrote {result.path} ({result.size} bytes)"
        text = result.text or summary
        event: dict[str, Any] = {
            "tool": self._file_tool_names.write,
            "ok": True,
            "summary": summary,
            "file_path": result.path,
        }
        if self._file_tool_behavior.include_write_size_in_event:
            event["size"] = result.size
        event.update(result.event)
        return text, event

    def _read_file(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        if not isinstance(file_path, str):
            return self._failure_event(
                self._file_tool_names.read,
                AgentFileToolFailure("file_path must be a string"),
            )

        result = self._read_agent_file(file_path)
        if isinstance(result, AgentFileToolFailure):
            return self._failure_event(self._file_tool_names.read, result)

        line_range: tuple[int, int] | None = None
        if self._supports_line_range:
            try:
                line_range = self._prepare_agent_line_range(
                    result.content,
                    args.get("line_range"),
                )
            except ValueError as error:
                return self._failure_event(
                    self._file_tool_names.read,
                    AgentFileToolFailure(str(error)),
                )

        selected = select_line_range(
            result.content,
            line_range,
            preserve_line_endings=(self._file_tool_behavior.preserve_read_line_endings),
        )
        max_chars = self._max_read_chars()
        truncated = len(selected) > max_chars
        selected_text = (
            selected if not truncated else selected[:max_chars] + "\n...[truncated]"
        )
        text, summary, extra_event = self._format_agent_file_read(
            result,
            selected_text,
            line_range,
        )
        event: dict[str, Any] = {
            "tool": self._file_tool_names.read,
            "ok": True,
            "summary": summary,
        }
        if line_range is not None:
            event["line_range"] = list(line_range)
        event.update(extra_event)
        return text, event

    def _max_read_chars(self) -> int:
        return MAX_READ_CHARS

    def _prepare_agent_line_range(
        self,
        content: str,
        value: Any,
    ) -> tuple[int, int] | None:
        return normalize_line_range(value)

    def _format_agent_file_read(
        self,
        result: AgentFileRead,
        selected_text: str,
        line_range: tuple[int, int] | None,
    ) -> tuple[str, str, dict[str, Any]]:
        return selected_text, result.path, {}

    def _list_files(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        result = self._list_agent_files(args)
        if isinstance(result, AgentFileToolFailure):
            return self._failure_event(self._file_tool_names.list, result)

        files = sorted(result, key=lambda item: str(item["path"]))
        if not files:
            text = self._file_tool_behavior.empty_list_text
            summary = self._file_tool_behavior.empty_list_summary
        else:
            text = (
                self._file_tool_behavior.list_header
                + "\n"
                + "\n".join(
                    f"- {item['path']} ({item['size']} bytes)" for item in files
                )
            )
            summary = f"{len(files)} files"
        event: dict[str, Any] = {
            "tool": self._file_tool_names.list,
            "ok": True,
            "summary": summary,
        }
        if self._file_tool_behavior.include_files_in_list_event:
            event["files"] = files
        return text, event

    @staticmethod
    def _failure_event(
        tool_name: str,
        failure: AgentFileToolFailure,
    ) -> tuple[str, dict[str, Any]]:
        event: dict[str, Any] = {
            "tool": tool_name,
            "ok": False,
            "summary": failure.summary or failure.text,
        }
        event.update(failure.event)
        return failure.text, event

    @abstractmethod
    def _write_agent_file(
        self,
        file_path: str,
        content: str,
    ) -> AgentFileWrite | AgentFileToolFailure:
        """校验并持久化一个逻辑工作区文件。"""

    @abstractmethod
    def _read_agent_file(
        self,
        file_path: str,
    ) -> AgentFileRead | AgentFileToolFailure:
        """校验并读取一个完整的逻辑工作区文件。"""

    @abstractmethod
    def _list_agent_files(
        self,
        args: dict[str, Any],
    ) -> list[dict[str, Any]] | AgentFileToolFailure:
        """返回逻辑工作区中可见的文件列表。"""

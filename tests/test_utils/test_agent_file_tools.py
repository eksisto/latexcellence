"""自主 Agent 共享文件工具契约测试。"""

from typing import Any

from backend.core.config import get_config
from backend.modules.plot_gen.agent import PlotGenAgent
from backend.modules.plot_gen.executor import PlotExecutor
from backend.modules.style_modify.agent import (
    StyleModifyAgent,
    _ToolExecutor as StyleModifyToolExecutor,
)
from backend.modules.template_gen.agent import (
    TemplateAgentService,
    _ToolExecutor as TemplateToolExecutor,
)
from backend.utils.agent_file_tools import (
    AgentFileToolExecutor,
    AgentFileToolNames,
)


def _tool(tools: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(tool for tool in tools if tool["function"]["name"] == name)


def test_agents_share_file_tool_execution_layer() -> None:
    expected = {
        TemplateToolExecutor: (
            True,
            {"write_file", "read_file", "list_files"},
        ),
        PlotExecutor: (False, {"write_file", "read_file", "list_files"}),
        StyleModifyToolExecutor: (
            True,
            {"stage_replace", "read_project_file", "list_project_files"},
        ),
    }
    for executor, (supports_line_range, tool_names) in expected.items():
        assert issubclass(executor, AgentFileToolExecutor)
        assert executor._write_file is AgentFileToolExecutor._write_file
        assert executor._read_file is AgentFileToolExecutor._read_file
        assert executor._list_files is AgentFileToolExecutor._list_files
        assert executor._supports_line_range is supports_line_range
        assert executor._file_tool_names.values == tool_names


def test_read_file_schema_uses_canonical_line_range() -> None:
    template_tools = TemplateAgentService()._build_tools_schema(get_config())
    style_tools = StyleModifyAgent._build_tools_schema()
    plot_tools = PlotGenAgent._build_tools_schema()

    template_properties = _tool(template_tools, "read_file")["function"]["parameters"][
        "properties"
    ]
    style_properties = _tool(style_tools, "read_project_file")["function"][
        "parameters"
    ]["properties"]
    plot_properties = _tool(plot_tools, "read_file")["function"]["parameters"][
        "properties"
    ]
    style_write = _tool(style_tools, "stage_replace")["function"]
    style_list = _tool(style_tools, "list_project_files")["function"]

    for properties in (template_properties, style_properties):
        assert "start_line" not in properties
        assert "end_line" not in properties

    common_range_schema = {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 2,
        "maxItems": 2,
    }
    assert style_properties["line_range"] == common_range_schema
    assert template_properties["line_range"] == {
        **common_range_schema,
        "description": "Optional inclusive 1-based [start, end] line range.",
    }

    assert plot_properties == {"file_path": {"type": "string"}}
    assert style_write["parameters"]["required"] == ["file_path", "content"]
    assert set(style_write["parameters"]["properties"]) == {"file_path", "content"}
    assert set(style_list["parameters"]["properties"]) == {"max_results"}


def test_agent_file_tool_names_values_is_cached() -> None:
    names = AgentFileToolNames()

    assert names.values is names.values
    assert names.values == {"write_file", "read_file", "list_files"}

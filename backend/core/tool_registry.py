"""
Function Calling 工具注册表。

职责：
    1. 管理所有可用工具的 schema 定义和执行函数
    2. 根据 tool_call.name 分发到对应的执行函数
    3. 按 config 配置启用/禁用工具
    4. 对外暴露 get_enabled_tools()，返回可注入 LLM 的工具列表

设计原则：
    - 工具作为全局能力，不绑定特定业务模块
    - 新增工具只需 register()，无需修改已有代码
    - 执行函数签名统一为 async (tool_name, arguments) -> Any
    - 全局单例模式
"""

from typing import Any, Callable, Awaitable

from backend.core.config import get_config
from backend.core.logger import get_logger

# 工具执行函数类型：接收工具名和参数，返回结果
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


class ToolEntry:
    """单个工具的注册条目。"""

    __slots__ = ("name", "schema", "executor", "enabled", "config_path")

    def __init__(
        self,
        name: str,
        schema: dict[str, Any],
        executor: ToolExecutor,
        enabled: bool = True,
        config_path: str | None = None,
    ) -> None:
        self.name = name
        self.schema = schema
        self.executor = executor
        self.enabled = enabled
        self.config_path = config_path


class ToolRegistry:
    """
    Function Calling 工具注册表（单例）。

    使用方式：
        registry = get_tool_registry()
        registry.register("web_search", schema, search_executor)
        tools = registry.get_enabled_tools()  # 注入 LLM
        result = await registry.execute("web_search", {"query": "..."})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._logger = get_logger("tool_registry")

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        executor: ToolExecutor,
        enabled: bool = True,
        config_path: str | None = None,
    ) -> None:
        """注册一个工具。若同名工具已存在则覆盖。"""
        self._tools[name] = ToolEntry(
            name=name,
            schema=schema,
            executor=executor,
            enabled=enabled,
            config_path=config_path,
        )
        self._logger.info(f"Tool registered: {name} (enabled={enabled})")

    def get_enabled_tools(self) -> list[dict[str, Any]]:
        """
        返回当前启用的所有工具 schema。

        用于注入 LLM 的 tools 参数。
        """
        cfg = get_config()
        result: list[dict[str, Any]] = []
        for name, entry in self._tools.items():
            # 检查全局配置中对应功能是否启用
            if not self._is_feature_enabled(name, cfg, self._tools):
                continue
            if entry.enabled:
                result.append(entry.schema)
        return result

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        根据工具名执行对应函数。

        异常：
            ValueError: 工具不存在或未启用
        """
        entry = self._tools.get(tool_name)
        if entry is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        if not entry.enabled:
            raise ValueError(f"Tool is disabled: {tool_name}")

        self._logger.info(f"Executing tool: {tool_name} | args={arguments}")
        result = await entry.executor(tool_name, arguments)
        self._logger.info(f"Tool execution completed: {tool_name}")
        return result

    def set_enabled(self, name: str, enabled: bool) -> None:
        """运行时启用/禁用某工具。"""
        if name in self._tools:
            self._tools[name].enabled = enabled
            self._logger.info(f"Tool {name} {'enabled' if enabled else 'disabled'}")

    @staticmethod
    def _is_feature_enabled(
        tool_name: str, cfg: Any, tools: dict[str, ToolEntry]
    ) -> bool:
        """检查工具对应的全局功能开关。"""
        entry = tools.get(tool_name)
        if entry is None or entry.config_path is None:
            return True
        value = cfg
        for part in entry.config_path.split("."):
            value = getattr(value, part)
        return bool(value)


# ── 全局单例 ────────────────────────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取全局唯一的 ToolRegistry 实例。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry

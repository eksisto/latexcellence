"""搜索管理器 — 统一管理引擎注册、优先级、降级与健康检查。"""

import time
from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.time_utils import get_current_time_context
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.engines.ddg import DDGChallengeError, DuckDuckGoHtmlEngine
from backend.utils.web_search.engines.searxng import SearXNGEngine
from backend.utils.web_search.models import SearchResponse


class SearchManager:
    """
    统一搜索接口。

    职责：
        - 管理多个搜索引擎的注册和优先级
        - 执行搜索时按优先级尝试，失败自动降级
        - 提供 Function Calling 工具 schema
    """

    def __init__(self) -> None:
        self._engines: list[BaseSearchEngine] = []
        self._primary: BaseSearchEngine | None = None
        self._logger = get_logger("web_search")

    def register_engine(
        self, engine: BaseSearchEngine, primary: bool = False
    ) -> None:
        """注册搜索引擎。primary=True 时设为主引擎。"""
        self._engines.append(engine)
        if primary:
            self._primary = engine
        self._logger.info(
            f"搜索引擎已注册：{engine.name} (primary={primary})"
        )

    def get_engine(self, name: str) -> BaseSearchEngine | None:
        """按名称获取已注册的引擎。"""
        for e in self._engines:
            if e.name == name:
                return e
        return None

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        engine_name: str | None = None,
    ) -> SearchResponse:
        """
        执行搜索。

        - 指定 engine_name 时使用指定引擎
        - 否则使用主引擎，失败时自动降级到其他可用引擎
        """
        cfg = get_config()
        if max_results is None:
            max_results = cfg.features.web_search.max_results

        # 指定引擎
        if engine_name:
            engine = self.get_engine(engine_name)
            if engine is None:
                return SearchResponse(
                    query=query,
                    engine="",
                )
            return await self._search_with_engine(engine, query, max_results)

        # 使用主引擎 + 降级
        if self._primary:
            try:
                return await self._search_with_engine(
                    self._primary, query, max_results
                )
            except DDGChallengeError:
                raise  # challenge 不降级，直接向上传播
            except Exception as e:
                self._logger.warning(
                    f"主引擎 {self._primary.name} 搜索失败：{e}，尝试降级"
                )

        # 降级到其他引擎
        for engine in self._engines:
            if engine is self._primary:
                continue
            try:
                return await self._search_with_engine(
                    engine, query, max_results
                )
            except Exception as e:
                self._logger.warning(f"引擎 {engine.name} 搜索失败：{e}")

        # 所有引擎均失败
        self._logger.error("所有搜索引擎均不可用")
        return SearchResponse(query=query, engine="none")

    async def _search_with_engine(
        self, engine: BaseSearchEngine, query: str, max_results: int
    ) -> SearchResponse:
        """使用指定引擎执行搜索，记录耗时。"""
        start = time.monotonic()
        result = await engine.search(query, max_results)
        elapsed_ms = (time.monotonic() - start) * 1000
        result.search_time_ms = elapsed_ms
        result.engine = engine.name
        self._logger.info(
            f"搜索完成 | engine={engine.name} | query={query[:50]} | "
            f"results={result.total_results} | time={elapsed_ms:.0f}ms"
        )
        return result

    def get_tools_schema(self) -> list[dict[str, Any]]:
        """
        返回可注入 LLM tools 参数的 Function Calling schema。

        供 ToolRegistry 或直接在 LLM 调用中使用。
        动态注入当前日期时间，帮助 LLM 理解相对时间概念。
        """
        t = get_current_time_context()

        description = (
            f"Search the internet for the latest information. Current date: {t['date']} ({t['weekday']}), "
            f"time: {t['time']}. Use this tool when the user asks for real-time information, "
            f"the latest data, or facts you are unsure of."
        )

        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query terms; keep them concise and precise.",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results to return; default is 5.",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]


# ── 全局单例 ────────────────────────────────────────────────────────────────

_search_manager: SearchManager | None = None


async def _web_search_executor(tool_name: str, arguments: dict[str, Any]) -> SearchResponse:
    """web_search 工具执行函数，供 ToolRegistry 调用。"""
    manager = get_search_manager()
    cfg = get_config()
    result = await manager.search(
        query=arguments["query"],
        max_results=arguments.get(
            "max_results", cfg.features.web_search.max_results),
    )
    return result


def reset_search_manager() -> None:
    """重置全局 SearchManager 单例，使配置变更（如切换引擎）立即生效。"""
    global _search_manager
    _search_manager = None


def get_search_manager() -> SearchManager:
    """获取全局唯一的 SearchManager 实例。"""
    global _search_manager
    if _search_manager is None:
        _search_manager = SearchManager()
        cfg = get_config()
        engines_cfg = cfg.features.web_search.engines

        # 根据每个引擎的 enabled 标志决定是否注册
        engine_registry: dict[str, BaseSearchEngine] = {}
        if engines_cfg.duckduckgo.enabled:
            engine_registry["duckduckgo"] = DuckDuckGoHtmlEngine()
        if engines_cfg.searxng.enabled:
            engine_registry["searxng"] = SearXNGEngine()

        primary = cfg.features.web_search.primary_engine
        for name, engine in engine_registry.items():
            _search_manager.register_engine(engine, primary=(name == primary))

        # 将搜索工具注册到 ToolRegistry，使 LLM tool_call 能正确分发
        from backend.core.tool_registry import get_tool_registry
        registry = get_tool_registry()
        schema = _search_manager.get_tools_schema()
        if schema:
            registry.register(
                name="web_search",
                schema=schema[0],
                executor=_web_search_executor,
            )

    return _search_manager

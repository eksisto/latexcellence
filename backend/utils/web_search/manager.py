"""搜索管理器 — 统一管理引擎注册、优先级、降级与健康检查。"""

import time
from typing import Any

import asyncio

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.time_utils import get_current_time_context
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.deep_read import DeepReader
from backend.utils.web_search.engines.ddg import DDGChallengeError, DuckDuckGoHtmlEngine
from backend.utils.web_search.engines.searxng import SearXNGEngine
from backend.utils.web_search.engines.tavily import TavilyEngine
from backend.utils.web_search.models import SearchResponse, SearchResult
from backend.utils.web_search.query_rewriter import QueryRewriter


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

    def register_engine(self, engine: BaseSearchEngine, primary: bool = False) -> None:
        """注册搜索引擎。primary=True 时设为主引擎。"""
        self._engines.append(engine)
        if primary:
            self._primary = engine
        self._logger.info(
            f"Search engine registered: {engine.name} (primary={primary})"
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
            max_results = cfg.features.function_calling.web_search.max_results

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
                return await self._search_with_engine(self._primary, query, max_results)
            except DDGChallengeError:
                raise  # challenge 不降级，直接向上传播
            except Exception as e:
                self._logger.warning(
                    f"Primary engine {self._primary.name} search failed: {e}, attempting fallback"
                )

        # 降级到其他引擎
        for engine in self._engines:
            if engine is self._primary:
                continue
            try:
                return await self._search_with_engine(engine, query, max_results)
            except Exception as e:
                self._logger.warning(f"Engine {engine.name} search failed: {e}")

        # 所有引擎均失败
        self._logger.error("All search engines are unavailable")
        return SearchResponse(query=query, engine="none")

    async def search_multi(
        self,
        queries: list[str],
        max_results: int | None = None,
    ) -> SearchResponse:
        """并行执行多条查询，合并结果去重后返回。"""
        cfg = get_config()
        if max_results is None:
            max_results = cfg.features.function_calling.web_search.max_results

        tasks = [self.search(q, max_results) for q in queries]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        seen_urls: set[str] = set()
        merged: list[SearchResponse] = []
        for resp in responses:
            if isinstance(resp, Exception):
                self._logger.warning(f"Multi-query sub-search failed: {resp}")
                continue
            merged.append(resp)

        all_results: list[SearchResult] = []
        for resp in merged:
            for r in resp.results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)

        engine_names = {r.engine for r in merged if r.engine}
        return SearchResponse(
            query=" | ".join(queries),
            results=all_results[:max_results],
            engine=",".join(sorted(engine_names)),
            total_results=len(all_results),
        )

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
            f"Search completed | engine={engine.name} | query={query[:50]} | "
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


async def _web_search_executor(
    tool_name: str, arguments: dict[str, Any]
) -> SearchResponse:
    """web_search 工具执行函数，供 ToolRegistry 调用。"""
    manager = get_search_manager()
    cfg = get_config()
    original_query = arguments["query"]

    # ── 查询重写 ──────────────────────────────────────────────────────────
    variants: list[str] = []
    if cfg.features.function_calling.web_search.query_rewriting.enabled:
        try:
            rewriter = QueryRewriter()
            variants = await rewriter.rewrite(original_query)
        except Exception as e:
            logger = get_logger("web_search")
            logger.warning(f"Query rewriting failed: {e}")

    # ── 执行搜索 ──────────────────────────────────────────────────────────
    cfg_max = cfg.features.function_calling.web_search.max_results
    user_max = arguments.get("max_results", cfg_max)

    if len(variants) > 1:
        result = await manager.search_multi(
            queries=variants,
            max_results=user_max,
        )
        result.original_query = original_query
        result.rewritten_queries = variants
    else:
        result = await manager.search(
            query=original_query,
            max_results=user_max,
        )

    # ── Deep Read ─────────────────────────────────────────────────────────
    if cfg.features.function_calling.web_search.deep_read_pages > 0 and result.results:
        reader = DeepReader()
        await reader.fetch_top_results(
            result.results,
            max_pages=cfg.features.function_calling.web_search.deep_read_pages,
            max_chars_per_page=cfg.features.function_calling.web_search.deep_read_max_chars,
            timeout=cfg.features.function_calling.web_search.timeout_seconds,
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
        engines_cfg = cfg.features.function_calling.web_search.engines

        # 根据每个引擎的 enabled 标志决定是否注册
        engine_registry: dict[str, BaseSearchEngine] = {}
        if engines_cfg.duckduckgo.enabled:
            engine_registry["duckduckgo"] = DuckDuckGoHtmlEngine()
        if engines_cfg.searxng.enabled:
            engine_registry["searxng"] = SearXNGEngine()
        if engines_cfg.tavily.enabled and engines_cfg.tavily.api_key:
            engine_registry["tavily"] = TavilyEngine()

        primary = cfg.features.function_calling.web_search.primary_engine
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
                config_path="features.function_calling.web_search.enabled",
            )

    return _search_manager

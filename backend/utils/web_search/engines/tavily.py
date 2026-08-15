"""Tavily 搜索引擎实现。"""

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.models import SearchResponse, SearchResult

_TAVILY_API_URL = "https://api.tavily.com/search"


def _get_user_agent() -> str:
    """从配置获取 User-Agent。"""
    return get_config().http.user_agent


class TavilyEngine(BaseSearchEngine):
    """
    Tavily 搜索引擎。

    通过 Tavily Search API（POST https://api.tavily.com/search）执行搜索，
    需要在 config.json 中配置 engines.tavily.api_key。
    """

    name = "tavily"

    def __init__(self, api_key: str = "", api_url: str = _TAVILY_API_URL) -> None:
        self._logger = get_logger("tavily")
        self._api_key = api_key.strip()
        self._api_url = api_url.rstrip("/")
        # 进程级共享客户端：连接复用，避免每次请求新建
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取共享 httpx 异步客户端（懒创建，超时按请求传入）。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(follow_redirects=True)
        return self._client

    async def close(self) -> None:
        """释放共享客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_api_key(self) -> str:
        """获取 Tavily API Key，优先使用运行时配置。"""
        cfg = get_config()
        return (
            cfg.features.function_calling.web_search.engines.tavily.api_key.strip()
            or self._api_key
        )

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        api_key = self._get_api_key()
        if not api_key:
            self._logger.warning("Tavily API key not configured")
            return SearchResponse(query=query, engine=self.name, results=[])

        cfg = get_config()
        timeout = cfg.features.function_calling.web_search.timeout_seconds
        return await self._do_search(
            self._get_client(), query, max_results, api_key, timeout
        )

    async def _do_search(
        self,
        client: httpx.AsyncClient,
        query: str,
        max_results: int,
        api_key: str,
        timeout: float | None = None,
    ) -> SearchResponse:
        """实际搜索逻辑。"""
        if timeout is None:
            timeout = get_config().features.function_calling.web_search.timeout_seconds
        payload = {
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            # 合成答案 + 原始正文：答案直接给 LLM 快速结论，
            # raw_content 填充 full_content，避免 Deep Read 再次抓取
            "include_answer": True,
            "include_raw_content": True,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _get_user_agent(),
        }

        self._logger.info(f"Tavily search: {query[:60]}")
        try:
            resp = await client.post(
                self._api_url, json=payload, headers=headers, timeout=timeout
            )
        except httpx.ConnectError as e:
            self._logger.error(f"Tavily connection failed: {e}")
            return SearchResponse(query=query, engine=self.name, results=[])

        if resp.status_code != 200:
            self._logger.warning(
                f"Tavily HTTP {resp.status_code} | body={resp.text[:200]}"
            )
            return SearchResponse(query=query, engine=self.name, results=[])

        data = resp.json()
        raw_results = data.get("results", []) or []
        answer = data.get("answer", "") or ""
        results: list[SearchResult] = []
        max_chars = (
            get_config().features.function_calling.web_search.deep_read_max_chars
        )
        for item in raw_results[:max_results]:
            raw_content = item.get("raw_content", "") or ""
            full_content = raw_content[:max_chars] if raw_content else ""
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source=self.name,
                    full_content=full_content,
                )
            )

        self._logger.info(f"Tavily parsed {len(results)} results for: {query[:40]}")
        return SearchResponse(
            query=query,
            results=results,
            engine=self.name,
            total_results=len(raw_results),
            answer=answer,
        )

    async def health_check(self) -> bool:
        """健康检查：确认已配置 Tavily API Key（不发起网络请求，避免消耗额度）。"""
        return bool(self._get_api_key())

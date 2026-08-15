"""SearXNG 搜索引擎实现。"""

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.models import SearchResponse, SearchResult


def _get_user_agent() -> str:
    """从配置获取 User-Agent。"""
    return get_config().http.user_agent


class SearXNGEngine(BaseSearchEngine):
    """
    SearXNG 搜索引擎。

    通过 SearXNG 实例的 JSON API 执行搜索。
    需要在 config.json 中配置 engines.searxng.base_url 指向可用的 SearXNG 实例。
    """

    name = "searxng"

    def __init__(self, base_url: str = "") -> None:
        self._logger = get_logger("searxng")
        self._base_url = base_url.rstrip("/")
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

    def _get_base_url(self) -> str:
        """获取 SearXNG 实例地址，优先使用运行时配置。"""
        cfg = get_config()
        url = (
            cfg.features.function_calling.web_search.engines.searxng.base_url
            or self._base_url
        )
        return url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        base_url = self._get_base_url()
        if not base_url:
            self._logger.warning("SearXNG instance URL not configured")
            return SearchResponse(query=query, engine=self.name, results=[])

        cfg = get_config()
        timeout = cfg.features.function_calling.web_search.timeout_seconds
        return await self._do_search(
            self._get_client(), base_url, query, max_results, timeout
        )

    async def _do_search(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        query: str,
        max_results: int,
        timeout: float | None = None,
    ) -> SearchResponse:
        """实际搜索逻辑。"""
        if timeout is None:
            timeout = get_config().features.function_calling.web_search.timeout_seconds
        url = f"{base_url}/search"
        params = {
            "q": query,
            "format": "json",
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": _get_user_agent(),
        }

        self._logger.info(f"SearXNG search: {query[:60]} | instance={base_url}")
        try:
            resp = await client.get(
                url, params=params, headers=headers, timeout=timeout
            )
        except httpx.ConnectError as e:
            self._logger.error(f"SearXNG connection failed: {e}")
            return SearchResponse(query=query, engine=self.name, results=[])

        if resp.status_code != 200:
            self._logger.warning(
                f"SearXNG HTTP {resp.status_code} | body={resp.text[:200]}"
            )
            return SearchResponse(query=query, engine=self.name, results=[])

        data = resp.json()
        raw_results = data.get("results", [])
        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source=item.get("engine", ""),
                )
            )

        self._logger.info(f"SearXNG parsed {len(results)} results for: {query[:40]}")
        return SearchResponse(
            query=query,
            results=results,
            engine=self.name,
            total_results=len(raw_results),
        )

    async def health_check(self) -> bool:
        """健康检查：尝试访问 SearXNG 实例。"""
        base_url = self._get_base_url()
        if not base_url:
            return False
        try:
            headers = {
                "Accept": "application/json",
                "User-Agent": _get_user_agent(),
            }
            resp = await self._get_client().get(
                f"{base_url}/search",
                params={"q": "test", "format": "json"},
                headers=headers,
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

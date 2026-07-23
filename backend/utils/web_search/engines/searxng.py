"""SearXNG 搜索引擎实现。"""

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.models import SearchResponse, SearchResult


def _get_user_agent() -> str:
    """从配置获取 User-Agent。"""
    return get_config().features.web_search.user_agent


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

    def _get_base_url(self) -> str:
        """获取 SearXNG 实例地址，优先使用运行时配置。"""
        cfg = get_config()
        url = cfg.features.web_search.engines.searxng.base_url or self._base_url
        return url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        base_url = self._get_base_url()
        if not base_url:
            self._logger.warning("SearXNG 实例地址未配置")
            return SearchResponse(query=query, engine=self.name, results=[])

        cfg = get_config()
        client = httpx.AsyncClient(
            timeout=cfg.features.web_search.timeout_seconds,
            follow_redirects=True,
        )
        try:
            return await self._do_search(client, base_url, query, max_results)
        finally:
            await client.aclose()

    async def _do_search(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        """实际搜索逻辑。"""
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
            resp = await client.get(url, params=params, headers=headers)
        except httpx.ConnectError as e:
            self._logger.error(f"SearXNG 连接失败：{e}")
            return SearchResponse(query=query, engine=self.name, results=[])

        if resp.status_code != 200:
            self._logger.warning(f"SearXNG HTTP {resp.status_code} | body={resp.text[:200]}")
            return SearchResponse(query=query, engine=self.name, results=[])

        data = resp.json()
        raw_results = data.get("results", [])
        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source=item.get("engine", ""),
            ))

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
            client = httpx.AsyncClient(timeout=5)
            try:
                headers = {
                    "Accept": "application/json",
                    "User-Agent": _get_user_agent(),
                }
                resp = await client.get(
                    f"{base_url}/search",
                    params={"q": "test", "format": "json"},
                    headers=headers,
                )
                return resp.status_code == 200
            finally:
                await client.aclose()
        except Exception:
            return False

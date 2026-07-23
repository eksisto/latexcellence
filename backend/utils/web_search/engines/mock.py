"""Mock 搜索引擎 — 用于测试和降级。"""

from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.models import SearchResponse


class MockSearchEngine(BaseSearchEngine):
    """
    Mock 搜索引擎 — 用于测试和降级。

    始终返回空结果，保证架构可用但不发起真实网络请求。
    """

    name = "mock"

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=[],
            engine="mock",
            total_results=0,
        )

    async def health_check(self) -> bool:
        return True

"""搜索引擎抽象基类。"""

from abc import ABC, abstractmethod

from backend.utils.web_search.models import SearchResponse


class BaseSearchEngine(ABC):
    """
    搜索引擎抽象基类。

    子类需实现：
        - name: 引擎唯一标识
        - search(): 执行搜索
        - health_check(): 检查引擎是否可用
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """检查引擎是否可用（网络连通性、API Key 有效性等）。"""
        ...

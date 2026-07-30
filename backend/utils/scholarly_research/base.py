"""学术数据源抽象接口。"""

from abc import ABC, abstractmethod
from typing import Any

from .models import RelatedWorksResult, SearchScholarlyResult, WorkDetailResult


class BaseResearchProvider(ABC):
    """可插拔学术数据源的最小接口。"""

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        """检索学术作品。"""
        ...

    @abstractmethod
    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        """获取单篇作品详情。"""
        ...

    @abstractmethod
    async def find_related(
        self, seed_id: str, relation_type: str, **kwargs: Any
    ) -> RelatedWorksResult:
        """查找参考文献、被引作品或相似作品。"""
        ...

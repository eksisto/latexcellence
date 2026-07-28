"""DOI 元数据提供者抽象基类。"""

from abc import ABC, abstractmethod

from backend.modules.bib_manager.models import DoiLookupResult


class BaseMetadataProvider(ABC):
    """DOI 元数据提供者抽象基类。

    子类需实现：
        - name: 提供者唯一标识
        - lookup_by_doi(): 通过 DOI 查询元数据
        - health_check(): 检查 API 是否可用
    """

    name: str = "base"

    @abstractmethod
    async def lookup_by_doi(self, doi: str) -> DoiLookupResult | None:
        """通过 DOI 查询元数据，失败返回 None。"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """检查 API 是否可用。"""
        ...


def clean_doi(doi: str) -> str:
    """清理 DOI 格式：移除 URL 前缀和 "doi:" 前缀。"""
    clean = doi.strip()
    if clean.startswith("https://doi.org/"):
        clean = clean[len("https://doi.org/") :]
    elif clean.startswith("http://doi.org/"):
        clean = clean[len("http://doi.org/") :]
    elif clean.startswith("doi:"):
        clean = clean[len("doi:") :]
    return clean


def is_arxiv_doi(doi: str) -> bool:
    """判断是否为 arXiv DOI（前缀 10.48550/arXiv.）。"""
    return doi.startswith("10.48550/arXiv.")


def extract_arxiv_id(doi: str) -> str:
    """从 arXiv DOI 中提取 arXiv ID。

    例如：10.48550/arXiv.1706.03762 → 1706.03762
    """
    if is_arxiv_doi(doi):
        return doi[len("10.48550/arXiv.") :]
    return ""

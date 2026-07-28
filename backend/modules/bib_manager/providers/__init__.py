"""DOI 元数据提供者模块。

提供多家学术 API 数据源：
    - Crossref（主索引）
    - arXiv（预印本专用）
    - DataCite（补全层）
    - OpenAlex（补全层）
    - Europe PMC（补全层）
    - Semantic Scholar（补全层）
"""

from backend.modules.bib_manager.providers.base import BaseMetadataProvider
from backend.modules.bib_manager.providers.crossref import CrossrefProvider
from backend.modules.bib_manager.providers.arxiv import ArxivProvider
from backend.modules.bib_manager.providers.datacite import DataCiteProvider
from backend.modules.bib_manager.providers.openalex import OpenAlexProvider
from backend.modules.bib_manager.providers.europepmc import EuropePMCProvider
from backend.modules.bib_manager.providers.semantic import SemanticScholarProvider

__all__ = [
    "BaseMetadataProvider",
    "CrossrefProvider",
    "ArxivProvider",
    "DataCiteProvider",
    "OpenAlexProvider",
    "EuropePMCProvider",
    "SemanticScholarProvider",
]

"""可插拔学术数据源提供者。"""

from .arxiv import ArxivProvider
from .core import COREProvider
from .crossref import CrossrefProvider
from .datacite import DataCiteProvider
from .openalex import OpenAlexProvider
from .pubmed import PubMedProvider
from .semantic import SemanticScholarProvider
from .unpaywall import UnpaywallProvider

__all__ = [
    "ArxivProvider",
    "COREProvider",
    "CrossrefProvider",
    "DataCiteProvider",
    "OpenAlexProvider",
    "PubMedProvider",
    "SemanticScholarProvider",
    "UnpaywallProvider",
]

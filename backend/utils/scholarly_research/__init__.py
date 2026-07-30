"""学术论文检索与引文追踪工具 — 多数据源联邦。"""

from .manager import (
    ResearchManager,
    close_research_http_client,
    get_research_manager,
    initialize_research_http_client,
    reset_research_manager,
)
from .models import (
    Author,
    Concept,
    FullTextResult,
    RelatedWorksResult,
    ResearchDataResource,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

__all__ = [
    "Author",
    "Concept",
    "FullTextResult",
    "RelatedWorksResult",
    "ResearchDataResource",
    "ResearchManager",
    "ScholarlyWork",
    "SearchScholarlyResult",
    "WorkDetailResult",
    "close_research_http_client",
    "get_research_manager",
    "initialize_research_http_client",
    "reset_research_manager",
]

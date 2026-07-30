"""学术研究共享工具函数 — 规范化、去重、排序与结果限制。"""

import re
from typing import Any

from .models import ScholarlyWork

_DOI_PREFIX_PATTERN = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def _normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    return _DOI_PREFIX_PATTERN.sub("", value.strip()).lower()


def _limit_results(requested: int | None, default: int, max_allowed: int = 100) -> int:
    from backend.core.config import get_config

    configured = get_config().features.function_calling.scholarly_research.max_results
    return max(1, min(requested or default, configured, max_allowed))


def _sort_works(works: list[ScholarlyWork], sort_by: str) -> list[ScholarlyWork]:
    if sort_by == "publication_date":
        return sorted(works, key=lambda w: w.year, reverse=True)
    if sort_by == "citation_count":
        return sorted(works, key=lambda w: w.citation_count, reverse=True)
    return works


def _dedup_works(works: list[ScholarlyWork]) -> list[ScholarlyWork]:
    """按 DOI、OpenAlex ID、arXiv ID、PMID 或标题年份去重，优先保留正式版本。"""
    seen: dict[str, ScholarlyWork] = {}
    order: list[str] = []
    for work in works:
        if work.doi:
            key = f"doi:{_normalize_doi(work.doi)}"
        elif work.openalex_id:
            key = f"openalex:{work.openalex_id.upper()}"
        elif work.pmid:
            key = f"pmid:{work.pmid}"
        elif work.arxiv_id:
            key = f"arxiv:{work.arxiv_id.lower()}"
        else:
            normalized_title = " ".join(work.title.lower().split())
            key = f"title:{normalized_title}:{work.year}"

        if key not in seen:
            seen[key] = work
            order.append(key)
            continue

        existing = seen[key]
        sources = list(dict.fromkeys(existing.source_records + work.source_records))
        if work.work_type != "preprint" and existing.work_type == "preprint":
            work.source_records = sources
            seen[key] = work
        else:
            existing.source_records = sources
    return [seen[key] for key in order]

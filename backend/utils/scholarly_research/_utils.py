"""学术研究共享工具函数 — 规范化、去重、排序与结果限制。"""

import re
from typing import Any

from .models import FieldProvenance, ScholarlyWork, WorkVersion

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


def _rrf_rank(
    provider_results: list[list[ScholarlyWork]], *, k: int = 60
) -> list[ScholarlyWork]:
    """用 Reciprocal Rank Fusion 合并不同 Provider 的原始排名。"""
    scores: dict[str, float] = {}
    records: dict[str, ScholarlyWork] = {}
    for results in provider_results:
        for rank, work in enumerate(results, 1):
            key = _work_key(work)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            records.setdefault(key, work)
    ranked = sorted(records, key=lambda key: scores[key], reverse=True)
    for key in ranked:
        records[key].ranking_explanation = {
            "rrf_score": scores[key],
            "provider_consensus": float(
                sum(
                    1
                    for results in provider_results
                    if any(_work_key(item) == key for item in results)
                )
            ),
        }
    return [records[key] for key in ranked]


def _work_key(work: ScholarlyWork) -> str:
    if work.doi:
        return f"doi:{_normalize_doi(work.doi)}"
    for name in ("pmid", "pmcid", "arxiv_id", "openalex_id", "semantic_scholar_id"):
        value = getattr(work, name)
        if value:
            return f"{name}:{value.casefold()}"
    return f"title:{' '.join(work.title.casefold().split())}:{work.year}"


def _dedup_works(works: list[ScholarlyWork]) -> list[ScholarlyWork]:
    """按 DOI、OpenAlex ID、arXiv ID、PMID 或标题年份去重，优先保留正式版本。"""
    seen: dict[str, ScholarlyWork] = {}
    order: list[str] = []
    for work in works:
        identifiers = work.identifiers or {}
        exact = (
            _normalize_doi(work.doi),
            work.pmid,
            work.pmcid,
            work.arxiv_id.lower(),
            work.openalex_id.upper(),
            work.semantic_scholar_id,
        )
        if any(exact):
            key = next(
                f"{kind}:{value}"
                for kind, value in zip(
                    ("doi", "pmid", "pmcid", "arxiv", "openalex", "semantic"), exact
                )
                if value
            )
        else:
            normalized_title = " ".join(work.title.lower().split())
            key = f"title:{normalized_title}:{work.year}"

        # 预印本与正式版往往没有共同标识符；只在题名、首作者和年份
        # 高度接近时关联，避免把同名作品错误合并。
        if key not in seen and work.title:
            title_key = " ".join(work.title.casefold().split())
            first_author = work.authors[0].name.casefold() if work.authors else ""
            for candidate_key, candidate in seen.items():
                candidate_title = " ".join(candidate.title.casefold().split())
                candidate_author = (
                    candidate.authors[0].name.casefold() if candidate.authors else ""
                )
                if (
                    title_key == candidate_title
                    and first_author
                    and first_author == candidate_author
                    and abs(work.year - candidate.year) <= 1
                ):
                    key = candidate_key
                    break

        if key not in seen:
            work.identifiers.update(
                {
                    k: v
                    for k, v in {
                        "doi": work.doi,
                        "pmid": work.pmid,
                        "pmcid": work.pmcid,
                        "arxiv": work.arxiv_id,
                        "openalex": work.openalex_id,
                        "semantic_scholar": work.semantic_scholar_id,
                    }.items()
                    if v
                }
            )
            seen[key] = work
            order.append(key)
            continue

        existing = seen[key]
        sources = list(dict.fromkeys(existing.source_records + work.source_records))
        if work.work_type != "preprint" and existing.work_type == "preprint":
            work.source_records = sources
            work.versions = _unique_versions(
                existing.versions
                + [
                    WorkVersion(
                        identifier=existing.doi or existing.openalex_id,
                        version_type=existing.work_type,
                        provider=existing.source_records[0],
                        title=existing.title,
                    )
                ]
            )
            seen[key] = work
        else:
            existing.source_records = sources
            if work.doi or work.openalex_id:
                existing.versions = _unique_versions(
                    existing.versions
                    + [
                        WorkVersion(
                            identifier=work.doi or work.openalex_id,
                            version_type=work.work_type,
                            provider=work.source_records[0],
                            title=work.title,
                        )
                    ]
                )
        for field in ("title", "year", "doi"):
            value = str(getattr(work, field))
            if value and value != str(getattr(seen[key], field)):
                seen[key].field_provenance.append(
                    FieldProvenance(
                        field=field,
                        provider=work.source_records[0],
                        value=value,
                        conflict=True,
                    )
                )
    return [seen[key] for key in order]


def _unique_versions(versions: list[WorkVersion]) -> list[WorkVersion]:
    result: list[WorkVersion] = []
    seen: set[str] = set()
    for version in versions:
        if version.identifier and version.identifier not in seen:
            seen.add(version.identifier)
            result.append(version)
    return result

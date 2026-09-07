"""Semantic Scholar API 提供者 — 引文图谱、论文检索与影响力指标。"""

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger

from .._utils import _normalize_doi
from ..base import BaseResearchProvider
from ..models import (
    Author,
    RelatedWorksResult,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

_API_BASE = "https://api.semanticscholar.org/graph/v1"
_MAX_RELATED_CANDIDATES = 500
_OPENALEX_ID_PATTERN = re.compile(r"^[Ww]\d+$")
_ARXIV_VERSION = re.compile(r"v\d+$")

_SORT_FIELDS = {
    "citation_count": "citationCount:desc",
    "publication_date": "publicationDate:desc",
    "relevance": "relevance",
}

_PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,tldr,year,authors,"
    "venue,citationCount,influentialCitationCount,publicationTypes,"
    "openAccessPdf,fieldsOfStudy,publicationDate,journal,"
    "referenceCount"
)

_SEARCH_FIELDS = (
    "paperId,externalIds,title,abstract,tldr,year,authors,"
    "venue,citationCount,publicationTypes,openAccessPdf"
)

_RELATED_FIELDS = (
    "paperId,externalIds,title,year,authors,"
    "venue,citationCount,publicationTypes,openAccessPdf"
)


def _clean_arxiv_id(arxiv_id: str | None) -> str:
    if not arxiv_id:
        return ""
    return _ARXIV_VERSION.sub("", arxiv_id.strip()).removeprefix("arXiv:")


def _build_work_from_s2(paper: dict[str, Any]) -> ScholarlyWork:
    ext = paper.get("externalIds") or {}
    oa_info = paper.get("openAccessPdf") or {}
    venue = paper.get("venue") or "" if isinstance(paper.get("venue"), str) else ""
    journal_name = ""
    if isinstance(paper.get("venue"), dict):
        journal_name = paper["venue"].get("name") or ""
    elif isinstance(paper.get("venue"), str):
        journal_name = venue
    journal_info = paper.get("journal") or {}
    if isinstance(journal_info, dict):
        if not journal_name:
            journal_name = journal_info.get("name") or ""

    authors = [
        Author(name=author.get("name") or "Unknown author")
        for author in (paper.get("authors") or [])
    ]
    pub_types = paper.get("publicationTypes") or []
    work_type = ""
    if "Review" in pub_types:
        work_type = "review"
    elif "JournalArticle" in pub_types:
        work_type = "journal_article"
    elif "Conference" in pub_types:
        work_type = "conference_paper"

    is_oa = bool(oa_info.get("url"))
    oa_url = oa_info.get("url") or ""

    return ScholarlyWork(
        openalex_id=_normalize_doi(ext.get("DBLP")) if ext.get("DBLP") else "",
        semantic_scholar_id=paper.get("paperId") or "",
        doi=_normalize_doi(ext.get("DOI")),
        pmid=ext.get("PubMed") or "",
        arxiv_id=_clean_arxiv_id(ext.get("ArXiv")),
        title=paper.get("title") or "Untitled work",
        authors=authors,
        year=int(paper.get("year") or 0),
        journal=journal_name,
        work_type=work_type,
        abstract=paper.get("abstract") or (paper.get("tldr") or {}).get("text") or "",
        citation_count=int(paper.get("citationCount") or 0),
        is_oa=is_oa,
        oa_url=oa_url,
        oa_status="gold" if is_oa else "",
        source_records=["semantic_scholar"],
        cited_by_count=int(paper.get("citationCount") or 0),
        referenced_works_count=int(paper.get("referenceCount") or 0),
    )


class SemanticScholarProvider(BaseResearchProvider):
    name = "semantic_scholar"
    capabilities = frozenset(
        {"search", "details", "references", "citations", "similar"}
    )

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.semantic")

    def _api_key(self) -> str:
        cfg = get_config().features.function_calling.scholarly_research
        return cfg.providers.semantic_scholar.api_key

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        request_params = dict(params or {})
        headers: dict[str, str] = {"User-Agent": self._get_user_agent()}
        api_key = self._api_key()
        if api_key:
            headers["x-api-key"] = api_key
        last_error: Exception | None = None
        last_status = 0
        for attempt in range(3):
            try:
                response = await self._client.get(
                    url,
                    params=request_params,
                    headers=headers,
                    timeout=cfg.timeout_seconds,
                )
                if response.status_code == 429:
                    await asyncio.sleep(1.0 * (2**attempt))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError(
                        "Semantic Scholar returned a non-object JSON response"
                    )
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else 0
                )
                last_status = status
                if status == 404 or (status and status < 500 and status != 429):
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        self._logger.error(
            "S2 request failed | path=%s | status=%s | error_type=%s",
            path,
            last_status or "network",
            type(last_error).__name__,
        )
        detail = f"HTTP {last_status}" if last_status else "network error"
        raise RuntimeError(f"Semantic Scholar request failed: {detail}") from last_error

    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        return await self.search_works(query=query, **filters)

    async def search_works(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        authors: list[str] | None = None,
        work_types: list[str] | None = None,
        sort_by: str = "relevance",
        max_results: int | None = None,
    ) -> SearchScholarlyResult:
        from ..manager import _limit_results

        if not query.strip():
            raise ValueError("query must not be empty")
        limit = _limit_results(max_results, 20)
        params: dict[str, Any] = {
            "query": query.strip(),
            "limit": min(limit, 100),
            "fields": _SEARCH_FIELDS,
        }
        year = ""
        if year_from:
            year = f"{year_from}-"
        if year_to:
            year = f"{year}{year_to}"
        if year:
            params["year"] = year

        started = time.monotonic()
        try:
            payload = await self._request_json("/paper/search", params=params)
        except RuntimeError:
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        works = [_build_work_from_s2(item) for item in payload.get("data") or []]
        elapsed_ms = (time.monotonic() - started) * 1000
        total = int(payload.get("total") or len(works))
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=total,
            per_page=limit,
            source=self.name,
            search_time_ms=elapsed_ms,
        )

    async def get_details(self, work_id: str) -> WorkDetailResult | None:

        identifier = work_id.strip()
        url_id = identifier
        search_params: dict[str, Any] | None = None

        if identifier.startswith("DOI:") or identifier.startswith("10."):
            doi = _normalize_doi(identifier)
            url_id = f"DOI:{doi}"
        elif not (identifier.startswith("CorpusId:") or len(identifier) == 40):
            search_params = {
                "query": identifier,
                "limit": 1,
                "fields": _PAPER_FIELDS,
            }

        if search_params:
            payload = await self._request_json("/paper/search", params=search_params)
            results = payload.get("data") or []
            if not results:
                return None
            paper = results[0]
        else:
            encoded = quote(url_id, safe="")
            try:
                payload = await self._request_json(
                    f"/paper/{encoded}",
                    params={"fields": _PAPER_FIELDS},
                )
            except RuntimeError as exc:
                if "404" in str(exc):
                    return None
                raise
            paper = payload

        return WorkDetailResult(work=_build_work_from_s2(paper), source=self.name)

    async def find_related(
        self,
        seed_id: str | None = None,
        relation_type: str = "similar",
        **kwargs: Any,
    ) -> RelatedWorksResult:
        work_id = str(kwargs.pop("work_id", seed_id or ""))
        max_results = kwargs.pop("max_results", None)
        sort_by = kwargs.pop("sort_by", "citation_count")
        if kwargs:
            self._logger.debug("Ignored related-work arguments: %s", sorted(kwargs))
        return await self.find_related_works(
            work_id=work_id,
            relation_type=relation_type,
            max_results=max_results,
            sort_by=sort_by,
        )

    async def find_related_works(
        self,
        work_id: str,
        relation_type: str,
        max_results: int | None = None,
        sort_by: str = "citation_count",
    ) -> RelatedWorksResult:
        from .._utils import _dedup_works, _limit_results, _sort_works

        if relation_type not in {"references", "cited_by", "similar"}:
            raise ValueError("relation_type must be references, cited_by, or similar")
        if relation_type == "similar":
            return RelatedWorksResult(
                seed_id=work_id,
                relation_type=relation_type,
                works=[],
                total=0,
            )

        limit = _limit_results(max_results, 20)
        identifier = work_id.strip()
        url_id = identifier
        if identifier.startswith("10."):
            url_id = f"DOI:{_normalize_doi(identifier)}"

        encoded = quote(url_id, safe="")
        endpoint = "references" if relation_type == "references" else "citations"
        params: dict[str, Any] = {
            "fields": _RELATED_FIELDS,
            "limit": min(limit, 100),
        }
        try:
            payload = await self._request_json(
                f"/paper/{encoded}/{endpoint}", params=params
            )
        except RuntimeError as exc:
            if "404" in str(exc):
                return RelatedWorksResult(seed_id=work_id, relation_type=relation_type)
            raise

        items = payload.get("data") or []
        seed_paper_id = ""
        works: list[ScholarlyWork] = []
        for item in items:
            paper = (
                item.get("citedPaper")
                if endpoint == "citations"
                else item.get("citedPaper") or item
            )
            work = _build_work_from_s2(paper)
            if not seed_paper_id and item.get("citingPaper"):
                seed_paper_id = item["citingPaper"].get("paperId") or ""
            works.append(work)

        works = _dedup_works(works)
        works = _sort_works(works, sort_by)[:limit]
        return RelatedWorksResult(
            seed_id=seed_paper_id or work_id,
            relation_type=relation_type,
            works=works,
            total=len(works),
        )

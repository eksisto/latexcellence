"""Crossref API 提供者 — DOI 元数据查询与出版商信息。"""

import asyncio
import time
from typing import Any

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

_API_BASE = "https://api.crossref.org"


def _build_work_from_crossref(item: dict[str, Any]) -> ScholarlyWork:
    authors = []
    for author in item.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        name = f"{given} {family}".strip()
        orcid = ""
        orcid_url = author.get("ORCID", "")
        if orcid_url:
            orcid = orcid_url.removeprefix("https://orcid.org/").removeprefix(
                "http://orcid.org/"
            )
        affiliation_list = author.get("affiliation", [])
        institution = ""
        if affiliation_list:
            institution = affiliation_list[0].get("name", "")
        authors.append(Author(name=name, orcid=orcid, institution=institution))

    published = item.get("published-print") or item.get("published-online") or {}
    date_parts = published.get("date-parts", [[0]])
    year = 0
    if date_parts and date_parts[0]:
        year = int(date_parts[0][0])

    container = item.get("container-title", [])
    journal = container[0] if container else item.get("publisher", "")

    refs = item.get("reference", [])
    referenced_ids = [ref.get("DOI", "") for ref in refs if ref.get("DOI")]

    return ScholarlyWork(
        doi=_normalize_doi(item.get("DOI", "")),
        title=" ".join((item.get("title") or ["Untitled work"])[0].split()),
        authors=authors,
        year=year,
        journal=journal,
        volume=item.get("volume", ""),
        issue=item.get("issue", ""),
        pages=item.get("page", ""),
        publisher=item.get("publisher", ""),
        work_type=item.get("type", ""),
        abstract=item.get("abstract", ""),
        citation_count=int(item.get("is-referenced-by-count") or 0),
        source_records=["crossref"],
        referenced_works_count=len(referenced_ids),
    )


class CrossrefProvider(BaseResearchProvider):
    name = "crossref"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.crossref")

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        request_params = dict(params or {})
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(
                    url,
                    params=request_params,
                    headers={"User-Agent": self._get_user_agent()},
                    timeout=cfg.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Crossref returned a non-object JSON response")
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else 0
                )
                if status == 404 or (status and status < 500):
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        detail = (
            f"HTTP {status}"
            if last_error and isinstance(last_error, httpx.HTTPStatusError)
            else "error"
        )
        raise RuntimeError(f"Crossref request failed: {detail}") from last_error

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
        from .._utils import _limit_results

        if not query.strip():
            raise ValueError("query must not be empty")
        limit = _limit_results(max_results, 20)
        params: dict[str, Any] = {
            "query": query.strip(),
            "rows": min(limit, 100),
        }
        started = time.monotonic()
        try:
            payload = await self._request_json("/works", params=params)
        except RuntimeError:
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        items = payload.get("message", {}).get("items", [])
        works = [_build_work_from_crossref(item) for item in items]
        elapsed_ms = (time.monotonic() - started) * 1000
        total = int(payload.get("message", {}).get("total-results", len(works)))
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=total,
            per_page=limit,
            source=self.name,
            search_time_ms=elapsed_ms,
        )

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        doi = _normalize_doi(work_id)
        if not doi.startswith("10."):
            return None
        try:
            payload = await self._request_json(f"/works/{doi}")
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        message = payload.get("message", {})
        if not message:
            return None
        return WorkDetailResult(
            work=_build_work_from_crossref(message), source=self.name
        )

    async def find_related(
        self,
        seed_id: str | None = None,
        relation_type: str = "similar",
        **kwargs: Any,
    ) -> RelatedWorksResult:
        work_id = str(kwargs.pop("work_id", seed_id or ""))
        if kwargs:
            self._logger.debug("Ignored related-work arguments: %s", sorted(kwargs))
        return await self.find_related_works(
            work_id=work_id,
            relation_type=relation_type,
            max_results=kwargs.pop("max_results", None),
            sort_by=kwargs.pop("sort_by", "citation_count"),
        )

    async def find_related_works(
        self,
        work_id: str,
        relation_type: str,
        max_results: int | None = None,
        sort_by: str = "citation_count",
    ) -> RelatedWorksResult:
        if relation_type != "references":
            return RelatedWorksResult(seed_id=work_id, relation_type=relation_type)
        detail = await self.get_details(work_id)
        if detail is None:
            return RelatedWorksResult(seed_id=work_id, relation_type=relation_type)
        # Crossref response message includes reference list with DOIs
        # References are fetched via the get_details call above
        return RelatedWorksResult(
            seed_id=work_id,
            relation_type=relation_type,
            total=detail.work.referenced_works_count,
        )

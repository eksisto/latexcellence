"""OpenAlex API 提供者 — 学术作品检索、详情查询与引文追踪。"""

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
    Concept,
    RelatedWorksResult,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

_API_BASE = "https://api.openalex.org"
_MAX_RELATED_CANDIDATES = 500
_OPENALEX_ID_PATTERN = re.compile(r"^[Ww]\d+$")

_OPENALEX_TO_WORK_TYPE = {
    "article": "journal_article",
    "book-chapter": "book_chapter",
    "dataset": "dataset",
    "posted-content": "preprint",
    "preprint": "preprint",
    "proceedings-article": "conference_paper",
    "review": "review",
}
_WORK_TYPE_TO_OPENALEX = {
    "journal_article": "article",
    "book_chapter": "book-chapter",
    "conference_paper": "article",
    "dataset": "dataset",
    "preprint": "preprint",
    "review": "review",
}
_SORT_FIELDS = {
    "citation_count": "cited_by_count:desc",
    "publication_date": "publication_date:desc",
    "relevance": "relevance_score:desc",
}


def _short_openalex_id(value: str | None) -> str:
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


def _normalize_work_identifier(work_id: str) -> str:
    value = work_id.strip()
    if not value:
        raise ValueError("work_id must not be empty")
    short_id = _short_openalex_id(value)
    if _OPENALEX_ID_PATTERN.fullmatch(short_id):
        return short_id.upper()
    doi = _normalize_doi(value)
    if doi.startswith("10.") and "/" in doi:
        return f"https://doi.org/{doi}"
    raise ValueError(
        "work_id must be an OpenAlex work ID (for example W2741809807) or DOI"
    )


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positioned_words = [
        (pos, word) for word, positions in inverted_index.items() for pos in positions
    ]
    positioned_words.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned_words)


def _build_work_from_openalex(data: dict[str, Any]) -> ScholarlyWork:
    ids = data.get("ids") or {}
    authors: list[Author] = []
    for authorship in data.get("authorships") or []:
        author_data = authorship.get("author") or {}
        institutions = authorship.get("institutions") or []
        institution = ""
        if institutions:
            institution = institutions[0].get("display_name") or ""
        authors.append(
            Author(
                name=author_data.get("display_name") or "Unknown author",
                orcid=(author_data.get("orcid") or "")
                .removeprefix("https://orcid.org/")
                .removeprefix("http://orcid.org/"),
                institution=institution,
            )
        )

    topic_data = data.get("topics") or data.get("concepts") or []
    concepts = [
        Concept(
            id=_short_openalex_id(item.get("id")),
            name=item.get("display_name") or "",
            score=float(item.get("score") or 0),
            level=int(item.get("level") or 0),
        )
        for item in topic_data
        if item.get("id") and item.get("display_name")
    ]
    keywords = [
        item.get("display_name") or ""
        for item in (data.get("keywords") or [])
        if item.get("display_name")
    ]

    primary_location = data.get("primary_location") or {}
    source = primary_location.get("source") or {}
    best_oa_location = data.get("best_oa_location") or {}
    open_access = data.get("open_access") or {}
    biblio = data.get("biblio") or {}
    percentile = data.get("citation_normalized_percentile") or {}
    openalex_type = data.get("type") or ""
    work_type = _OPENALEX_TO_WORK_TYPE.get(openalex_type, openalex_type)
    if openalex_type == "article" and source.get("type") == "conference":
        work_type = "conference_paper"
    is_oa = bool(open_access.get("is_oa"))
    oa_url = ""
    if is_oa:
        oa_url = (
            best_oa_location.get("pdf_url")
            or best_oa_location.get("landing_page_url")
            or open_access.get("oa_url")
            or ""
        )

    first_page = biblio.get("first_page") or ""
    last_page = biblio.get("last_page") or ""
    pages = first_page
    if first_page and last_page and last_page != first_page:
        pages = f"{first_page}-{last_page}"

    return ScholarlyWork(
        openalex_id=_short_openalex_id(data.get("id") or ids.get("openalex")),
        doi=_normalize_doi(data.get("doi") or ids.get("doi")),
        pmid=_short_openalex_id(ids.get("pmid")),
        pmcid=_short_openalex_id(ids.get("pmcid")),
        arxiv_id=_short_openalex_id(ids.get("arxiv")),
        title=data.get("display_name") or data.get("title") or "Untitled work",
        authors=authors,
        year=int(data.get("publication_year") or 0),
        journal=source.get("display_name") or "",
        volume=str(biblio.get("volume") or ""),
        issue=str(biblio.get("issue") or ""),
        pages=pages,
        publisher=source.get("host_organization_name") or "",
        work_type=work_type,
        abstract=_reconstruct_abstract(data.get("abstract_inverted_index")),
        keywords=keywords,
        concepts=concepts,
        citation_count=int(data.get("cited_by_count") or 0),
        normalized_citation_percentile=float(percentile.get("value") or 0),
        is_oa=is_oa,
        oa_url=oa_url,
        oa_status=open_access.get("oa_status") or "",
        referenced_works_count=int(data.get("referenced_works_count") or 0),
        cited_by_count=int(data.get("cited_by_count") or 0),
    )


class OpenAlexProvider(BaseResearchProvider):
    name = "openalex"
    capabilities = frozenset(
        {"search", "details", "references", "citations", "similar"}
    )

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.openalex")

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        request_params = dict(params or {})
        api_key = cfg.providers.openalex.api_key
        if api_key:
            request_params["api_key"] = api_key
        last_error: Exception | None = None
        last_status = 0
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
                    raise RuntimeError("OpenAlex returned a non-object JSON response")
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else 0
                )
                last_status = status
                if attempt == 2 or (status and status != 429 and status < 500):
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        self._logger.error(
            "OpenAlex request failed | path=%s | status=%s | error_type=%s",
            path,
            last_status or "network",
            type(last_error).__name__,
        )
        detail = f"HTTP {last_status}" if last_status else "network error"
        raise RuntimeError(f"OpenAlex request failed: {detail}") from last_error

    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        return await self.search_works(query=query, **filters)

    async def _resolve_author_ids(self, authors: list[str]) -> list[str]:
        author_ids: list[str] = []
        for author_name in authors:
            name = author_name.strip()
            if not name:
                continue
            payload = await self._request_json(
                "/authors", params={"search": name, "per-page": 1}
            )
            results = payload.get("results") or []
            if results:
                author_id = _short_openalex_id(results[0].get("id"))
                if author_id:
                    author_ids.append(author_id)
        return author_ids

    async def _fetch_works_by_ids(
        self,
        work_ids: list[str],
        max_results: int,
        sort_by: str = "citation_count",
    ) -> tuple[list[ScholarlyWork], int]:
        from .._utils import _dedup_works, _sort_works

        all_unique_ids = list(dict.fromkeys(filter(None, work_ids)))
        candidate_ids = all_unique_ids[:_MAX_RELATED_CANDIDATES]
        if not candidate_ids:
            return [], 0

        async def fetch_batch(batch: list[str]) -> list[ScholarlyWork]:
            params: dict[str, Any] = {
                "filter": f"openalex_id:{'|'.join(batch)}",
                "per-page": len(batch),
            }
            if sort_by in _SORT_FIELDS:
                params["sort"] = _SORT_FIELDS[sort_by]
            payload = await self._request_json("/works", params=params)
            return [
                _build_work_from_openalex(item) for item in payload.get("results") or []
            ]

        batches = [
            candidate_ids[i : i + 100] for i in range(0, len(candidate_ids), 100)
        ]
        fetched_batches = await asyncio.gather(
            *(fetch_batch(batch) for batch in batches)
        )
        works = _dedup_works(
            [w for batch_works in fetched_batches for w in batch_works]
        )
        return _sort_works(works, sort_by)[:max_results], len(all_unique_ids)

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
        from .._utils import _dedup_works, _limit_results

        if not query.strip():
            raise ValueError("query must not be empty")
        if year_from and year_to and year_from > year_to:
            raise ValueError("year_from must be less than or equal to year_to")
        limit = _limit_results(max_results, 20)
        openalex_filters: list[str] = []
        if year_from:
            openalex_filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            openalex_filters.append(f"to_publication_date:{year_to}-12-31")
        if authors:
            author_ids = await self._resolve_author_ids(authors)
            if not author_ids:
                return SearchScholarlyResult(
                    query=query, per_page=limit, source=self.name
                )
            openalex_filters.append(f"authorships.author.id:{'|'.join(author_ids)}")
        if work_types:
            mapped_types = [
                _WORK_TYPE_TO_OPENALEX.get(wt, wt) for wt in work_types if wt
            ]
            if mapped_types:
                openalex_filters.append(f"type:{'|'.join(mapped_types)}")
            if work_types == ["conference_paper"]:
                openalex_filters.append("primary_location.source.type:conference")

        params: dict[str, Any] = {
            "search": query.strip(),
            "per-page": limit,
            "sort": _SORT_FIELDS.get(sort_by, _SORT_FIELDS["relevance"]),
        }
        if openalex_filters:
            params["filter"] = ",".join(openalex_filters)
        started = time.monotonic()
        payload = await self._request_json("/works", params=params)
        works = _dedup_works(
            [_build_work_from_openalex(item) for item in payload.get("results") or []]
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        total = int((payload.get("meta") or {}).get("count") or len(works))
        self._logger.info(
            "OpenAlex search completed | query=%s | results=%s | time=%.0fms",
            query[:80],
            total,
            elapsed_ms,
        )
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=total,
            per_page=limit,
            source=self.name,
            search_time_ms=elapsed_ms,
        )

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        identifier = _normalize_work_identifier(work_id)
        encoded = quote(identifier, safe=":/")
        try:
            payload = await self._request_json(f"/works/{encoded}")
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        return WorkDetailResult(
            work=_build_work_from_openalex(payload), source=self.name
        )

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
        from .._utils import _dedup_works, _limit_results

        if relation_type not in {"references", "cited_by", "similar"}:
            raise ValueError("relation_type must be references, cited_by, or similar")
        limit = _limit_results(max_results, 20)
        identifier = _normalize_work_identifier(work_id)
        seed_payload = await self._request_json(
            f"/works/{quote(identifier, safe=':/')}"
        )
        seed_openalex_id = _short_openalex_id(seed_payload.get("id"))

        if relation_type == "references":
            referenced_ids = [
                _short_openalex_id(item)
                for item in seed_payload.get("referenced_works") or []
            ]
            works, total = await self._fetch_works_by_ids(
                referenced_ids, limit, sort_by
            )
            return RelatedWorksResult(
                seed_id=seed_openalex_id,
                relation_type=relation_type,
                works=works,
                total=total,
            )

        if relation_type == "cited_by":
            relation_filter = f"cites:{seed_openalex_id}"
        else:
            related_ids = [
                _short_openalex_id(item)
                for item in seed_payload.get("related_works") or []
            ]
            works, total = await self._fetch_works_by_ids(related_ids, limit, sort_by)
            return RelatedWorksResult(
                seed_id=seed_openalex_id,
                relation_type=relation_type,
                works=works,
                total=total,
            )

        payload = await self._request_json(
            "/works",
            params={
                "filter": relation_filter,
                "sort": (
                    _SORT_FIELDS["citation_count"]
                    if sort_by == "relevance"
                    else _SORT_FIELDS.get(sort_by, _SORT_FIELDS["citation_count"])
                ),
                "per-page": limit,
            },
        )
        works = [
            _build_work_from_openalex(item) for item in payload.get("results") or []
        ]
        works = [w for w in works if w.openalex_id != seed_openalex_id]
        works = _dedup_works(works)[:limit]
        total = int((payload.get("meta") or {}).get("count") or len(works))
        return RelatedWorksResult(
            seed_id=seed_openalex_id,
            relation_type=relation_type,
            works=works,
            total=max(total, len(works)),
        )

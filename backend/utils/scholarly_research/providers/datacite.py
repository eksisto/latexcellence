"""DataCite API 提供者 — 研究数据 DOI 检索与数据集元数据查询。"""

import time
from typing import Any

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger

from ..base import BaseResearchProvider
from ..models import (
    Author,
    RelatedWorksResult,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

_API_BASE = "https://api.datacite.org"


def _build_work_from_datacite(item: dict[str, Any]) -> ScholarlyWork:
    attrs = item.get("attributes") or {}
    creators = attrs.get("creators", [])
    authors = [
        Author(
            name=creator.get("name", "Unknown author"),
            orcid=(
                (
                    creator.get("nameIdentifiers", [{}])[0]
                    .get("nameIdentifier", "")
                    .removeprefix("https://orcid.org/")
                )
                if creator.get("nameIdentifiers")
                else ""
            ),
        )
        for creator in creators
    ]
    pub_year = attrs.get("publicationYear", 0)
    year = int(pub_year) if pub_year else 0
    types = attrs.get("types", {})
    resource_type = types.get("resourceTypeGeneral", "")
    return ScholarlyWork(
        doi=attrs.get("doi", ""),
        title=attrs.get("title", "Untitled resource"),
        authors=authors,
        year=year,
        publisher=attrs.get("publisher", ""),
        work_type="dataset" if resource_type == "Dataset" else resource_type.lower(),
        abstract=attrs.get("description", ""),
        source_records=["datacite"],
    )


class DataCiteProvider(BaseResearchProvider):
    name = "datacite"
    capabilities = frozenset({"search", "details", "dataset_search"})

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.datacite")

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        request_params = dict(params or {})
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
                raise RuntimeError("DataCite returned a non-object JSON response")
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError("DataCite DOI not found") from exc
            raise RuntimeError(
                f"DataCite request failed: HTTP {exc.response.status_code}"
            ) from exc

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
        limit = _limit_results(max_results, 10)
        params: dict[str, Any] = {
            "query": query.strip(),
            "page[size]": min(limit, 100),
        }
        started = time.monotonic()
        try:
            payload = await self._request_json("/dois", params=params)
        except RuntimeError:
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        items = payload.get("data", [])
        works = [_build_work_from_datacite(item) for item in items]
        elapsed_ms = (time.monotonic() - started) * 1000
        total = int(payload.get("meta", {}).get("total", len(works)))
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
        try:
            payload = await self._request_json(f"/dois/{identifier}")
        except RuntimeError as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise
        data = payload.get("data", {})
        if not data:
            return None
        return WorkDetailResult(work=_build_work_from_datacite(data), source=self.name)

    async def find_related(
        self, seed_id: str | None = None, relation_type: str = "similar", **kwargs: Any
    ) -> RelatedWorksResult:
        return RelatedWorksResult(seed_id=seed_id or "", relation_type=relation_type)

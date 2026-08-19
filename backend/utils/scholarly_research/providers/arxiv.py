"""arXiv API 提供者 — 预印本检索与元数据查询。"""

import time
from typing import Any
from urllib.parse import quote

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

_API_BASE = "https://export.arxiv.org/api"


def _build_work_from_arxiv(entry: dict[str, Any]) -> ScholarlyWork:
    arxiv_id = entry.get("id", "").replace("http://arxiv.org/abs/", "")
    authors = [
        Author(name=name.strip())
        for name in entry.get("authors", "").split(",")
        if name.strip()
    ]
    published = entry.get("published", "")
    year = 0
    if published:
        try:
            year = int(published.split("-")[0])
        except ValueError:
            pass
    categories = [
        cat.strip()
        for cat in entry.get("primary_category", "").split(",")
        if cat.strip()
    ]
    return ScholarlyWork(
        arxiv_id=arxiv_id,
        doi=entry.get("doi", ""),
        title=entry.get("title", "Untitled work"),
        authors=authors,
        year=year,
        journal=f"arXiv:{', '.join(categories)}" if categories else "arXiv",
        work_type="preprint",
        abstract=entry.get("summary", ""),
        source_records=["arxiv"],
    )


class ArxivProvider(BaseResearchProvider):
    name = "arxiv"
    capabilities = frozenset({"search", "details", "full_text"})

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research.arxiv")

    def _get_user_agent(self) -> str:
        return get_config().http.user_agent

    async def _request_atom(
        self, path: str, params: dict[str, Any] | None = None
    ) -> str:
        cfg = get_config().features.function_calling.scholarly_research
        url = f"{_API_BASE}{path}"
        try:
            response = await self._client.get(
                url,
                params=params,
                headers={"User-Agent": self._get_user_agent()},
                timeout=cfg.timeout_seconds,
            )
            response.raise_for_status()
            return response.text
        except Exception as exc:
            self._logger.warning("arXiv request failed | path=%s | %s", path, exc)
            raise RuntimeError(f"arXiv request failed: {exc}") from exc

    @staticmethod
    def _parse_atom(xml_text: str) -> list[dict[str, Any]]:
        import xml.etree.ElementTree as ET

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ET.fromstring(xml_text)
        entries = []
        for entry_elem in root.findall("atom:entry", ns):
            entry: dict[str, Any] = {}
            id_elem = entry_elem.find("atom:id", ns)
            if id_elem is not None and id_elem.text:
                entry["id"] = id_elem.text
            title_elem = entry_elem.find("atom:title", ns)
            if title_elem is not None and title_elem.text:
                entry["title"] = " ".join(title_elem.text.split())
            summary_elem = entry_elem.find("atom:summary", ns)
            if summary_elem is not None and summary_elem.text:
                entry["summary"] = " ".join(summary_elem.text.split())
            published_elem = entry_elem.find("atom:published", ns)
            if published_elem is not None and published_elem.text:
                entry["published"] = published_elem.text
            author_names = []
            for author_elem in entry_elem.findall("atom:author", ns):
                name_elem = author_elem.find("atom:name", ns)
                if name_elem is not None and name_elem.text:
                    author_names.append(name_elem.text)
            entry["authors"] = ", ".join(author_names)
            doi_elem = entry_elem.find("arxiv:doi", ns)
            if doi_elem is not None and doi_elem.text:
                entry["doi"] = doi_elem.text
            cat_elem = entry_elem.find("arxiv:primary_category", ns)
            if cat_elem is not None:
                entry["primary_category"] = cat_elem.get("term", "")
            entries.append(entry)
        return entries

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
            "search_query": f"all:{quote(query.strip())}",
            "max_results": min(limit, 100),
        }
        if authors:
            author_query = "+AND+".join(
                f"au:{quote(author.strip())}" for author in authors if author.strip()
            )
            if author_query:
                params["search_query"] += f"+AND+({author_query})"

        started = time.monotonic()
        try:
            xml_text = await self._request_atom("/query", params=params)
        except RuntimeError:
            return SearchScholarlyResult(query=query, per_page=limit, source=self.name)

        entries = self._parse_atom(xml_text)
        works = [_build_work_from_arxiv(entry) for entry in entries]
        elapsed_ms = (time.monotonic() - started) * 1000
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=len(works),
            per_page=limit,
            source=self.name,
            search_time_ms=elapsed_ms,
        )

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        identifier = work_id.strip()
        if identifier.startswith("10."):
            params = {"id_list": identifier}
            path = "/query"
        else:
            arxiv_id = identifier.replace("arxiv:", "").replace("arXiv:", "")
            params = {"id_list": arxiv_id}
            path = "/query"
        try:
            xml_text = await self._request_atom(path, params=params)
        except RuntimeError:
            return None
        entries = self._parse_atom(xml_text)
        if not entries:
            return None
        return WorkDetailResult(
            work=_build_work_from_arxiv(entries[0]), source=self.name
        )

    async def find_related(
        self,
        seed_id: str | None = None,
        relation_type: str = "similar",
        **kwargs: Any,
    ) -> RelatedWorksResult:
        return RelatedWorksResult(seed_id=seed_id or "", relation_type=relation_type)

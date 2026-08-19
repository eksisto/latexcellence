"""学术研究工具协调器 — Provider 注册、联邦搜索与 ToolRegistry 集成。"""

import asyncio
from typing import Any

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.tool_registry import get_tool_registry

from ._utils import _dedup_works, _limit_results, _rrf_rank, _sort_works
from .base import BaseResearchProvider
from .models import (
    FullTextResult,
    RelatedWorksResult,
    SearchScholarlyResult,
    WorkDetailResult,
)
from .planner import ScholarlyQueryPlan
from backend.utils.web_search.page_reader import get_structured_page_reader
from .providers.arxiv import ArxivProvider
from .providers.core import COREProvider
from .providers.crossref import CrossrefProvider
from .providers.datacite import DataCiteProvider
from .providers.openalex import OpenAlexProvider
from .providers.pubmed import PubMedProvider
from .providers.semantic import SemanticScholarProvider
from .providers.unpaywall import UnpaywallProvider

_research_http_client: httpx.AsyncClient | None = None
_research_manager: "ResearchManager | None" = None


async def initialize_research_http_client() -> None:
    global _research_http_client
    if _research_http_client is None or _research_http_client.is_closed:
        _research_http_client = httpx.AsyncClient(follow_redirects=True)


async def close_research_http_client() -> None:
    global _research_http_client
    if _research_http_client is not None:
        await _research_http_client.aclose()
        _research_http_client = None


class ResearchManager:
    """学术研究协调器 — 注册 Provider、联邦搜索、去重合并。"""

    def __init__(self) -> None:
        self._providers: dict[str, BaseResearchProvider] = {}
        self._logger = get_logger("scholarly_research")

    def register(self, provider: BaseResearchProvider) -> None:
        self._providers[provider.name] = provider
        self._logger.info("Research provider registered: %s", provider.name)

    def _enabled_providers(self) -> list[BaseResearchProvider]:
        cfg = get_config().features.function_calling.scholarly_research
        provider_cfg_map = {
            "openalex": cfg.providers.openalex,
            "semantic_scholar": cfg.providers.semantic_scholar,
            "crossref": cfg.providers.crossref,
            "arxiv": cfg.providers.arxiv,
            "core": cfg.providers.core,
            "unpaywall": cfg.providers.unpaywall,
            "pubmed": cfg.providers.pubmed,
            "datacite": cfg.providers.datacite,
        }
        result: list[BaseResearchProvider] = []
        for name, provider in self._providers.items():
            pcfg = provider_cfg_map.get(name)
            if pcfg is None:
                result.append(provider)
            elif pcfg.enabled:
                result.append(provider)
        return result

    def _active(self) -> list[BaseResearchProvider]:
        providers = self._enabled_providers()
        if not providers:
            raise RuntimeError("No scholarly research providers enabled")
        return providers

    async def _federated_search(
        self, query: str, max_results: int | None = None, **filters: Any
    ) -> SearchScholarlyResult:
        providers = self._active()
        limit = _limit_results(max_results, 20)
        tasks = []
        searchable = [provider for provider in providers if provider.supports("search")]
        for provider in searchable:
            try:
                tasks.append(provider.search(query, max_results=limit, **filters))
            except Exception:
                pass
        if not tasks:
            return SearchScholarlyResult(query=query, per_page=limit, source="none")

        responses = await asyncio.gather(*tasks, return_exceptions=True)
        all_works: list[ScholarlyWork] = []
        provider_works: list[list[ScholarlyWork]] = []
        total = 0
        search_time_ms = 0.0
        for resp in responses:
            if isinstance(resp, Exception):
                continue
            all_works.extend(resp.results)
            provider_works.append(resp.results)
            total += resp.total_estimated
            search_time_ms = max(search_time_ms, resp.search_time_ms)

        ranking_mode = (
            get_config().features.function_calling.scholarly_research.ranking_mode
        )
        merged = _dedup_works(
            _rrf_rank(provider_works) if ranking_mode == "rrf" else all_works
        )
        sort_by = filters.get("sort_by", "relevance")
        merged = _sort_works(merged, sort_by)[:limit]

        return SearchScholarlyResult(
            query=query,
            results=merged,
            total_estimated=total,
            per_page=limit,
            source=",".join(p.name for p in providers),
            search_time_ms=search_time_ms,
        )

    async def search_works(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        authors: list[str] | None = None,
        work_types: list[str] | None = None,
        sort_by: str = "relevance",
        max_results: int | None = None,
        query_plan: ScholarlyQueryPlan | None = None,
    ) -> SearchScholarlyResult:
        unsupported_filters: list[str] = []
        if query_plan is not None:
            query = " ".join(query_plan.topic_terms)
            unsupported_filters = sorted(
                {
                    item
                    for provider in self._active()
                    for item in query_plan.provider_filters(provider.name)[1]
                }
            )
            year_from, year_to, authors, work_types = (
                query_plan.year_from,
                query_plan.year_to,
                query_plan.authors,
                query_plan.work_types,
            )
        result = await self._federated_search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            authors=authors,
            work_types=work_types,
            sort_by=sort_by,
            max_results=max_results,
        )
        result.unsupported_filters = unsupported_filters
        return result

    async def get_details(self, work_id: str) -> WorkDetailResult | None:
        for provider in self._active():
            try:
                detail = await provider.get_details(work_id)
                if detail is not None:
                    return detail
            except Exception:
                continue
        return None

    async def find_related_works(
        self,
        work_id: str,
        relation_type: str,
        max_results: int | None = None,
        sort_by: str = "citation_count",
    ) -> RelatedWorksResult:
        last_error: Exception | None = None
        relation_capability = {
            "references": "references",
            "cited_by": "citations",
            "similar": "similar",
        }.get(relation_type, relation_type)
        results: list[RelatedWorksResult] = []
        ranking_mode = (
            get_config().features.function_calling.scholarly_research.ranking_mode
        )
        for provider in self._active():
            if not provider.supports(relation_capability):
                continue
            try:
                result = await provider.find_related_works(
                    work_id=work_id,
                    relation_type=relation_type,
                    max_results=max_results,
                    sort_by=sort_by,
                )
                if result is not None:
                    if ranking_mode == "legacy" and result.works:
                        return result
                    results.append(result)
            except NotImplementedError:
                continue
            except Exception as exc:
                last_error = exc
                continue
        if results:
            works = _dedup_works(_rrf_rank([item.works for item in results]))
            return RelatedWorksResult(
                seed_id=work_id,
                relation_type=relation_type,
                works=works[: _limit_results(max_results, 20)],
                total=sum(item.total for item in results),
            )
        if last_error:
            raise last_error
        return RelatedWorksResult(seed_id=work_id, relation_type=relation_type)

    async def get_full_text(
        self,
        work_id: str,
        requested_sections: list[str] | None = None,
    ) -> FullTextResult:
        detail = await self.get_details(work_id)
        if detail is None:
            raise ValueError(f"Scholarly work not found: {work_id}")
        work = detail.work
        requested = set(requested_sections or ["abstract"])
        sections: dict[str, str] = {}
        if "abstract" in requested and work.abstract:
            sections["abstract"] = work.abstract
        unsupported = sorted(requested - {"abstract"})
        note = "Provides abstract and OA location."
        oa_url = work.oa_url
        if not oa_url and work.doi:
            for provider in self._enabled_providers():
                finder = getattr(provider, "find_oa_url", None)
                if finder:
                    try:
                        url = await finder(work.doi)
                        if url:
                            oa_url = url
                            break
                    except Exception:
                        continue
        full_text_enabled = (
            get_config().features.function_calling.scholarly_research.full_text_enabled
        )
        if not full_text_enabled:
            if unsupported:
                note += f" Sections not available: {', '.join(unsupported)}."
            return FullTextResult(
                work_id=work.openalex_id or work.doi,
                sections=sections,
                oa_url=oa_url,
                note=note,
            )

        note = ""
        source_kind = "metadata"
        confidence = 0.0
        available = list(sections)
        if oa_url and unsupported:
            try:
                reader = await get_structured_page_reader()
                document, _, _, failure, _ = await reader.read(
                    oa_url, mode="full", max_chars=30000
                )
                if document is not None:
                    for section in document.outline:
                        text = "\n\n".join(
                            block.text
                            for block in document.blocks
                            if block.block_id in section.block_ids
                        )
                        if text:
                            sections.setdefault(section.title, text)
                    available = list(sections)
                    source_kind = "structured_full_text"
                    confidence = float(document.quality.get("overall", 0.0))
                if failure:
                    note = failure.message
            except Exception as exc:
                self._logger.info("Scholarly full-text extraction failed: %s", exc)
        missing = sorted(requested - set(available))
        if missing:
            note = (
                note + " " if note else ""
            ) + f"Sections unavailable: {', '.join(missing)}."
        return FullTextResult(
            work_id=work.openalex_id or work.doi,
            sections=sections,
            oa_url=oa_url,
            note=note,
            available_sections=available,
            missing_sections=missing,
            extraction_confidence=confidence,
            source_kind=source_kind,
        )

    async def search_resources(
        self,
        query: str,
        resource_type: str = "dataset",
        max_results: int | None = None,
    ) -> SearchScholarlyResult:
        if resource_type != "dataset":
            raise ValueError("resource_type must be dataset")
        return await self._federated_search(
            query=query, work_types=["dataset"], max_results=max_results or 10
        )

    @staticmethod
    def get_tools_schema() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_scholarly_works",
                    "description": (
                        "Search for papers across multiple academic data sources. Support searching by topic, author, year, document type, and other criteria, "
                        "and return structured records containing stable identifiers such as DOI and OpenAlex ID. "
                        "Results may be truncated: the output ends with a status block (truncated: yes/no). "
                        "When truncated, refine the query or increase max_results to fetch more."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search keyword",
                            },
                            "year_from": {
                                "type": "integer",
                                "description": "Start year",
                            },
                            "year_to": {"type": "integer", "description": "End year"},
                            "authors": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Author names",
                            },
                            "work_types": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "review / journal_article / conference_paper / "
                                    "preprint / book_chapter / dataset"
                                ),
                            },
                            "sort_by": {
                                "type": "string",
                                "enum": [
                                    "relevance",
                                    "citation_count",
                                    "publication_date",
                                ],
                                "default": "relevance",
                            },
                            "max_results": {"type": "integer", "default": 20},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_work_details",
                    "description": (
                        "Get the complete detailed information of a paper: abstract, authors, affiliations, journal, DOI, citation count, "
                        "topics, and open access information. Support multiple data sources."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID or DOI for the paper",
                            }
                        },
                        "required": ["work_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_related_works",
                    "description": (
                        "Find related works based on a seed paper: references, papers that cite the seed paper, or similar papers."
                        "Utilize citation graphs from multiple data sources. "
                        "Results may be truncated: the output ends with a status block (truncated: yes/no). "
                        "When truncated, increase max_results or narrow the query."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID or DOI",
                            },
                            "relation_type": {
                                "type": "string",
                                "enum": ["references", "cited_by", "similar"],
                            },
                            "max_results": {"type": "integer", "default": 20},
                            "sort_by": {
                                "type": "string",
                                "enum": [
                                    "citation_count",
                                    "publication_date",
                                    "relevance",
                                ],
                                "default": "citation_count",
                            },
                        },
                        "required": ["work_id", "relation_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_full_text_or_sections",
                    "description": (
                        "Get the abstract and open access full-text location information of a paper. Provide the most comprehensive OA links across data sources."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID or DOI",
                            },
                            "requested_sections": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "abstract / limitations / methods / results / "
                                    "discussion / conclusion / future_work"
                                ),
                            },
                        },
                        "required": ["work_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_research_data_resources",
                    "description": (
                        "Search for datasets and research data related to a research topic. Search across multiple data sources. "
                        "Results may be truncated: the output ends with a status block (truncated: yes/no). "
                        "When truncated, refine the query or increase max_results."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Research topic",
                            },
                            "resource_type": {
                                "type": "string",
                                "enum": ["dataset"],
                                "default": "dataset",
                            },
                            "max_results": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]


async def _scholarly_research_executor(
    tool_name: str, arguments: dict[str, Any]
) -> Any:
    manager = get_research_manager()
    tool_arguments = dict(arguments)
    tool_arguments.pop("_workspace_id", None)
    if tool_name == "search_scholarly_works":
        return await manager.search_works(**tool_arguments)
    if tool_name == "get_work_details":
        result = await manager.get_details(**tool_arguments)
        if result is None:
            raise ValueError(f"Scholarly work not found: {tool_arguments['work_id']}")
        return result
    if tool_name == "find_related_works":
        return await manager.find_related_works(**tool_arguments)
    if tool_name == "get_full_text_or_sections":
        return await manager.get_full_text(**tool_arguments)
    if tool_name == "search_research_data_resources":
        return await manager.search_resources(**tool_arguments)
    raise ValueError(f"Unknown research tool: {tool_name}")


def reset_research_manager() -> None:
    global _research_manager
    _research_manager = None


def get_research_manager() -> ResearchManager:
    global _research_manager
    if _research_manager is None:
        _research_manager = ResearchManager()
        if _research_http_client is None or _research_http_client.is_closed:
            raise RuntimeError(
                "Scholarly research HTTP client is not initialized. "
                "Call initialize_research_http_client() before get_research_manager()."
            )
        _research_manager.register(OpenAlexProvider(_research_http_client))

        research_cfg = get_config().features.function_calling.scholarly_research
        provider_registry = [
            (SemanticScholarProvider, research_cfg.providers.semantic_scholar),
            (CrossrefProvider, research_cfg.providers.crossref),
            (ArxivProvider, research_cfg.providers.arxiv),
            (COREProvider, research_cfg.providers.core),
            (UnpaywallProvider, research_cfg.providers.unpaywall),
            (PubMedProvider, research_cfg.providers.pubmed),
            (DataCiteProvider, research_cfg.providers.datacite),
        ]
        for provider_cls, provider_cfg in provider_registry:
            if provider_cfg.enabled:
                _research_manager.register(provider_cls(_research_http_client))

        registry = get_tool_registry()
        for schema in _research_manager.get_tools_schema():
            registry.register(
                name=schema["function"]["name"],
                schema=schema,
                executor=_scholarly_research_executor,
                config_path="features.function_calling.scholarly_research.enabled",
            )
    return _research_manager

"""OpenAlex 集成、学术研究工具 schema 与 ToolRegistry 执行器。"""

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.tool_registry import get_tool_registry

from .base import BaseResearchProvider
from .models import (
    Author,
    Concept,
    FullTextResult,
    RelatedWorksResult,
    ScholarlyWork,
    SearchScholarlyResult,
    WorkDetailResult,
)

_API_BASE = "https://api.openalex.org"
_MAX_RELATED_CANDIDATES = 500
_OPENALEX_ID_PATTERN = re.compile(r"^[Ww]\d+$")
_DOI_PREFIX_PATTERN = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)

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

_research_http_client: httpx.AsyncClient | None = None
_research_manager: "ResearchManager | None" = None


def _get_user_agent() -> str:
    return get_config().http.user_agent


def _short_openalex_id(value: str | None) -> str:
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


def _normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    return _DOI_PREFIX_PATTERN.sub("", value.strip()).lower()


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
    """将 OpenAlex 的词位置倒排索引重建为摘要。"""
    if not inverted_index:
        return ""
    positioned_words = [
        (position, word)
        for word, positions in inverted_index.items()
        for position in positions
    ]
    positioned_words.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned_words)


def _build_work_from_openalex(data: dict[str, Any]) -> ScholarlyWork:
    """将 OpenAlex work 响应解析为统一模型。"""
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


async def initialize_research_http_client() -> None:
    """在 FastAPI lifespan 中初始化共享 OpenAlex HTTP 客户端。"""
    global _research_http_client
    if _research_http_client is None or _research_http_client.is_closed:
        _research_http_client = httpx.AsyncClient(follow_redirects=True)


async def close_research_http_client() -> None:
    """关闭 lifespan 管理的共享 HTTP 客户端。"""
    global _research_http_client
    if _research_http_client is not None:
        await _research_http_client.aclose()
        _research_http_client = None


class ResearchManager(BaseResearchProvider):
    """OpenAlex 学术检索、作品详情与引文追踪管理器。"""

    name = "openalex"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._logger = get_logger("scholarly_research")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if _research_http_client is None or _research_http_client.is_closed:
            raise RuntimeError("Scholarly research HTTP client is not initialized")
        return _research_http_client

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
                response = await self._get_client().get(
                    url,
                    params=request_params,
                    headers={"User-Agent": _get_user_agent()},
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

    @staticmethod
    def _limit_results(requested: int | None, default: int) -> int:
        configured = (
            get_config().features.function_calling.scholarly_research.max_results
        )
        return max(1, min(requested or default, configured, 100))

    @staticmethod
    def _sort_works(works: list[ScholarlyWork], sort_by: str) -> list[ScholarlyWork]:
        if sort_by == "publication_date":
            return sorted(works, key=lambda work: work.year, reverse=True)
        if sort_by == "citation_count":
            return sorted(works, key=lambda work: work.citation_count, reverse=True)
        return works

    async def search(self, query: str, **filters: Any) -> SearchScholarlyResult:
        return await self.search_works(query=query, **filters)

    async def _resolve_author_ids(self, authors: list[str]) -> list[str]:
        """将作者姓名解析为 OpenAlex Author ID。"""
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
        """使用 OpenAlex 批量 ID filter 获取作品。"""
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
            candidate_ids[index : index + 100]
            for index in range(0, len(candidate_ids), 100)
        ]
        fetched_batches = await asyncio.gather(
            *(fetch_batch(batch) for batch in batches)
        )
        works = self._dedup_works(
            [work for batch_works in fetched_batches for work in batch_works]
        )
        return self._sort_works(works, sort_by)[:max_results], len(all_unique_ids)

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
        """按主题和元数据筛选条件检索 OpenAlex works。"""
        if not query.strip():
            raise ValueError("query must not be empty")
        if year_from and year_to and year_from > year_to:
            raise ValueError("year_from must be less than or equal to year_to")
        limit = self._limit_results(max_results, 20)
        openalex_filters: list[str] = []
        if year_from:
            openalex_filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            openalex_filters.append(f"to_publication_date:{year_to}-12-31")
        if authors:
            author_ids = await self._resolve_author_ids(authors)
            if not author_ids:
                return SearchScholarlyResult(query=query, per_page=limit)
            openalex_filters.append(f"authorships.author.id:{'|'.join(author_ids)}")
        if work_types:
            mapped_types = [
                _WORK_TYPE_TO_OPENALEX.get(work_type, work_type)
                for work_type in work_types
                if work_type
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
        works = self._dedup_works(
            [_build_work_from_openalex(item) for item in payload.get("results") or []]
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        total = int((payload.get("meta") or {}).get("count") or len(works))
        self._logger.info(
            "Scholarly search completed | query=%s | results=%s | time=%.0fms",
            query[:80],
            total,
            elapsed_ms,
        )
        return SearchScholarlyResult(
            query=query,
            results=works,
            total_estimated=total,
            per_page=limit,
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
        return WorkDetailResult(work=_build_work_from_openalex(payload))

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
        """追踪参考文献、被引作品或主题相近作品。"""
        if relation_type not in {"references", "cited_by", "similar"}:
            raise ValueError("relation_type must be references, cited_by, or similar")
        limit = self._limit_results(max_results, 20)
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
        works = [work for work in works if work.openalex_id != seed_openalex_id]
        works = self._dedup_works(works)[:limit]
        total = int((payload.get("meta") or {}).get("count") or len(works))
        return RelatedWorksResult(
            seed_id=seed_openalex_id,
            relation_type=relation_type,
            works=works,
            total=max(total, len(works)),
        )

    async def get_full_text(
        self,
        work_id: str,
        requested_sections: list[str] | None = None,
    ) -> FullTextResult:
        """Phase 1：返回摘要及开放获取链接，不抓取第三方全文。"""
        detail = await self.get_details(work_id)
        if detail is None:
            raise ValueError(f"Scholarly work not found: {work_id}")
        work = detail.work
        requested = set(requested_sections or ["abstract"])
        sections: dict[str, str] = {}
        if "abstract" in requested and work.abstract:
            sections["abstract"] = work.abstract
        unsupported = sorted(requested - {"abstract"})
        note = "Phase 1 provides the OpenAlex abstract and OA location only."
        if unsupported:
            note += f" Sections not available: {', '.join(unsupported)}."
        return FullTextResult(
            work_id=work.openalex_id or work.doi,
            sections=sections,
            oa_url=work.oa_url,
            note=note,
        )

    async def search_resources(
        self,
        query: str,
        resource_type: str = "dataset",
        max_results: int | None = None,
    ) -> SearchScholarlyResult:
        """Phase 1：在 OpenAlex 中检索 dataset 类型作品。"""
        if resource_type != "dataset":
            raise ValueError("resource_type must be dataset")
        result = await self.search_works(
            query=query,
            work_types=["dataset"],
            max_results=max_results or 10,
        )
        return result

    @staticmethod
    def _dedup_works(works: list[ScholarlyWork]) -> list[ScholarlyWork]:
        """按 DOI、OpenAlex ID、arXiv ID，最后按标题和年份去重。"""
        seen: dict[str, ScholarlyWork] = {}
        order: list[str] = []
        for work in works:
            if work.doi:
                key = f"doi:{_normalize_doi(work.doi)}"
            elif work.openalex_id:
                key = f"openalex:{work.openalex_id.upper()}"
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

    @staticmethod
    def get_tools_schema() -> list[dict[str, Any]]:
        """返回写作助手可注入的学术研究 Function Calling schema。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_scholarly_works",
                    "description": (
                        "按主题、作者、年份、文献类型等条件检索学术论文。返回含 DOI、"
                        "OpenAlex ID 等稳定标识符的结构化记录。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                            "year_from": {"type": "integer", "description": "起始年份"},
                            "year_to": {"type": "integer", "description": "结束年份"},
                            "authors": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "作者姓名",
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
                        "获取论文摘要、作者、机构、期刊、DOI、被引数、主题和开放获取信息。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID 或 DOI",
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
                        "基于种子论文查找参考文献、引用该论文的后续工作或主题相似论文。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID 或 DOI",
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
                        "获取开放获取论文的摘要和全文定位信息。当前阶段返回摘要与 OA 链接。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "work_id": {
                                "type": "string",
                                "description": "OpenAlex ID 或 DOI",
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
                        "检索与研究主题相关的数据资源。当前阶段在 OpenAlex 中搜索 dataset。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "研究主题"},
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
    """学术研究工具执行器，供 ToolRegistry 分发。"""
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
    """重置 ResearchManager 单例，供测试与配置热重载使用。"""
    global _research_manager
    _research_manager = None


def get_research_manager() -> ResearchManager:
    """获取全局 ResearchManager，并按需注册全部研究工具。"""
    global _research_manager
    if _research_manager is None:
        _research_manager = ResearchManager()
        registry = get_tool_registry()
        for schema in _research_manager.get_tools_schema():
            registry.register(
                name=schema["function"]["name"],
                schema=schema,
                executor=_scholarly_research_executor,
                config_path="features.function_calling.scholarly_research.enabled",
            )
    return _research_manager

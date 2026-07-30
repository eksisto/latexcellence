"""测试 OpenAlex 学术研究工具的数据映射、查询与注册。"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.core.config import get_config
from backend.utils.scholarly_research import manager as manager_module
from backend.utils.scholarly_research._utils import _dedup_works
from backend.utils.scholarly_research.manager import (
    ResearchManager,
    _scholarly_research_executor,
    reset_research_manager,
)
from backend.utils.scholarly_research.models import ScholarlyWork
from backend.utils.scholarly_research.providers.openalex import (
    OpenAlexProvider,
    _build_work_from_openalex,
    _reconstruct_abstract,
)


def _openalex_work(
    work_id: str = "W123",
    *,
    title: str = "A useful paper",
    work_type: str = "article",
) -> dict:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": "https://doi.org/10.1000/TEST",
        "display_name": title,
        "publication_year": 2025,
        "type": work_type,
        "authorships": [
            {
                "author": {
                    "display_name": "Ada Lovelace",
                    "orcid": "https://orcid.org/0000-0000-0000-0001",
                },
                "institutions": [{"display_name": "Analytical Engine Lab"}],
            }
        ],
        "primary_location": {
            "source": {
                "display_name": "Journal of Tests",
                "host_organization_name": "Test Publisher",
                "type": "journal",
            },
            "landing_page_url": "https://example.test/paper",
        },
        "best_oa_location": {"pdf_url": "https://example.test/paper.pdf"},
        "open_access": {"is_oa": True, "oa_status": "gold"},
        "biblio": {
            "volume": "3",
            "issue": "2",
            "first_page": "10",
            "last_page": "18",
        },
        "abstract_inverted_index": {"World": [1], "Hello": [0]},
        "keywords": [{"display_name": "testing"}],
        "topics": [
            {
                "id": "https://openalex.org/T100",
                "display_name": "Software testing",
                "score": 0.95,
            }
        ],
        "cited_by_count": 42,
        "referenced_works_count": 7,
        "citation_normalized_percentile": {"value": 0.9},
        "referenced_works": ["https://openalex.org/W456"],
        "related_works": ["https://openalex.org/W789"],
        "ids": {"openalex": f"https://openalex.org/{work_id}"},
    }


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_research_manager()
    yield
    reset_research_manager()


def test_reconstruct_abstract_orders_word_positions():
    assert _reconstruct_abstract({"second": [1], "first": [0]}) == "first second"
    assert _reconstruct_abstract(None) == ""


def test_build_work_from_openalex_normalizes_metadata():
    work = _build_work_from_openalex(_openalex_work())

    assert work.openalex_id == "W123"
    assert work.doi == "10.1000/test"
    assert work.work_type == "journal_article"
    assert work.abstract == "Hello World"
    assert work.authors[0].institution == "Analytical Engine Lab"
    assert work.authors[0].orcid == "0000-0000-0000-0001"
    assert work.journal == "Journal of Tests"
    assert work.pages == "10-18"
    assert work.oa_url == "https://example.test/paper.pdf"
    assert work.concepts[0].id == "T100"


def test_dedup_prefers_published_version_and_merges_sources():
    preprint = ScholarlyWork(
        title="Paper preprint",
        doi="10.1/shared",
        work_type="preprint",
        source_records=["openalex"],
    )
    article = ScholarlyWork(
        title="Paper article",
        doi="https://doi.org/10.1/SHARED",
        work_type="journal_article",
        source_records=["another-source"],
    )

    result = _dedup_works([preprint, article])

    assert len(result) == 1
    assert result[0].title == "Paper article"
    assert result[0].source_records == ["openalex", "another-source"]


def test_get_tools_schema_has_expected_names():
    names = [
        schema["function"]["name"] for schema in ResearchManager.get_tools_schema()
    ]
    assert names == [
        "search_scholarly_works",
        "get_work_details",
        "find_related_works",
        "get_full_text_or_sections",
        "search_research_data_resources",
    ]


@pytest.mark.asyncio
async def test_search_builds_openalex_filters(monkeypatch):
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={"meta": {"count": 1}, "results": [_openalex_work()]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        get_config().features.function_calling.scholarly_research,
        "max_results",
        20,
    )
    try:
        result = await OpenAlexProvider(client).search_works(
            query="program synthesis",
            year_from=2022,
            year_to=2026,
            work_types=["review", "journal_article"],
            max_results=8,
        )
    finally:
        await client.aclose()

    assert result.total_estimated == 1
    assert result.results[0].title == "A useful paper"
    assert captured_request is not None
    params = captured_request.url.params
    assert params["search"] == "program synthesis"
    assert params["per-page"] == "8"
    assert "from_publication_date:2022-01-01" in params["filter"]
    assert "type:review|article" in params["filter"]


@pytest.mark.asyncio
async def test_search_resolves_author_name_to_id():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/authors":
            return httpx.Response(
                200,
                json={
                    "meta": {"count": 1},
                    "results": [{"id": "https://openalex.org/A123"}],
                },
            )
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await OpenAlexProvider(client).search_works(
            query="analytical engines", authors=["Ada Lovelace"]
        )
    finally:
        await client.aclose()

    assert requests[0].url.path == "/authors"
    assert requests[0].url.params["search"] == "Ada Lovelace"
    assert requests[1].url.params["filter"] == "authorships.author.id:A123"


@pytest.mark.asyncio
async def test_search_sends_configured_api_key(monkeypatch):
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    research_cfg = get_config().features.function_calling.scholarly_research
    monkeypatch.setattr(research_cfg.providers.openalex, "api_key", "test-openalex-key")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await OpenAlexProvider(client).search_works(query="latex")
    finally:
        await client.aclose()

    assert captured_request is not None
    assert captured_request.url.params["api_key"] == "test-openalex-key"


@pytest.mark.asyncio
async def test_find_cited_by_uses_cites_filter():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/works/W123":
            return httpx.Response(200, json=_openalex_work())
        return httpx.Response(
            200,
            json={
                "meta": {"count": 1},
                "results": [_openalex_work("W999", title="Citing paper")],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await OpenAlexProvider(client).find_related_works(
            work_id="W123", relation_type="cited_by", max_results=5
        )
    finally:
        await client.aclose()

    assert result.works[0].title == "Citing paper"
    assert requests[1].url.params["filter"] == "cites:W123"


@pytest.mark.asyncio
async def test_find_similar_uses_related_works_batch_filter():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/works/W123":
            return httpx.Response(200, json=_openalex_work())
        return httpx.Response(
            200,
            json={
                "meta": {"count": 1},
                "results": [_openalex_work("W789", title="Related paper")],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await OpenAlexProvider(client).find_related_works(
            work_id="W123", relation_type="similar", max_results=5
        )
    finally:
        await client.aclose()

    assert result.works[0].title == "Related paper"
    assert requests[1].url.params["filter"] == "openalex_id:W789"


@pytest.mark.asyncio
async def test_closed_work_does_not_expose_publisher_url_as_oa():
    work_data = _openalex_work()
    work_data["open_access"] = {"is_oa": False, "oa_status": "closed"}
    work_data["best_oa_location"] = None
    work = _build_work_from_openalex(work_data)

    assert work.is_oa is False
    assert work.oa_url == ""


@pytest.mark.asyncio
async def test_get_full_text_returns_abstract_and_oa_url():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openalex_work())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = ResearchManager()
    manager.register(OpenAlexProvider(client))
    try:
        result = await manager.get_full_text(
            "10.1000/test", requested_sections=["abstract", "methods"]
        )
    finally:
        await client.aclose()

    assert result.sections == {"abstract": "Hello World"}
    assert result.oa_url.endswith("paper.pdf")
    assert "methods" in result.note


@pytest.mark.asyncio
async def test_search_resources_rejects_unsupported_type():
    with pytest.raises(ValueError, match="resource_type must be dataset"):
        await ResearchManager().search_resources("benchmark", resource_type="code")


@pytest.mark.asyncio
async def test_executor_removes_workspace_context(monkeypatch):
    manager = ResearchManager()
    manager.search_works = AsyncMock(return_value="ok")
    monkeypatch.setattr(
        "backend.utils.scholarly_research.manager.get_research_manager",
        lambda: manager,
    )

    result = await _scholarly_research_executor(
        "search_scholarly_works",
        {"query": "test", "_workspace_id": "ignored"},
    )

    assert result == "ok"
    manager.search_works.assert_awaited_once_with(query="test")


def test_result_format_includes_stable_identifiers():
    from backend.utils.scholarly_research.models import SearchScholarlyResult

    result = SearchScholarlyResult(
        query="test",
        results=[_build_work_from_openalex(_openalex_work())],
        total_estimated=1,
    )
    formatted = result.format_for_llm()

    assert "OpenAlex ID: W123" in formatted
    assert "DOI: 10.1000/test" in formatted
    assert json.dumps(result.model_dump())


@pytest.mark.asyncio
async def test_shared_http_client_lifecycle():
    await manager_module.close_research_http_client()
    assert manager_module._research_http_client is None

    await manager_module.initialize_research_http_client()
    client = manager_module._research_http_client
    assert client is not None
    assert not client.is_closed

    await manager_module.close_research_http_client()
    assert client.is_closed
    assert manager_module._research_http_client is None

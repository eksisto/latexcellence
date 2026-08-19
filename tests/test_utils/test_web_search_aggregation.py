"""阶段 1：通用搜索聚合、排序与查询规划测试。"""

import asyncio
import json

import pytest

from backend.core.config import get_config
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.manager import SearchManager
from backend.utils.web_search.models import SearchResponse, SearchResult
from backend.utils.web_search.planner import SearchQueryPlanner
from backend.utils.web_search.ranking import rank_responses
from backend.utils.web_search.url_utils import canonicalize_url, classify_source


class StubEngine(BaseSearchEngine):
    def __init__(
        self,
        name: str,
        results: list[SearchResult] | None = None,
        *,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.results = results or []
        self.delay = delay
        self.error = error
        self.calls = 0

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SearchResponse(
            query=query,
            engine=self.name,
            results=self.results[:max_results],
            total_results=len(self.results),
        )

    async def health_check(self) -> bool:
        return True


def _result(title: str, url: str, snippet: str = "summary") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


def test_canonicalize_url_removes_tracking_and_rejects_unsafe_targets() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM:443/docs/?b=2&utm_source=x&a=1#part")
        == "https://example.com/docs?a=1&b=2"
    )
    assert canonicalize_url("http://127.0.0.1/admin") is None
    assert canonicalize_url("http://[::1]/admin") is None
    assert canonicalize_url("http://2130706433/admin") is None
    assert canonicalize_url("http://0x7f000001/admin") is None
    assert canonicalize_url("file:///etc/passwd") is None
    assert canonicalize_url("https://user:pass@example.com/") is None
    assert classify_source("https://docs.python.org/3/library/") == "official_docs"
    assert classify_source("https://example.gov/report") == "government"


def test_rrf_deduplicates_and_applies_domain_diversity() -> None:
    first = SearchResponse(
        query="q",
        engine="first",
        results=[
            _result("Shared", "https://example.com/a?utm_source=one"),
            _result("Other", "https://example.com/b", "different text"),
            _result("Government", "https://agency.gov/report", "official report"),
        ],
    )
    second = SearchResponse(
        query="q",
        engine="second",
        results=[
            _result("Shared", "https://example.com/a"),
            _result("Independent", "https://independent.test/item", "independent"),
        ],
    )

    candidates, valid_count = rank_responses(
        [("first:0", first), ("second:0", second)],
        mode="rrf",
        domain_result_cap=1,
        source_type_boosts={"government": 0.02},
    )

    assert valid_count == 5
    assert [c.canonical_url for c in candidates].count("https://example.com/a") == 1
    assert (
        sum(c.canonical_url.startswith("https://example.com/") for c in candidates) == 1
    )
    shared = next(c for c in candidates if c.canonical_url == "https://example.com/a")
    assert shared.transport_sources == ["first", "second"]
    assert shared.score_components["consensus"] > 0


@pytest.mark.asyncio
async def test_aggregate_returns_deterministic_partial_results_on_timeout(
    monkeypatch,
) -> None:
    cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(cfg, "strategy", "aggregate")
    monkeypatch.setattr(cfg, "per_engine_timeout_seconds", 0.02)
    monkeypatch.setattr(cfg, "overall_timeout_seconds", 0.1)
    monkeypatch.setattr(cfg, "domain_result_cap", 3)

    manager = SearchManager()
    manager.register_engine(
        StubEngine("fast", [_result("Fast", "https://fast.test/result")]),
        primary=True,
    )
    manager.register_engine(
        StubEngine(
            "slow",
            [_result("Slow", "https://slow.test/result")],
            delay=0.2,
        )
    )

    response = await manager.search("query", max_results=10)

    assert [result.title for result in response.results] == ["Fast"]
    assert response.strategy == "aggregate"
    assert response.partial is True
    assert [(s.engine, s.status) for s in response.engine_statuses] == [
        ("fast", "ok"),
        ("slow", "timeout"),
    ]
    assert response.search_time_ms < 150


@pytest.mark.asyncio
async def test_aggregate_opens_circuit_after_repeated_failures(monkeypatch) -> None:
    cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(cfg, "strategy", "aggregate")
    failing = StubEngine("failing", error=RuntimeError("unavailable"))
    manager = SearchManager()
    manager.register_engine(failing, primary=True)

    for _ in range(3):
        response = await manager.search("query")
        assert response.engine_statuses[0].status == "error"

    response = await manager.search("query")
    assert failing.calls == 3
    assert response.engine_statuses[0].circuit_open is True
    assert response.engine_statuses[0].error_code == "circuit_open"


def test_circuit_settlement_is_deterministic_for_mixed_lane_results() -> None:
    from backend.utils.web_search.models import EngineStatus

    manager = SearchManager()
    manager._engine_failures["mixed"] = 2
    statuses = [
        EngineStatus(
            engine="mixed",
            status="ok",
            elapsed_ms=1,
            result_count=1,
            retryable=False,
        ),
        EngineStatus(
            engine="mixed",
            status="timeout",
            elapsed_ms=2,
            result_count=0,
            retryable=True,
        ),
    ]

    manager._update_circuits(statuses)

    assert manager._engine_failures["mixed"] == 0
    assert not any(status.circuit_open for status in statuses)


@pytest.mark.asyncio
async def test_query_planner_retries_schema_validation(monkeypatch) -> None:
    responses = iter(
        [
            '{"intent":"fact","variants":[]}',
            json.dumps(
                {
                    "original_query": "changed by model",
                    "intent": "official_docs",
                    "languages": ["en"],
                    "variants": ["FastAPI lifespan official docs"],
                }
            ),
        ]
    )

    class FakeClient:
        async def chat(self, messages, **kwargs):
            return next(responses)

    monkeypatch.setattr(
        "backend.utils.web_search.planner.get_llm_client", lambda: FakeClient()
    )
    plan = await SearchQueryPlanner().plan("FastAPI lifespan")

    assert plan.original_query == "FastAPI lifespan"
    assert plan.intent == "official_docs"
    assert plan.variants == ["FastAPI lifespan official docs"]

"""Tavily 搜索引擎单元测试。"""

import json

import httpx
import pytest

from backend.core.config import get_config
from backend.utils.web_search.engines.tavily import TavilyEngine


@pytest.mark.asyncio
async def test_tavily_search_sends_bearer_token_and_maps_results():
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "query": "latex tutorial",
                "results": [
                    {
                        "title": "LaTeX 入门教程",
                        "url": "https://example.com/latex-intro",
                        "content": "LaTeX 是一个高质量的排版系统。",
                        "raw_content": "RAW " + "LaTeX 入门教程。" * 100,
                        "score": 0.92,
                    },
                    {
                        "title": "Overleaf 文档",
                        "url": "https://overleaf.com",
                        "content": "Overleaf 是在线 LaTeX 编辑器。",
                        "score": 0.85,
                    },
                ],
                "answer": "A quick summary of LaTeX tutorials.",
                "response_time": "1.2",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    try:
        result = await engine._do_search(client, "latex tutorial", 5, "tvly-test")
    finally:
        await client.aclose()

    assert captured_request is not None
    assert captured_request.headers["Authorization"] == "Bearer tvly-test"
    assert "X-Tavily-Access-Mode" not in captured_request.headers
    assert captured_request.headers["Content-Type"] == "application/json"
    payload = json.loads(captured_request.content)
    assert payload["query"] == "latex tutorial"
    assert payload["search_depth"] == "advanced"
    assert payload["max_results"] == 5
    assert payload["include_answer"] is True
    assert payload["include_raw_content"] is True

    assert result.engine == "tavily"
    assert result.total_results == 2
    assert result.answer == "A quick summary of LaTeX tutorials."
    assert len(result.results) == 2
    assert result.results[0].title == "LaTeX 入门教程"
    assert result.results[0].url == "https://example.com/latex-intro"
    assert result.results[0].snippet == "LaTeX 是一个高质量的排版系统。"
    assert result.results[0].source == "tavily"
    # raw_content 应填充 full_content（受 deep_read_max_chars 上限约束）
    max_chars = get_config().features.function_calling.web_search.deep_read_max_chars
    assert result.results[0].full_content.startswith("RAW LaTeX")
    assert len(result.results[0].full_content) <= max_chars + 3


@pytest.mark.asyncio
async def test_tavily_search_respects_max_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"Result {i}",
                        "url": f"https://example.com/{i}",
                        "content": f"Content {i}",
                    }
                    for i in range(5)
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    try:
        result = await engine._do_search(client, "python", 2, "tvly-test")
    finally:
        await client.aclose()

    assert len(result.results) == 2
    assert result.total_results == 5


@pytest.mark.asyncio
async def test_tavily_search_http_error_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"error": "Unauthorized"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    try:
        result = await engine._do_search(client, "latex", 5, "invalid-key")
    finally:
        await client.aclose()

    assert result.engine == "tavily"
    assert result.results == []
    assert result.total_results == 0


@pytest.mark.asyncio
async def test_tavily_search_keyless_rate_limit_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"detail": {"error": "Keyless rate limit exceeded"}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    try:
        with pytest.raises(RuntimeError, match="rate limit"):
            await engine._do_search(client, "latex", 5, "")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_tavily_search_keyless_sends_access_mode_header():
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "query": "latex tutorial",
                "results": [
                    {
                        "title": "LaTeX 入门教程",
                        "url": "https://example.com/latex-intro",
                        "content": "LaTeX 是一个高质量的排版系统。",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    try:
        result = await engine._do_search(client, "latex tutorial", 5, "")
    finally:
        await client.aclose()

    assert captured_request is not None
    assert captured_request.headers["X-Tavily-Access-Mode"] == "keyless"
    assert "Authorization" not in captured_request.headers
    assert result.engine == "tavily"
    assert result.total_results == 1
    assert result.results[0].title == "LaTeX 入门教程"


@pytest.mark.asyncio
async def test_tavily_search_without_key_uses_keyless(monkeypatch):
    """空 API Key 时，search() 自动以 keyless 模式发起请求。"""
    tavily_cfg = get_config().features.function_calling.web_search.engines.tavily
    monkeypatch.setattr(tavily_cfg, "api_key", "")

    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Overleaf 文档",
                        "url": "https://overleaf.com",
                        "content": "Overleaf 是在线 LaTeX 编辑器。",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = TavilyEngine()
    monkeypatch.setattr(engine, "_get_client", lambda: client)
    try:
        result = await engine.search("latex tutorial")
    finally:
        await client.aclose()

    assert captured_request is not None
    assert captured_request.headers["X-Tavily-Access-Mode"] == "keyless"
    assert "Authorization" not in captured_request.headers
    assert result.total_results == 1


@pytest.mark.asyncio
async def test_tavily_health_check(monkeypatch):
    """有无 API Key 均视为可用（无 Key 时自动走 keyless）。"""
    tavily_cfg = get_config().features.function_calling.web_search.engines.tavily
    monkeypatch.setattr(tavily_cfg, "api_key", "")
    assert await TavilyEngine().health_check() is True

    monkeypatch.setattr(tavily_cfg, "api_key", "tvly-test")
    assert await TavilyEngine().health_check() is True


@pytest.mark.asyncio
async def test_manager_registers_tavily_with_api_key(monkeypatch):
    from backend.utils.web_search import get_search_manager, reset_search_manager

    web_search_cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(web_search_cfg.engines.tavily, "enabled", True)
    monkeypatch.setattr(web_search_cfg.engines.tavily, "api_key", "tvly-test")
    monkeypatch.setattr(web_search_cfg, "primary_engine", "tavily")

    reset_search_manager()
    try:
        manager = get_search_manager()
        assert manager.get_engine("tavily") is not None
    finally:
        reset_search_manager()


@pytest.mark.asyncio
async def test_manager_registers_tavily_without_api_key(monkeypatch):
    from backend.utils.web_search import get_search_manager, reset_search_manager

    web_search_cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(web_search_cfg.engines.tavily, "enabled", True)
    monkeypatch.setattr(web_search_cfg.engines.tavily, "api_key", "")
    monkeypatch.setattr(web_search_cfg, "primary_engine", "tavily")

    reset_search_manager()
    try:
        manager = get_search_manager()
        assert manager.get_engine("tavily") is not None
    finally:
        reset_search_manager()


@pytest.mark.asyncio
async def test_manager_skips_tavily_when_disabled(monkeypatch):
    from backend.utils.web_search import get_search_manager, reset_search_manager

    web_search_cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(web_search_cfg.engines.duckduckgo, "enabled", True)
    monkeypatch.setattr(web_search_cfg.engines.tavily, "enabled", False)
    monkeypatch.setattr(web_search_cfg.engines.tavily, "api_key", "")
    monkeypatch.setattr(web_search_cfg, "primary_engine", "tavily")

    reset_search_manager()
    try:
        manager = get_search_manager()
        assert manager.get_engine("tavily") is None
        # 主引擎不可用时降级到第一个可用引擎
        assert manager._primary is not None
        assert manager._primary.name == "duckduckgo"
    finally:
        reset_search_manager()

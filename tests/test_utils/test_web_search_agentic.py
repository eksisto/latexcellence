"""web_search agentic 增强（open_page / find_in_page / 证据预算 / 引用校验）单元测试。"""

import pytest

from backend.core.config import get_config
from backend.modules.writing_chat.service import WritingChatService
from backend.utils.web_search import (
    FindInPageResult,
    PageReadResult,
    SearchResponse,
    SearchResult,
    get_search_manager,
    reset_search_manager,
)
from backend.utils.web_search.deep_read import DeepReader
from backend.utils.web_search.manager import (
    _find_in_page_executor,
    _open_page_executor,
)

# ── format_for_llm：untrusted 包装 + token 预算 ────────────────────────────


def _make_response() -> SearchResponse:
    return SearchResponse(
        query="test query",
        answer="Search engine answer.",
        results=[
            SearchResult(
                title="Title A",
                snippet="Snippet A",
                url="https://example.com/a",
                full_content="A" * 8000,
            ),
            SearchResult(
                title="Title B",
                snippet="Snippet B",
                url="https://example.com/b",
                full_content="B" * 8000,
            ),
        ],
        engine="mock",
    )


def test_format_for_llm_wraps_untrusted_data():
    text = _make_response().format_for_llm(max_results=5, max_chars=20000)
    assert "UNTRUSTED DATA" in text
    assert "Search engine answer." in text
    assert "https://example.com/a" in text


def test_format_for_llm_respects_char_budget():
    text = _make_response().format_for_llm(max_results=5, max_chars=3000)
    # 预算含固定开销，正文部分应被截断而不是整体超限过多
    assert len(text) < 6000
    assert "truncated to fit context budget" in text


def test_format_for_llm_no_results_wraps_untrusted():
    text = SearchResponse(query="none", results=[]).format_for_llm()
    assert "UNTRUSTED DATA" in text
    assert "No relevant results" in text


def test_page_read_result_format_wraps_untrusted():
    result = PageReadResult(
        url="https://example.com/page",
        title="Page Title",
        content="some content",
    )
    text = result.format_for_llm()
    assert "UNTRUSTED DATA" in text
    assert "https://example.com/page" in text
    assert "Page Title" in text


def test_find_in_page_result_format():
    result = FindInPageResult(
        url="https://example.com/page",
        query="latex",
        matches=["...a latex example...", "...more latex..."],
    )
    text = result.format_for_llm()
    assert "2 match(es)" in text
    assert "a latex example" in text


# ── search_multi：多查询合并上限与去重 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_search_multi_keeps_merged_cap_and_dedupes(monkeypatch):
    manager = get_search_manager()

    async def fake_search(query: str, max_results: int | None = None):
        base = 1000 + int(query[1]) * 1000
        return SearchResponse(
            query=query,
            results=[
                SearchResult(
                    title=f"{query} #{i}",
                    url=f"https://example.com/{base + i}",
                    snippet=f"snippet {i}",
                )
                for i in range(10)
            ]
            + [
                SearchResult(
                    title="duplicate",
                    url="https://example.com/999",
                    snippet="dup",
                )
            ],
            engine="mock",
        )

    monkeypatch.setattr(manager, "search", fake_search)
    result = await manager.search_multi(
        queries=["q1", "q2", "q3", "q4"], max_results=10
    )
    # 4 条查询 × 10 条唯一结果 + 1 条共享重复，合并上限 = min(10*4, 15) = 15
    assert result.total_results == 41
    assert len(result.results) == 15
    urls = [r.url for r in result.results]
    assert len(set(urls)) == len(urls)


@pytest.mark.asyncio
async def test_search_multi_single_query_keeps_max_results(monkeypatch):
    manager = get_search_manager()

    async def fake_search(query: str, max_results: int | None = None):
        return SearchResponse(
            query=query,
            results=[
                SearchResult(
                    title=f"R{i}",
                    url=f"https://example.com/{i}",
                    snippet="s",
                )
                for i in range(10)
            ],
            engine="mock",
        )

    monkeypatch.setattr(manager, "search", fake_search)
    result = await manager.search_multi(queries=["only"], max_results=10)
    assert len(result.results) == 10


# ── 主引擎缺失自动降级 ─────────────────────────────────────────────────────


def test_primary_engine_falls_back_when_unavailable(monkeypatch):
    cfg = get_config().features.function_calling.web_search
    monkeypatch.setattr(cfg, "primary_engine", "tavily")
    monkeypatch.setattr(cfg.engines.tavily, "enabled", False)
    monkeypatch.setattr(cfg.engines.tavily, "api_key", "")
    monkeypatch.setattr(cfg.engines.duckduckgo, "enabled", True)

    reset_search_manager()
    try:
        manager = get_search_manager()
        assert manager._primary is not None
        assert manager._primary.name == "duckduckgo"
    finally:
        reset_search_manager()


# ── 工具 schema ─────────────────────────────────────────────────────────────


def test_tools_schema_contains_three_tools():
    names = {s["function"]["name"] for s in get_search_manager().get_tools_schema()}
    assert names == {"web_search", "open_page", "find_in_page"}


def test_tools_schema_has_parameter_bounds():
    schemas = {
        s["function"]["name"]: s["function"]["parameters"]
        for s in get_search_manager().get_tools_schema()
    }
    assert schemas["web_search"]["properties"]["max_results"]["minimum"] == 1
    assert schemas["web_search"]["properties"]["max_results"]["maximum"] == 20
    assert schemas["open_page"]["properties"]["max_chars"]["minimum"] == 500
    assert schemas["open_page"]["properties"]["max_chars"]["maximum"] == 20000
    assert schemas["find_in_page"]["properties"]["context_chars"]["minimum"] == 50
    assert schemas["find_in_page"]["properties"]["context_chars"]["maximum"] == 2000
    assert schemas["find_in_page"]["properties"]["max_matches"]["minimum"] == 1
    assert schemas["find_in_page"]["properties"]["max_matches"]["maximum"] == 20


# ── open_page / find_in_page 执行器 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_page_executor_fetches_page(monkeypatch):
    async def fake_fetch_page(self, url, max_chars=10000, timeout=10):
        return "Fetched Title", "page body " * 100

    monkeypatch.setattr(DeepReader, "fetch_page", fake_fetch_page)
    result = await _open_page_executor("open_page", {"url": "https://example.com/page"})
    assert isinstance(result, PageReadResult)
    assert result.title == "Fetched Title"
    assert "page body" in result.content
    assert not result.error


@pytest.mark.asyncio
async def test_open_page_executor_missing_url():
    result = await _open_page_executor("open_page", {})
    assert result.error


@pytest.mark.asyncio
async def test_find_in_page_executor(monkeypatch):
    async def fake_fetch_page(self, url, max_chars=10000, timeout=10):
        return "Page Title", "long page content " * 100

    async def fake_find(self, url, query, **kwargs):
        return ["...match one...", "...match two..."]

    monkeypatch.setattr(DeepReader, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(DeepReader, "find_in_page", fake_find)
    result = await _find_in_page_executor(
        "find_in_page", {"url": "https://example.com/page", "query": "latex"}
    )
    assert isinstance(result, FindInPageResult)
    assert len(result.matches) == 2
    assert not result.error


@pytest.mark.asyncio
async def test_open_page_executor_clamps_max_chars(monkeypatch):
    captured: dict[str, int] = {}

    async def fake_fetch_page(self, url, max_chars=10000, timeout=10):
        captured["max_chars"] = max_chars
        return "Title", "page body " * 20

    monkeypatch.setattr(DeepReader, "fetch_page", fake_fetch_page)
    await _open_page_executor(
        "open_page", {"url": "https://example.com/page", "max_chars": 10**9}
    )
    assert captured["max_chars"] == 20000
    await _open_page_executor(
        "open_page", {"url": "https://example.com/page", "max_chars": 10}
    )
    assert captured["max_chars"] == 500


@pytest.mark.asyncio
async def test_find_in_page_executor_uses_large_search_cap(monkeypatch):
    captured: dict[str, int] = {}

    async def fake_fetch_page(self, url, max_chars=10000, timeout=10):
        captured["fetch_max_chars"] = max_chars
        return "Title", "long page content " * 1000

    async def fake_find(self, url, query, **kwargs):
        captured["find_max_chars"] = kwargs.get("max_chars", 10000)
        return ["...match..."]

    monkeypatch.setattr(DeepReader, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(DeepReader, "find_in_page", fake_find)
    await _find_in_page_executor(
        "find_in_page", {"url": "https://example.com/page", "query": "latex"}
    )
    assert captured["fetch_max_chars"] >= 100000
    assert captured["find_max_chars"] >= 100000


@pytest.mark.asyncio
async def test_find_in_page_executor_missing_args():
    result = await _find_in_page_executor("find_in_page", {"url": ""})
    assert result.error


# ── SSRF 防护 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deep_reader_rejects_private_localhost_and_non_https_urls():
    reader = DeepReader()
    for url in (
        "https://127.0.0.1/admin",
        "https://localhost/admin",
        "https://10.0.0.5/x",
        "https://192.168.1.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x",
        "http://example.com/x",
    ):
        title, content = await reader.fetch_page(url)
        assert title == "" and content == "", url


# ── 引用校验与工具证据裁剪 ─────────────────────────────────────────────────


def test_validate_citations_marks_unverified_links():
    allowed = {"https://example.com/a", "https://example.com/b?x=1"}
    content = (
        "See [A](https://example.com/a) and "
        "[B](https://example.com/b?x=1) and "
        "[Bad](https://evil.example/x)."
    )
    unverified, note = WritingChatService._validate_citations(content, allowed)
    assert unverified == ["https://evil.example/x"]
    assert "evil.example" in note


def test_validate_citations_empty_when_no_links():
    unverified, note = WritingChatService._validate_citations(
        "plain text without links", {"https://example.com/a"}
    )
    assert unverified == []
    assert note == ""


def test_trim_tool_messages_removes_oldest_pairs():
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1"}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "x" * 40000},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "2"}],
        },
        {"role": "tool", "tool_call_id": "2", "content": "y" * 30000},
    ]
    trimmed = WritingChatService._trim_tool_messages(messages, max_chars=50000)
    assert len(trimmed) == 3
    assert trimmed[0]["role"] == "user"
    assert trimmed[1]["tool_calls"][0]["id"] == "2"
    assert trimmed[2]["tool_call_id"] == "2"


def test_trim_tool_messages_keeps_latest_pair_when_single_exceeds_budget():
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1"}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "x" * 70000},
    ]
    trimmed = WritingChatService._trim_tool_messages(messages, max_chars=50000)
    # 最新证据是生成最终回复的依据，预算超限时保留（软上限）
    assert len(trimmed) == 3
    assert trimmed[2]["tool_call_id"] == "1"


def test_trim_tool_messages_skips_incomplete_current_round():
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "old"}],
        },
        {"role": "tool", "tool_call_id": "old", "content": "x" * 20000},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "a"}, {"id": "b"}],
        },
        {"role": "tool", "tool_call_id": "a", "content": "y" * 60000},
    ]
    trimmed = WritingChatService._trim_tool_messages(messages, max_chars=50000)
    # 只裁剪已完成的旧对；正在执行的当轮多工具调用不能被拆散
    assert len(trimmed) == 3
    assert trimmed[1]["tool_calls"][0]["id"] == "a"
    assert trimmed[2]["tool_call_id"] == "a"

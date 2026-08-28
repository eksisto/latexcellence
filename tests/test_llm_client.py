"""
llm_client.chat_stream_with_usage 行为测试。

覆盖：
    - 末次 chunk 透传 UsageInfo
    - 不支持 stream_options 时降级（usage=None）
    - 限流等待被正常执行
    - extra_body 配置透传（非空传递 / 空省略）
"""

from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.core.llm_client import (
    LLMClient,
    StreamChunk,
    UsageInfo,
    chat_stream_with_tools_retry,
)


def _make_chunk(content: Any, usage: Any = None) -> Any:
    """构造一个模拟 OpenAI 流式 chunk。"""
    chunk = MagicMock()
    chunk.choices = []
    if content is not None:
        delta = MagicMock()
        delta.content = content
        # 显式置 None：MagicMock 会自动创建属性，真实 API 无该字段时为 None
        delta.reasoning_content = None
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    chunk.usage = usage
    return chunk


def _make_reasoning_chunk(text: str) -> Any:
    """构造一个思维链流式 chunk（delta.reasoning_content，智谱/DeepSeek 风格）。"""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = None
    delta.reasoning_content = text
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    chunk.usage = None
    return chunk


def _make_usage(p: int, c: int, t: int) -> Any:
    usage = MagicMock()
    usage.prompt_tokens = p
    usage.completion_tokens = c
    usage.total_tokens = t
    return usage


# ── async iterator helper ───────────────────────────────────────────────────


class _AsyncIter:
    def __init__(self, items: list[Any]):
        self._items = items

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._i]
        self._i += 1
        return item


# ── tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_stream_with_usage_yields_tokens_then_usage(monkeypatch):
    client = LLMClient()
    # 跳过限流
    client._rate_limit = AsyncMock()

    mock_create = AsyncMock(
        return_value=_AsyncIter(
            [
                _make_chunk("Hello"),
                _make_chunk(" world"),
                _make_chunk(None, _make_usage(10, 5, 15)),
            ]
        )
    )
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    chunks: list[StreamChunk] = [
        c async for c in client.chat_stream_with_usage(messages=[])
    ]

    contents = [c.content for c in chunks if c.content is not None]
    assert contents == ["Hello", " world"]
    usage_chunk = [c for c in chunks if c.usage is not None]
    assert len(usage_chunk) == 1
    assert usage_chunk[0].usage.total_tokens == 15
    assert usage_chunk[0].usage.prompt_tokens == 10
    assert usage_chunk[0].usage.completion_tokens == 5


@pytest.mark.asyncio
async def test_chat_stream_with_usage_fallback_when_stream_options_unsupported(
    monkeypatch,
):
    client = LLMClient()
    client._rate_limit = AsyncMock()

    call_count = {"n": 0}

    async def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1 and "stream_options" in kwargs:
            raise RuntimeError("stream_options not supported")
        return _AsyncIter([_make_chunk("ok")])

    client._client = MagicMock()
    client._client.chat.completions.create = fake_create

    chunks: list[StreamChunk] = [
        c async for c in client.chat_stream_with_usage(messages=[])
    ]
    assert any(c.content == "ok" for c in chunks)
    # 没有末尾 usage chunk，因为模拟流里没有 usage 数据
    assert not any(c.usage is not None for c in chunks)
    # 第一次带 stream_options 失败，第二次不带参数成功
    assert call_count["n"] == 2


# ── extra_body 透传 ─────────────────────────────────────────────────────────


def _fake_cfg(extra_body: dict[str, Any] | None) -> SimpleNamespace:
    """构造替换 llm_client.get_config 的最小配置对象。"""
    return SimpleNamespace(
        llm=SimpleNamespace(
            model="test-model",
            temperature=0.1,
            max_tokens=100,
            max_concurrency=2,
            extra_body=extra_body or {},
        )
    )


def _make_completion_response(content: str) -> Any:
    response = MagicMock()
    response.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
    response.usage = None
    return response


@pytest.mark.asyncio
async def test_chat_with_usage_passes_extra_body_when_configured(monkeypatch):
    client = LLMClient()
    client._rate_limit = AsyncMock()
    monkeypatch.setattr(
        "backend.core.llm_client.get_config",
        lambda: _fake_cfg({"reasoning_effort": "low"}),
    )

    mock_create = AsyncMock(return_value=_make_completion_response("ok"))
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    content, _ = await client.chat_with_usage(
        messages=[{"role": "user", "content": "hi"}]
    )

    assert content == "ok"
    assert mock_create.call_args.kwargs["extra_body"] == {"reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_chat_with_usage_omits_extra_body_when_empty(monkeypatch):
    client = LLMClient()
    client._rate_limit = AsyncMock()
    monkeypatch.setattr("backend.core.llm_client.get_config", lambda: _fake_cfg({}))

    mock_create = AsyncMock(return_value=_make_completion_response("ok"))
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    await client.chat_with_usage(messages=[{"role": "user", "content": "hi"}])

    assert "extra_body" not in mock_create.call_args.kwargs


@pytest.mark.asyncio
async def test_chat_stream_passes_extra_body_when_configured(monkeypatch):
    client = LLMClient()
    client._rate_limit = AsyncMock()
    monkeypatch.setattr(
        "backend.core.llm_client.get_config",
        lambda: _fake_cfg({"thinking": {"reasoning_budget": "low"}}),
    )

    mock_create = AsyncMock(return_value=_AsyncIter([_make_chunk("ok")]))
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    tokens = [tok async for tok in client.chat_stream(messages=[])]

    assert tokens == ["ok"]
    assert mock_create.call_args.kwargs["extra_body"] == {
        "thinking": {"reasoning_budget": "low"}
    }


@pytest.mark.asyncio
async def test_chat_stream_with_usage_yields_reasoning_chunks():
    """思维链 chunk 转为 StreamChunk(reasoning=...)，与 content/usage 互斥。"""
    client = LLMClient()
    client._rate_limit = AsyncMock()

    mock_create = AsyncMock(
        return_value=_AsyncIter(
            [
                _make_reasoning_chunk("先规划"),
                _make_chunk("Hello"),
                _make_chunk(None, _make_usage(10, 5, 15)),
            ]
        )
    )
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    chunks: list[StreamChunk] = [
        c async for c in client.chat_stream_with_usage(messages=[])
    ]

    assert chunks[0].reasoning == "先规划"
    assert chunks[0].content is None and chunks[0].usage is None
    assert chunks[1].content == "Hello" and chunks[1].reasoning is None
    assert chunks[2].usage is not None and chunks[2].reasoning is None


def _make_tool_delta_chunk(index: int, cid=None, name=None, args=None) -> Any:
    """构造一个携带 tool_calls 增量（单条 delta）的流式 chunk。"""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = None
    delta.reasoning_content = None
    tc = MagicMock()
    tc.index = index
    tc.id = cid
    fn = MagicMock()
    fn.name = name
    fn.arguments = args
    tc.function = fn
    delta.tool_calls = [tc]
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    chunk.usage = None
    return chunk


@pytest.mark.asyncio
async def test_chat_stream_with_tools_yields_reasoning_and_aggregated_tool_calls():
    """tools 流路径：思维链 chunk 转为 reasoning 片段，tool_calls 跨 delta
    累积后在流末尾一次性返回（三个 Agent 使用的正是该路径）。"""
    client = LLMClient()
    client._rate_limit = AsyncMock()

    mock_create = AsyncMock(
        return_value=_AsyncIter(
            [
                _make_reasoning_chunk("先规划"),
                _make_chunk("Hello"),
                _make_tool_delta_chunk(0, cid="t1", name="write_file", args='{"a":'),
                _make_tool_delta_chunk(0, args=" 1}"),
                _make_chunk(None, _make_usage(10, 5, 15)),
            ]
        )
    )
    client._client = MagicMock()
    client._client.chat.completions.create = mock_create

    chunks: list[StreamChunk] = [
        c async for c in client.chat_stream_with_tools(messages=[], tools=[])
    ]

    assert chunks[0].reasoning == "先规划"
    assert chunks[0].content is None and chunks[0].usage is None
    assert chunks[1].content == "Hello" and chunks[1].reasoning is None
    assert chunks[2].usage is not None and chunks[2].usage.total_tokens == 15
    # tool_calls 在流末尾一次性返回（usage 之后）
    assert len(chunks) == 4
    calls = chunks[3].tool_calls
    assert calls is not None and len(calls) == 1
    assert calls[0].id == "t1"
    assert calls[0].function.name == "write_file"
    assert calls[0].function.arguments == '{"a": 1}'


class _FakeStreamLLM:
    """按调用次序返回预设结果/异常的假 LLM。

    sequence 每项为 (exc_or_None, chunks)：该次流先产出 chunks，随后抛出
    exc（chunks 为空即表示尚未产出任何 chunk 时就失败）。
    """

    def __init__(self, sequence: list[tuple[Exception | None, list[StreamChunk]]]):
        self.sequence = sequence
        self.call_count = 0

    def chat_stream_with_tools(self, **kwargs):
        self.call_count += 1
        exc, chunks = self.sequence[self.call_count - 1]

        async def _gen():
            for c in chunks:
                yield c
            if exc is not None:
                raise exc

        return _gen()


@pytest.mark.asyncio
async def test_stream_tools_retry_transient_failure_retried_once():
    """首 chunk 前的瞬时失败（连接抖动）补一次重试，第二次调用成功。"""
    llm = _FakeStreamLLM(
        [
            (ConnectionError("network jitter"), []),
            (None, [StreamChunk(content="ok", usage=None)]),
        ]
    )

    out = [c async for c in chat_stream_with_tools_retry(llm, messages=[], tools=[])]

    assert [c.content for c in out] == ["ok"]
    assert llm.call_count == 2


@pytest.mark.asyncio
async def test_stream_tools_retry_permanent_error_not_retried():
    """永久性错误（如 400 鉴权）不重试，直接抛出。"""
    llm = _FakeStreamLLM([(RuntimeError("bad request"), [])])

    with pytest.raises(RuntimeError, match="bad request"):
        async for _ in chat_stream_with_tools_retry(llm, messages=[], tools=[]):
            pass

    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_stream_tools_retry_no_retry_after_first_chunk():
    """已有增量输出后的失败无法安全重放，按原样抛出、不重试。"""
    llm = _FakeStreamLLM(
        [(RuntimeError("mid stream"), [StreamChunk(content="partial", usage=None)])]
    )

    got: list[StreamChunk] = []
    with pytest.raises(RuntimeError, match="mid stream"):
        async for c in chat_stream_with_tools_retry(llm, messages=[], tools=[]):
            got.append(c)

    assert [c.content for c in got] == ["partial"]
    assert llm.call_count == 1


def test_transient_stream_error_classifier():
    """瞬时异常分类：连接/超时/429/5xx 与内置 ConnectionError/TimeoutError
    可重试，其余（400、普通异常）不可重试。"""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        BadRequestError,
        InternalServerError,
        RateLimitError,
    )

    from backend.core.llm_client import _is_transient_stream_error

    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")

    assert _is_transient_stream_error(APIConnectionError(request=request))
    assert _is_transient_stream_error(APITimeoutError(request=request))
    assert _is_transient_stream_error(
        RateLimitError("429", response=httpx.Response(429, request=request), body=None)
    )
    assert _is_transient_stream_error(
        InternalServerError(
            "500", response=httpx.Response(500, request=request), body=None
        )
    )
    assert _is_transient_stream_error(ConnectionError("boom"))
    assert _is_transient_stream_error(TimeoutError("boom"))

    assert not _is_transient_stream_error(
        BadRequestError("400", response=httpx.Response(400, request=request), body=None)
    )
    assert not _is_transient_stream_error(RuntimeError("boom"))

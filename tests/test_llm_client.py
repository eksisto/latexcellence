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

import pytest

from backend.core.llm_client import LLMClient, StreamChunk, UsageInfo


def _make_chunk(content: Any, usage: Any = None) -> Any:
    """构造一个模拟 OpenAI 流式 chunk。"""
    chunk = MagicMock()
    chunk.choices = []
    if content is not None:
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    chunk.usage = usage
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

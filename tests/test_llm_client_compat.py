"""LLMClient 推理模型统一兼容封装层（multi-model §5.2，P2 交付）。

覆盖：
    - 请求侧参数适配：参数类 4xx（拒绝 temperature / 要求
      max_completion_tokens）自动降参重试一次并记 warning 日志；
      非参数类错误不降参
    - 响应侧清洗：JSON 路径剥离 content 中的 <think>...</think> 片段
    - 档案覆盖项合并：extra_body / temperature / max_tokens 覆盖与继承
    - 上下文窗口探测缓存按档案隔离（§10 风险项）

全部 Mock AsyncOpenAI，严禁真实网络请求。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError, BadRequestError, UnprocessableEntityError

from backend.core import llm_client as llm_client_module
from backend.core.config import AppConfig, LLMConfig
from backend.core.llm_client import (
    LLMClient,
    ResolvedLLMCfg,
    _adapt_kwargs_for_param_error,
    _strip_think_blocks,
)

TOP_LLM = {
    "api_key": "key-top",
    "base_url": "https://top.example.com/v1",
    "model": "top-model",
    "temperature": 0.3,
    "max_tokens": 16384,
    "request_interval": 1.5,
    "max_concurrency": 5,
    "context_window_tokens": 0,
    "models_probe_url": "",
    "extra_body": {},
}

VISION_PROFILE = {
    "id": "vision-pro",
    "name": "视觉模型",
    "base_url": "https://vision.example.com/v1",
    "api_key": "key-vision",
    "model": "vision-model",
    "capabilities": ["vision"],
    "temperature": 0.1,
    "max_tokens": 4096,
    "extra_body": {"thinking": {"type": "disabled"}},
}

REASONING_PROFILE = {
    "id": "deepthink",
    "name": "深度思考模型",
    "base_url": "https://think.example.com/v1",
    "api_key": "key-think",
    "model": "think-model",
    "capabilities": ["reasoning"],
}


def _install_cfg(monkeypatch, **llm_overrides) -> AppConfig:
    """安装受控内存配置（仅 patch llm_client 的 get_config）。"""
    data = dict(TOP_LLM)
    data.update(llm_overrides)
    cfg = AppConfig(llm=LLMConfig(**data))
    monkeypatch.setattr(llm_client_module, "get_config", lambda: cfg)
    return cfg


def _make_completion_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [
        MagicMock(message=MagicMock(content=content)),
    ]
    response.usage = None
    return response


def _api_error(exc_cls, status: int, message: str) -> APIStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(
        status, request=request, json={"error": {"message": message}}
    )
    return exc_cls(message, response=response, body=None)


class _AsyncIter:
    def __init__(self, items: list):
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


def _make_chunk(content) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = []
    if content is not None:
        delta = MagicMock()
        delta.content = content
        delta.reasoning_content = None
        chunk.choices = [MagicMock(delta=delta)]
    chunk.usage = None
    return chunk


def _wired_client(profile_id: str | None = None) -> LLMClient:
    """构造跳过限流、注入 mock create、带可断言 logger 的客户端。"""
    client = LLMClient(profile_id=profile_id)
    client._rate_limit = AsyncMock()
    client._logger = MagicMock()
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock()
    return client


# ── 请求侧：参数类 4xx 自动降参重试 ───────────────────────────────────────


class TestParameterAdaptation:
    @pytest.mark.asyncio
    async def test_retries_once_without_temperature(self, monkeypatch):
        _install_cfg(monkeypatch)
        client = _wired_client()
        error = _api_error(
            BadRequestError,
            400,
            "temperature does not support 0.3 with this model."
            " Only the default (1) temperature is supported",
        )
        create = client._client.chat.completions.create
        create.side_effect = [error, _make_completion_response("ok")]

        content = await client.chat(messages=[{"role": "user", "content": "hi"}])

        assert content == "ok"
        assert create.call_count == 2
        assert "temperature" in create.call_args_list[0].kwargs
        assert "temperature" not in create.call_args_list[1].kwargs
        # 降参重试必须记日志
        client._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_renames_max_tokens_to_max_completion_tokens(self, monkeypatch):
        _install_cfg(monkeypatch)
        client = _wired_client()
        error = _api_error(
            BadRequestError,
            400,
            "max_tokens is not supported by this model."
            " Use 'max_completion_tokens' instead.",
        )
        create = client._client.chat.completions.create
        create.side_effect = [error, _make_completion_response("ok")]

        await client.chat(messages=[{"role": "user", "content": "hi"}])

        assert create.call_count == 2
        first = create.call_args_list[0].kwargs
        second = create.call_args_list[1].kwargs
        assert first["max_tokens"] == TOP_LLM["max_tokens"]
        assert "max_tokens" not in second
        assert second["max_completion_tokens"] == TOP_LLM["max_tokens"]
        client._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_adaptation_for_unrelated_400(self, monkeypatch):
        """与参数无关的 4xx（如模型名错误）不降参，走原有失败路径。"""
        _install_cfg(monkeypatch)
        client = _wired_client()
        error = _api_error(BadRequestError, 400, "model 'ghost-model' does not exist")
        create = client._client.chat.completions.create
        create.side_effect = [error]

        with pytest.raises(BadRequestError, match="ghost-model"):
            await client.chat(
                messages=[{"role": "user", "content": "hi"}], max_retries=1
            )

        assert create.call_count == 1
        assert "temperature" in create.call_args.kwargs
        client._logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_adaptation_applies_to_tool_call_path(self, monkeypatch):
        _install_cfg(monkeypatch)
        client = _wired_client()
        error = _api_error(BadRequestError, 400, "'temperature' is not supported")
        completion = MagicMock()
        completion.usage = None
        create = client._client.chat.completions.create
        create.side_effect = [error, completion]

        await client.chat_with_tools(messages=[], tools=[], max_retries=2)

        assert create.call_count == 2
        assert "temperature" not in create.call_args_list[1].kwargs

    @pytest.mark.asyncio
    async def test_adaptation_applies_to_stream_path(self, monkeypatch):
        _install_cfg(monkeypatch)
        client = _wired_client()
        error = _api_error(BadRequestError, 400, "'temperature' is not supported")
        create = client._client.chat.completions.create
        create.side_effect = [error, _AsyncIter([_make_chunk("ok")])]

        tokens = [tok async for tok in client.chat_stream(messages=[])]

        assert tokens == ["ok"]
        assert create.call_count == 2
        assert "temperature" not in create.call_args_list[1].kwargs

    def test_adapt_kwargs_pure_function(self):
        kwargs = {
            "model": "m",
            "messages": [],
            "temperature": 0.3,
            "max_tokens": 100,
        }
        # 非参数类错误 / 非 4xx / 非 APIStatusError：不动
        unrelated = _api_error(BadRequestError, 400, "invalid api key format")
        assert _adapt_kwargs_for_param_error(kwargs, unrelated) is None
        server_error = _api_error(APIStatusError, 500, "temperature boom")
        assert _adapt_kwargs_for_param_error(kwargs, server_error) is None
        assert (
            _adapt_kwargs_for_param_error(kwargs, RuntimeError("temperature")) is None
        )

        # 422 同样视为参数类错误
        err_422 = _api_error(
            UnprocessableEntityError, 422, "temperature must be omitted for this model"
        )
        adapted = _adapt_kwargs_for_param_error(kwargs, err_422)
        assert adapted is not None
        assert "temperature" not in adapted

        # max_completion_tokens 语义：更名而非剔除（temperature 未被点名则保留）
        rename = _api_error(
            BadRequestError,
            400,
            "max_tokens is not supported, use max_completion_tokens",
        )
        adapted = _adapt_kwargs_for_param_error(kwargs, rename)
        assert adapted == {
            "model": "m",
            "messages": [],
            "temperature": 0.3,
            "max_completion_tokens": 100,
        }

        # 无命中参数名 → 不做无意义重试
        nothing = _api_error(BadRequestError, 400, "context length exceeded")
        assert _adapt_kwargs_for_param_error(kwargs, nothing) is None


# ── 响应侧：<think> 思维链剥离（JSON 路径）───────────────────────────────


class TestThinkBlockStripping:
    def test_strip_pure_function(self):
        assert _strip_think_blocks('<think>推理</think>{"a": 1}') == '{"a": 1}'
        assert _strip_think_blocks("no tags here") == "no tags here"
        # 未闭合标签不处理（无法确定边界）
        assert _strip_think_blocks("<think>unclosed") == "<think>unclosed"
        assert _strip_think_blocks("") == ""

    @pytest.mark.asyncio
    async def test_chat_strips_multiline_think_block(self, monkeypatch):
        _install_cfg(monkeypatch)
        client = _wired_client()
        create = client._client.chat.completions.create
        content = '<think>第一行推理\n第二行推理</think>\n{"a": 1}'
        create.return_value = _make_completion_response(content)

        result = await client.chat(messages=[])

        assert result == '\n{"a": 1}'
        assert "<think>" not in result

    @pytest.mark.asyncio
    async def test_chat_with_usage_strips_and_keeps_usage(self, monkeypatch):
        from backend.core.llm_client import UsageInfo

        _install_cfg(monkeypatch)
        client = _wired_client()
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="<think>x</think>ok"))]
        response.usage = UsageInfo(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        client._client.chat.completions.create = AsyncMock(return_value=response)

        content, usage = await client.chat_with_usage(messages=[])

        assert content == "ok"
        assert usage is not None and usage.total_tokens == 3

    @pytest.mark.asyncio
    async def test_none_content_passes_through(self, monkeypatch):
        """content 为 None（如纯工具调用响应）不被清洗破坏。"""
        _install_cfg(monkeypatch)
        client = _wired_client()
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content=None))]
        response.usage = None
        client._client.chat.completions.create = AsyncMock(return_value=response)

        content, _ = await client.chat_with_usage(messages=[])
        assert content is None

    def test_strip_degenerate_repeated_open_tags(self):
        """退化模型重复吐出海量 <think> 标签时语义与正则版逐例一致（线性）。"""
        # 全部未闭合 → 原样返回
        unclosed = ("<think>" * 2000) + '{"a": 1}'
        assert _strip_think_blocks(unclosed) == unclosed
        # 多段闭合 + 末尾未闭合 → 仅剥离闭合段
        assert _strip_think_blocks("A<think>x</think>B<think>y</think>C<think>z") == (
            "ABC<think>z"
        )
        # 未闭合标签内再含 <think> → 整段未确定边界，保持原样
        assert _strip_think_blocks("A<think>x<think>y") == "A<think>x<think>y"
        # 嵌套开标签被闭合覆盖 → 剥离至最近的闭合标签（与正则最短匹配一致）
        assert _strip_think_blocks("A<think>x<think>y</think>B") == "AB"
        # 仅闭合无开启 → 原样返回
        assert _strip_think_blocks("A</think>B") == "A</think>B"


# ── 档案覆盖项合并与 extra_body 透传 ─────────────────────────────────────


class TestProfileOverrideMerge:
    @pytest.mark.asyncio
    async def test_profile_extra_body_overrides_top_level(self, monkeypatch):
        _install_cfg(
            monkeypatch,
            extra_body={"reasoning_effort": "high"},
            profiles=[VISION_PROFILE],
        )
        client = _wired_client(profile_id="vision-pro")
        create = client._client.chat.completions.create
        create.return_value = _make_completion_response("ok")

        await client.chat_with_usage(messages=[{"role": "user", "content": "hi"}])

        kwargs = create.call_args.kwargs
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == 4096
        assert kwargs["model"] == "vision-model"

    @pytest.mark.asyncio
    async def test_profile_inherits_top_level_when_overrides_empty(self, monkeypatch):
        _install_cfg(monkeypatch, profiles=[REASONING_PROFILE])
        client = _wired_client(profile_id="deepthink")
        create = client._client.chat.completions.create
        create.return_value = _make_completion_response("ok")

        await client.chat_with_usage(messages=[{"role": "user", "content": "hi"}])

        kwargs = create.call_args.kwargs
        # 覆盖项缺省 → 继承顶层（顶层 extra_body 为空 → 不发送）
        assert kwargs["temperature"] == TOP_LLM["temperature"]
        assert kwargs["max_tokens"] == TOP_LLM["max_tokens"]
        assert "extra_body" not in kwargs
        # 连接三件套始终取档案（经合并视图断言；client 已被注入 mock）
        assert kwargs["model"] == "think-model"
        assert client._resolve_cfg().base_url == "https://think.example.com/v1"
        assert client._resolve_cfg().api_key == "key-think"

    def test_resolved_view_inherits_zero_and_empty_sentinels(self, monkeypatch):
        """0 / {} 覆盖项语义 = 继承顶层（extra_body 清空不可表达，已接受限制）。"""
        profile = dict(
            REASONING_PROFILE,
            request_interval=0,  # 0 = 继承顶层
            context_window_tokens=0,
            models_probe_url="",
        )
        _install_cfg(monkeypatch, profiles=[profile])
        client = LLMClient(profile_id="deepthink")
        resolved = client._resolve_cfg()
        assert isinstance(resolved, ResolvedLLMCfg)
        assert resolved.request_interval == TOP_LLM["request_interval"]
        assert resolved.context_window_tokens == TOP_LLM["context_window_tokens"]
        assert resolved.models_probe_url == TOP_LLM["models_probe_url"]
        assert resolved.extra_body == TOP_LLM["extra_body"]


# ── 上下文窗口探测缓存按档案隔离（§10）───────────────────────────────────


class TestContextWindowIsolation:
    @pytest.mark.asyncio
    async def test_explicit_profile_window_skips_probe(self, monkeypatch):
        profile = dict(REASONING_PROFILE, context_window_tokens=32768)
        _install_cfg(monkeypatch, profiles=[profile])
        client = LLMClient(profile_id="deepthink")
        with patch.object(
            client, "_probe_context_window_sync", return_value=1
        ) as probe:
            assert await client.get_context_window() == 32768
        probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_cache_not_shared_across_profiles(self, monkeypatch):
        """相同 (base_url, model) 的不同档案各自探测，缓存互不串味。"""
        shared_url = dict(REASONING_PROFILE)
        same_conn = dict(
            shared_url, base_url="https://shared.example.com/v1", model="same-model"
        )
        vision_same_conn = dict(
            VISION_PROFILE, base_url="https://shared.example.com/v1", model="same-model"
        )
        _install_cfg(monkeypatch, profiles=[same_conn, vision_same_conn])
        a = LLMClient(profile_id="deepthink")
        b = LLMClient(profile_id="vision-pro")

        with patch.object(
            a, "_probe_context_window_sync", return_value=111
        ) as pa, patch.object(b, "_probe_context_window_sync", return_value=222) as pb:
            assert await a.get_context_window() == 111
            assert await b.get_context_window() == 222
            assert await a.get_context_window() == 111
        assert pa.call_count == 1
        assert pb.call_count == 1

    def test_window_cache_key_includes_profile_scope(self, monkeypatch):
        _install_cfg(monkeypatch, profiles=[REASONING_PROFILE])
        scoped = LLMClient(profile_id="deepthink")
        bare = LLMClient()
        key = scoped._window_cache_key(scoped._resolve_cfg())
        assert key[0] == "deepthink"
        bare_key = bare._window_cache_key(bare._resolve_cfg())
        assert bare_key[0] == "__default__"


# ── 旧实例资源回收（reset / 指纹重建路径）────────────────────────────────


class TestStaleClientReclaim:
    @pytest.mark.asyncio
    async def test_reset_close_task_is_tracked_and_completes(self):
        """fire-and-forget close 任务被强引用（防 GC 中途回收）并正常完成出集。"""
        client = LLMClient()
        stale = AsyncMock()
        client._client = stale
        client._client_fingerprint = ("u", "k", "m")
        before = set(llm_client_module._pending_close_tasks)
        client.reset()
        new_tasks = llm_client_module._pending_close_tasks - before
        assert new_tasks  # 任务被强引用，不会在 await 期间被 GC 静默回收
        for task in new_tasks:
            await task
        stale.close.assert_awaited_once()
        # 再让出一拍，done callback（出集）才会在就绪队列中执行
        await asyncio.sleep(0)
        assert not (llm_client_module._pending_close_tasks & new_tasks)

    def test_reset_without_loop_skips_close_tracking(self):
        """同步上下文（无事件循环）reset 不调度关闭任务，静默交由 GC 兜底。"""
        client = LLMClient()
        client._client = AsyncMock()
        client._client_fingerprint = ("u", "k", "m")
        before = set(llm_client_module._pending_close_tasks)
        client.reset()
        assert llm_client_module._pending_close_tasks == before
        assert client._client is None

    def test_fingerprint_rebuild_closes_replaced_client(self, monkeypatch):
        """指纹变化重建内部 AsyncOpenAI 时关闭被替换实例（双保险路径不泄漏连接池）。"""
        cfg = _install_cfg(monkeypatch, profiles=[VISION_PROFILE])
        client = LLMClient(profile_id="vision-pro")
        first = client.client
        cfg.llm.profiles[0].api_key = "key-rotated"
        with patch.object(llm_client_module, "_close_async_client_quietly") as closer:
            second = client.client
        assert second is not first
        closer.assert_called_once_with(first)

    def test_fingerprint_unchanged_does_not_rebuild_nor_close(self, monkeypatch):
        """指纹未变化时复用实例，不触发重建与关闭。"""
        _install_cfg(monkeypatch, profiles=[VISION_PROFILE])
        client = LLMClient(profile_id="vision-pro")
        first = client.client
        with patch.object(llm_client_module, "_close_async_client_quietly") as closer:
            assert client.client is first
        closer.assert_not_called()

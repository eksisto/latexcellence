"""
llm_cache 模块单测：LRU 缓存、并发合流、失败不缓存、键隔离。
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from backend.utils.llm_cache import (
    _CACHE_MAX_ENTRIES,
    build_cache_key,
    cache_get,
    cache_put,
    cached_call,
    cached_llm_validate,
    clear_llm_cache,
)


class _DemoResult(BaseModel):
    """与被测服务同构的最小响应模型。"""

    corrected: str
    changes: list[str] = []


class _StubLLM:
    """同步可控的 LLM 客户端桩：记录调用次数，按脚本返回响应。"""

    def __init__(self, model: str = "test-model", responses: list[str] | None = None):
        self.model = model
        self.calls = 0
        self._responses = list(responses or [])

    @property
    def resolved_model(self) -> str:
        return self.model

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return json.dumps({"corrected": "ok", "changes": []})


_MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "text"},
]


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_llm_cache()
    yield
    clear_llm_cache()


@pytest.mark.asyncio
class TestCachedLLMValidate:
    async def test_second_identical_call_hits_cache(self):
        llm = _StubLLM()
        first = await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        second = await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        assert llm.calls == 1
        assert first.corrected == second.corrected == "ok"

    async def test_different_prompt_misses_cache(self):
        llm = _StubLLM()
        await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        other = [{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}]
        await cached_llm_validate(
            llm, messages=other, model_cls=_DemoResult, temperature=0.3
        )
        assert llm.calls == 2

    async def test_different_model_misses_cache(self):
        llm_a = _StubLLM(model="model-a")
        llm_b = _StubLLM(model="model-b")
        await cached_llm_validate(
            llm_a, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        await cached_llm_validate(
            llm_b, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        assert llm_a.calls == 1
        assert llm_b.calls == 1

    async def test_sampling_params_part_of_key(self):
        llm = _StubLLM()
        await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.9
        )
        assert llm.calls == 2

    async def test_failure_not_cached(self):
        # max_retries=1：首次抛错后立即上抛，不重试
        llm = _StubLLM(responses=["not-json-at-all{{{"])
        with pytest.raises(ValueError):
            await cached_llm_validate(
                llm,
                messages=_MESSAGES,
                model_cls=_DemoResult,
                max_retries=1,
                temperature=0.3,
            )
        # 桩响应耗尽后回落默认成功响应：失败结果未被缓存，会真实重调
        result = await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        assert result.corrected == "ok"
        assert llm.calls == 2

    async def test_concurrent_identical_calls_merged(self):
        llm = _StubLLM()
        results = await asyncio.gather(
            *(
                cached_llm_validate(
                    llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
                )
                for _ in range(5)
            )
        )
        assert llm.calls == 1
        assert all(r.corrected == "ok" for r in results)

    async def test_magicmock_llm_compatible(self):
        """服务层测试普遍传 MagicMock 客户端，缓存键不得因此崩溃。"""
        from unittest.mock import AsyncMock

        llm = MagicMock()
        llm.chat = AsyncMock(return_value=json.dumps({"corrected": "ok"}))
        result = await cached_llm_validate(
            llm, messages=_MESSAGES, model_cls=_DemoResult, temperature=0.3
        )
        assert result.corrected == "ok"


@pytest.mark.asyncio
class TestCachedCall:
    async def test_concurrent_merge_single_fetch(self):
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"value": 42}

        key = build_cache_key("m", _MESSAGES)
        results = await asyncio.gather(*(cached_call(key, fetch) for _ in range(4)))
        assert calls == 1
        assert all(r == {"value": 42} for r in results)

    async def test_fetch_error_propagates_and_not_cached(self):
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        key = build_cache_key("m", _MESSAGES)
        with pytest.raises(RuntimeError, match="boom"):
            await cached_call(key, fetch)
        assert cache_get(key) is None
        assert calls == 1

    async def test_caller_mutation_does_not_leak_into_cache(self):
        """缓存边界做值隔离：调用方就地改写返回值不得污染缓存条目。"""
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return {"latex": "x^2", "changes": ["a"]}

        key = build_cache_key("m", _MESSAGES)
        first = await cached_call(key, fetch)
        first["latex"] = "polluted"
        first["changes"].append("b")

        second = await cached_call(key, fetch)
        assert calls == 1
        assert second == {"latex": "x^2", "changes": ["a"]}


class TestCacheInternals:
    def test_lru_eviction_bound(self):
        clear_llm_cache()
        for i in range(_CACHE_MAX_ENTRIES + 10):
            cache_put(f"key-{i}", i)
        assert cache_get("key-0") is None  # 最旧条目被淘汰
        assert cache_get(f"key-{_CACHE_MAX_ENTRIES + 9}") == _CACHE_MAX_ENTRIES + 9

    def test_cache_get_returns_isolated_copy(self):
        """写入方与读取方改写自己持有的对象都不影响缓存内容。"""
        clear_llm_cache()
        payload = {"latex": "x", "changes": ["a"]}
        cache_put("isolated", payload)
        payload["latex"] = "polluted"  # 写入方随后改写

        got = cache_get("isolated")
        got["changes"].append("b")  # 读取方随后改写副本

        assert cache_get("isolated") == {"latex": "x", "changes": ["a"]}

    def test_key_sensitive_to_message_content(self):
        a = build_cache_key("m", [{"role": "user", "content": "abc"}])
        b = build_cache_key("m", [{"role": "user", "content": "abd"}])
        assert a != b

    def test_key_stable_regardless_of_kwarg_order(self):
        a = build_cache_key("m", _MESSAGES, temperature=0.3, max_tokens=128)
        b = build_cache_key("m", _MESSAGES, max_tokens=128, temperature=0.3)
        assert a == b

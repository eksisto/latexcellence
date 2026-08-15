"""
LLMClient 上下文窗口探测与缓存行为测试。

覆盖：
    - 显式配置 context_window_tokens 优先，跳过探测
    - /models 探测解析 context_length / max_model_len / :latest 后缀
    - 探测失败 → 内置模型表 → 默认值
    - 错误信息学习并缓存
    - 缓存按 (base_url, model) 命中，reset() 后失效
    - 探测失败不抛出
"""

import json
from unittest.mock import patch

import pytest

from backend.core.config import get_config
from backend.core.llm_client import LLMClient


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.asyncio
async def test_get_context_window_explicit_config_skips_probe(monkeypatch):
    monkeypatch.setattr(get_config().llm, "context_window_tokens", 999)
    client = LLMClient()
    with patch.object(client, "_probe_context_window_sync", return_value=777) as probe:
        assert await client.get_context_window() == 999
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_get_context_window_caches_probe_result(monkeypatch):
    client = LLMClient()
    with patch.object(
        client, "_probe_context_window_sync", return_value=123456
    ) as probe:
        assert await client.get_context_window() == 123456
        assert await client.get_context_window() == 123456
    assert probe.call_count == 1


def test_probe_parses_max_model_len_with_probe_url(monkeypatch):
    monkeypatch.setattr(get_config().llm, "models_probe_url", "http://direct:8000/v1")
    monkeypatch.setattr(get_config().llm, "api_key", "test-key")
    captured = {}

    def fake_urlopen(req, timeout=5.0):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _FakeResp({"data": [{"id": "deepseek-flash", "max_model_len": 131072}]})

    with patch("backend.core.llm_client.urllib.request.urlopen", fake_urlopen):
        window = LLMClient._probe_context_window_sync(
            base_url="http://direct:8000/v1/gateway",
            model="deepseek-flash",
            api_key="test-key",
            probe_url="http://direct:8000/v1",
        )

    assert window == 131072
    assert captured["url"] == "http://direct:8000/v1/models"
    assert captured["auth"] == "Bearer test-key"


def test_probe_cross_origin_omits_api_key():
    """探测地址与主服务不同源时不携带 API key，避免密钥泄漏。"""
    captured = {}

    def fake_urlopen(req, timeout=5.0):
        captured["auth"] = req.get_header("Authorization")
        return _FakeResp({"data": [{"id": "deepseek-flash", "max_model_len": 131072}]})

    with patch("backend.core.llm_client.urllib.request.urlopen", fake_urlopen):
        window = LLMClient._probe_context_window_sync(
            base_url="https://gateway/api/x",
            model="deepseek-flash",
            api_key="test-key",
            probe_url="http://direct:8000/v1",
        )

    assert window == 131072
    assert captured["auth"] is None


def test_probe_matches_model_with_latest_suffix():
    def fake_urlopen(req, timeout=5.0):
        return _FakeResp(
            {
                "data": [
                    {"id": "deepseek-flash:latest", "context_length": 65536},
                    {"id": "other", "context_length": 1000},
                ]
            }
        )

    with patch("backend.core.llm_client.urllib.request.urlopen", fake_urlopen):
        window = LLMClient._probe_context_window_sync(
            base_url="http://localhost:8000/v1",
            model="deepseek-flash",
            api_key="",
            probe_url="",
        )
    assert window == 65536


def test_probe_failure_returns_none():
    def fake_urlopen(req, timeout=5.0):
        raise OSError("connection refused")

    with patch("backend.core.llm_client.urllib.request.urlopen", fake_urlopen):
        window = LLMClient._probe_context_window_sync(
            base_url="http://localhost:8000/v1",
            model="deepseek-flash",
            api_key="",
            probe_url="",
        )
    assert window is None


@pytest.mark.asyncio
async def test_get_context_window_builtin_table_hit(monkeypatch):
    monkeypatch.setattr(get_config().llm, "model", "gpt-5.6")
    client = LLMClient()
    with patch.object(client, "_probe_context_window_sync", return_value=None):
        assert await client.get_context_window() == 1_050_000


@pytest.mark.asyncio
async def test_get_context_window_default_fallback(monkeypatch):
    monkeypatch.setattr(get_config().llm, "model", "custom-unknown-model")
    client = LLMClient()
    with patch.object(client, "_probe_context_window_sync", return_value=None):
        assert await client.get_context_window() == 200000


@pytest.mark.asyncio
async def test_learn_context_window_from_error(monkeypatch):
    client = LLMClient()
    learned = client.learn_context_window_from_error(
        "This model's maximum context length is 131072 tokens"
    )
    assert learned == 131072
    with patch.object(client, "_probe_context_window_sync", return_value=None) as probe:
        assert await client.get_context_window() == 131072
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_reset_clears_context_window_cache(monkeypatch):
    client = LLMClient()
    with patch.object(client, "_probe_context_window_sync", return_value=100000):
        assert await client.get_context_window() == 100000
    client.reset()
    with patch.object(
        client, "_probe_context_window_sync", return_value=200000
    ) as probe:
        assert await client.get_context_window() == 200000
    assert probe.call_count == 1

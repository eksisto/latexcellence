"""模型路由器与档案化客户端池（multi-model-plan §5.1，P2 交付）。

覆盖：
    - 槽位两级解析路由（role 指派 → default 指派 → 内置档案）与未知角色报错
    - 池复用：同档案同实例、不同档案不同实例
    - 档案指纹（base_url / api_key / model）变化时重建内部 AsyncOpenAI
    - 热重载清空池（reset_client_pool + update_config_from_dict 接线）
    - get_llm_client() 薄别名语义（等价 get_client_for("default")）
    - default 档案并发行为与现状一致（Semaphore 容量与请求间隔继承顶层字段）
"""

import json
import os
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

from backend.core import config as cfg_module
from backend.core import llm_client as llm_client_module
from backend.core import model_router
from backend.core.config import BUILTIN_PROFILE_ID, AppConfig, LLMConfig
from backend.core.llm_client import LLMClient, get_llm_client
from backend.core.model_router import get_client_for, has_api_key_for, reset_client_pool

# 与 test_config_model_profiles 一致的顶层字段基线（语义保持不变）
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
    "request_interval": 0.5,
    "max_concurrency": 2,
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


@pytest.fixture(autouse=True)
def _clean_pool_and_config():
    """池与 config 全局状态在每个用例前后复位，避免跨用例串扰。"""
    reset_client_pool()
    yield
    reset_client_pool()
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


@pytest.fixture
def install_config(monkeypatch):
    """安装受控的内存配置：router 与 llm_client 的 get_config 同源。"""

    def _install(**llm_overrides) -> AppConfig:
        data = dict(TOP_LLM)
        data.update(llm_overrides)
        cfg = AppConfig(llm=LLMConfig(**data))
        monkeypatch.setattr(model_router, "get_config", lambda: cfg)
        monkeypatch.setattr(llm_client_module, "get_config", lambda: cfg)
        return cfg

    return _install


# ── 槽位路由与两级解析 ───────────────────────────────────────────────────


class TestRoleRouting:
    def test_role_assignment_routes_to_assigned_profile(self, install_config):
        install_config(
            profiles=[VISION_PROFILE, REASONING_PROFILE],
            assignments={"ocr": "vision-pro", "agent": "deepthink"},
        )
        assert get_client_for("ocr")._profile_id == "vision-pro"
        assert get_client_for("agent")._profile_id == "deepthink"

    def test_empty_role_falls_back_to_default_assignment(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"default": "vision-pro"},
        )
        assert get_client_for("writing_chat")._profile_id == "vision-pro"
        assert get_client_for("default")._profile_id == "vision-pro"

    def test_empty_everywhere_falls_back_to_builtin_profile(self, install_config):
        install_config(profiles=[VISION_PROFILE], assignments={"ocr": "vision-pro"})
        assert get_client_for("writing_chat")._profile_id == BUILTIN_PROFILE_ID

    def test_unknown_role_raises_value_error(self, install_config):
        install_config()
        with pytest.raises(ValueError, match="未知"):
            get_client_for("nonexistent_role")

    def test_get_llm_client_alias_equals_default_role(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"default": "vision-pro"},
        )
        assert get_llm_client() is get_client_for("default")


# ── 客户端池 ────────────────────────────────────────────────────────────


class TestClientPool:
    def test_same_profile_reuses_same_instance(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        assert get_client_for("ocr") is get_client_for("ocr")

    def test_roles_resolving_to_same_profile_share_instance(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        # 两个空槽位都两级回退到内置档案 → 同一池条目
        builtin_a = get_client_for("writing_chat")
        builtin_b = get_client_for("terminology_check")
        assert builtin_a is builtin_b
        assert builtin_a._profile_id == BUILTIN_PROFILE_ID

    def test_distinct_profiles_get_distinct_instances(self, install_config):
        install_config(
            profiles=[VISION_PROFILE, REASONING_PROFILE],
            assignments={"ocr": "vision-pro", "agent": "deepthink"},
        )
        assert get_client_for("ocr") is not get_client_for("agent")

    def test_reset_client_pool_clears_all_entries(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        first_ocr = get_client_for("ocr")
        first_default = get_client_for("default")
        reset_client_pool()
        assert get_client_for("ocr") is not first_ocr
        assert get_client_for("default") is not first_default


# ── 指纹惰性校验（双保险）────────────────────────────────────────────────


class TestFingerprintValidation:
    def test_fingerprint_change_rebuilds_async_openai(self, install_config):
        cfg = install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("ocr")
        first = client.client
        assert first.api_key == "key-vision"
        assert str(first.base_url).rstrip("/") == "https://vision.example.com/v1"

        # 同一池条目、未清池：档案指纹（api_key）变化 → 重建内部 AsyncOpenAI
        cfg.llm.profiles[0].api_key = "key-rotated"
        second = client.client
        assert second is not first
        assert second.api_key == "key-rotated"

        # 指纹未再变化 → 复用同一实例
        assert client.client is second

    def test_fingerprint_change_via_model_and_base_url(self, install_config):
        cfg = install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("ocr")
        first = client.client
        cfg.llm.profiles[0].model = "vision-model-v2"
        cfg.llm.profiles[0].base_url = "https://vision2.example.com/v1"
        second = client.client
        assert second is not first
        assert str(second.base_url).rstrip("/") == "https://vision2.example.com/v1"


# ── 热重载接线 ──────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, data: dict) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def test_update_config_from_dict_clears_pool(tmp_path):
    """热重载接线：/api/config 保存路径（update_config_from_dict）清空整个池。"""
    path = _write_config(tmp_path, {"llm": dict(TOP_LLM)})
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg_module.load_config(path)
        first = get_client_for("default")
        assert first._profile_id == BUILTIN_PROFILE_ID
        cfg_module.update_config_from_dict({"llm": {"model": "changed-model"}})
        second = get_client_for("default")
    assert second is not first
    assert cfg_module.get_config().llm.model == "changed-model"


# ── default 档案并发行为与现状一致（继承顶层字段）────────────────────────


class TestConcurrencyInheritance:
    def test_default_profile_resolves_top_level_fields(self, install_config):
        cfg = install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("default")
        resolved = client._resolve_cfg()
        assert resolved is cfg.llm  # 内置档案直接使用顶层配置视图
        assert resolved.max_concurrency == TOP_LLM["max_concurrency"]
        assert resolved.request_interval == TOP_LLM["request_interval"]
        assert resolved.temperature == TOP_LLM["temperature"]
        assert resolved.max_tokens == TOP_LLM["max_tokens"]

    def test_default_profile_semaphore_capacity_matches_top_level(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("default")
        client._get_semaphore()
        assert client._semaphore_capacity == TOP_LLM["max_concurrency"]

    def test_profile_override_semaphore_capacity(self, install_config):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("ocr")
        resolved = client._resolve_cfg()
        assert resolved.max_concurrency == 2
        assert resolved.request_interval == 0.5
        client._get_semaphore()
        assert client._semaphore_capacity == 2

    def test_resolved_max_concurrency_public_accessor(self, install_config):
        """批次编排的外部信号量经公共方法取容量（与内部信号量同口径）。"""
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        # 档案覆盖项生效
        assert get_client_for("ocr").resolved_max_concurrency() == 2
        # 未覆盖档案两级回退继承顶层
        assert get_client_for("typo_check").resolved_max_concurrency() == (
            TOP_LLM["max_concurrency"]
        )

    @pytest.mark.asyncio
    async def test_rate_limit_uses_resolved_interval(self, install_config, monkeypatch):
        install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = get_client_for("ocr")
        delays: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(llm_client_module.asyncio, "sleep", fake_sleep)
        await client._rate_limit()  # 首次调用仅记录时间戳
        await client._rate_limit()  # 第二次按档案间隔等待
        # 实际等待略小于间隔（计算经过时间的开销），量级必须一致
        assert delays and 0.4 < delays[-1] <= 0.5


# ── LLMClient 档案化基础 ────────────────────────────────────────────────


class TestLLMClientProfileAwareness:
    def test_constructor_defaults_to_no_profile(self):
        client = LLMClient()
        assert client._profile_id is None

    def test_profile_re_resolved_from_config_each_request(self, install_config):
        """档案对象按 profile_id 每次实时从 config 重新解析（防热重载旧档）。"""
        cfg = install_config(
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        client = LLMClient(profile_id="vision-pro")
        assert client._resolve_cfg().temperature == 0.1
        cfg.llm.profiles[0].temperature = 0.9
        assert client._resolve_cfg().temperature == 0.9

    def test_missing_profile_falls_back_to_top_level(self, install_config):
        """档案被删除但池未清（防御路径）：回退顶层配置，功能不中断。"""
        install_config()
        client = LLMClient(profile_id="ghost")
        resolved = client._resolve_cfg()
        assert resolved.model == TOP_LLM["model"]
        assert resolved.api_key == TOP_LLM["api_key"]

    def test_builtin_profile_id_uses_top_level_view(self, install_config):
        install_config()
        client = LLMClient(profile_id=BUILTIN_PROFILE_ID)
        assert client._resolve_cfg().model == TOP_LLM["model"]


# ── 池化客户端与注入客户端互不干扰 ────────────────────────────────────────


class TestBackwardCompat:
    def test_injected_client_bypasses_fingerprint_check(self, install_config):
        """直接注入 _client（测试路径，指纹为 None）不被指纹校验重建。"""
        install_config()
        client = LLMClient()
        injected = MagicMock()
        client._client = injected
        assert client.client is injected

    def test_reset_releases_injected_client(self):
        client = LLMClient()
        client._client = MagicMock()
        client.reset()
        assert client._client is None
        assert client._client_fingerprint is None


# ── API-key 门禁按槽位路由判断（调用点预检统一入口）──────────────────────


class TestApiKeyGate:
    def test_profile_only_key_resolves_through_assignment(self, install_config):
        """顶层留空、仅档案配 Key（合法配置）：门禁必须按路由结果判定为有 Key。

        回归锁定：历史门禁只看顶层 llm.api_key，会把该配置误判为未配置 Key
        而让所有功能走 no-key 降级路径。
        """
        install_config(
            api_key="",
            profiles=[VISION_PROFILE, REASONING_PROFILE],
            assignments={"ocr": "vision-pro", "agent": "deepthink"},
        )
        assert has_api_key_for("ocr") is True
        assert has_api_key_for("agent") is True

    def test_profile_only_key_builtin_fallback_is_false(self, install_config):
        """顶层留空且槽位两级回退到内置档案：门禁判定为无 Key（正确降级）。"""
        install_config(
            api_key="",
            profiles=[VISION_PROFILE],
            assignments={"ocr": "vision-pro"},
        )
        assert has_api_key_for("writing_chat") is False
        assert has_api_key_for("default") is False

    def test_top_level_key_used_via_builtin_profile(self, install_config):
        """无档案指派时内置档案继承顶层 Key：门禁判定为有 Key。"""
        install_config()
        assert has_api_key_for("default") is True

    def test_two_level_fallback_uses_default_slot_key(self, install_config):
        """空槽位跟随 default 槽指派档案的 Key（两级解析语义）。"""
        install_config(
            api_key="",
            profiles=[VISION_PROFILE],
            assignments={"default": "vision-pro"},
        )
        assert has_api_key_for("terminology_check") is True

    def test_empty_profile_key_falls_back_to_default_slot(self, install_config):
        """被指派档案 Key 为空串（免鉴权端点）时，门禁判定为无 Key。"""
        profile_without_key = dict(VISION_PROFILE, api_key="")
        install_config(
            api_key="key-top",
            profiles=[profile_without_key],
            assignments={"ocr": "vision-pro", "default": "vision-pro"},
        )
        assert has_api_key_for("ocr") is False
        # 未指派槽位两级回退同样落到该档案
        assert has_api_key_for("typo_check") is False

    def test_unknown_role_raises_value_error(self, install_config):
        install_config()
        with pytest.raises(ValueError, match="未知"):
            has_api_key_for("nonexistent_role")

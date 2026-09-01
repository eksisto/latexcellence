"""模型档案（profiles）与槽位指派（assignments）配置：自动迁移 + 能力硬校验。

对应 multi-model-plan §4/§5.3 的 P1 交付：
- profiles 为空时读取侧合成内置默认档案（不回写文件），全部槽位解析落到它；
- 校验门禁：档案 id 唯一且格式合法、assignments 引用存在、OCR 槽必须 vision；
- 存量兼容：旧单模型结构（仅顶层字段）加载行为不变；
- 两级解析：role 指派优先 → default 指派 → 内置档案。
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from backend.core import config as cfg_module
from backend.core.config import (
    BUILTIN_PROFILE_ID,
    LLM_ROLE_KEYS,
    LLMConfig,
    ModelProfile,
)

# 旧单模型结构的顶层字段（语义须保持不变）
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
}

REASONING_PROFILE = {
    "id": "deepthink",
    "name": "深度思考模型",
    "base_url": "https://think.example.com/v1",
    "api_key": "key-think",
    "model": "think-model",
    "capabilities": ["reasoning"],
}

SAMPLE_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8000, "dev_mode": True},
    "llm": dict(TOP_LLM),
    "logging": {"level": "INFO", "dir": "logs/"},
}


@pytest.fixture(autouse=True)
def _reset_config():
    """每次测试后重置 config 模块全局状态"""
    yield
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def _llm(**overrides) -> LLMConfig:
    data = dict(TOP_LLM)
    data.update(overrides)
    return LLMConfig(**data)


def _write_config(tmp_path, data) -> str:
    path = tmp_path / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return str(path)


class TestBuiltinProfileMigration:
    """自动迁移：profiles 为空时合成内置档案；profiles 非空时不合成。"""

    def test_empty_profiles_synthesize_builtin(self):
        llm = _llm()
        profiles = llm.effective_profiles()
        assert [p.id for p in profiles] == [BUILTIN_PROFILE_ID]
        builtin = profiles[0]
        assert builtin.name == "全局默认模型"
        # 内置档案视为全能力，保证旧配置迁移后 OCR 门禁不产生新失败
        assert builtin.capabilities == ["vision", "reasoning"]
        # 取值即顶层字段
        assert builtin.base_url == TOP_LLM["base_url"]
        assert builtin.api_key == TOP_LLM["api_key"]
        assert builtin.model == TOP_LLM["model"]
        assert builtin.temperature == TOP_LLM["temperature"]
        assert builtin.max_tokens == TOP_LLM["max_tokens"]
        assert builtin.request_interval == TOP_LLM["request_interval"]
        assert builtin.max_concurrency == TOP_LLM["max_concurrency"]
        assert builtin.context_window_tokens == TOP_LLM["context_window_tokens"]
        assert builtin.models_probe_url == TOP_LLM["models_probe_url"]
        assert builtin.extra_body == TOP_LLM["extra_body"]

    def test_empty_profiles_all_roles_resolve_to_builtin(self):
        llm = _llm()
        for role in LLM_ROLE_KEYS:
            assert llm.resolve_role(role).id == BUILTIN_PROFILE_ID

    def test_migration_does_not_write_back(self):
        """合成仅在读取侧：profiles 字段与 model_dump 保持为空（不回写文件）。"""
        llm = _llm()
        llm.effective_profiles()
        llm.resolve_role("ocr")
        assert llm.profiles == []
        assert llm.model_dump()["profiles"] == []

    def test_profiles_present_not_synthesized(self):
        llm = _llm(profiles=[VISION_PROFILE, REASONING_PROFILE])
        assert [p.id for p in llm.effective_profiles()] == [
            "vision-pro",
            "deepthink",
        ]

    def test_builtin_synthesis_is_pure(self):
        """合成是纯函数：每次调用返回新对象，互不影响。"""
        llm = _llm()
        first = llm.builtin_default_profile()
        second = llm.builtin_default_profile()
        assert first is not second
        first.extra_body["injected"] = True
        assert llm.builtin_default_profile().extra_body == {}


class TestValidationGates:
    """校验门禁：非法配置在加载 / 保存时一律拒绝（ValidationError）。"""

    def test_ocr_slot_non_vision_profile_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _llm(
                profiles=[REASONING_PROFILE],
                assignments={"ocr": "deepthink"},
            )
        message = str(exc_info.value)
        assert "ocr" in message
        assert "vision" in message

    def test_ocr_slot_via_default_fallback_non_vision_rejected(self):
        """ocr 槽留空时两级回退到 default 指派，非 vision 同样被拒。"""
        with pytest.raises(ValidationError) as exc_info:
            _llm(
                profiles=[REASONING_PROFILE],
                assignments={"default": "deepthink"},
            )
        assert "vision" in str(exc_info.value)

    def test_assignment_to_missing_profile_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _llm(
                profiles=[VISION_PROFILE],
                assignments={"writing_chat": "ghost"},
            )
        message = str(exc_info.value)
        assert "writing_chat" in message
        assert "ghost" in message

    def test_duplicate_profile_id_rejected(self):
        duplicate = dict(VISION_PROFILE, name="另一个视觉模型")
        with pytest.raises(ValidationError) as exc_info:
            _llm(profiles=[VISION_PROFILE, duplicate])
        assert "重复" in str(exc_info.value)

    @pytest.mark.parametrize("bad_id", ["deep think", "深度思考", "a.b", ""])
    def test_invalid_profile_id_format_rejected(self, bad_id):
        with pytest.raises(ValidationError):
            _llm(profiles=[dict(VISION_PROFILE, id=bad_id)])

    def test_reserved_builtin_id_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _llm(profiles=[dict(VISION_PROFILE, id=BUILTIN_PROFILE_ID)])
        assert "保留" in str(exc_info.value)

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _llm(profiles=[dict(VISION_PROFILE, capabilities=["telepathy"])])
        assert "telepathy" in str(exc_info.value)

    def test_unknown_assignment_role_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _llm(assignments={"unknown_slot": "vision-pro"})
        message = str(exc_info.value)
        assert "unknown_slot" in message
        assert "ocr" in message  # 错误消息给出支持的槽位列表

    def test_missing_profile_required_field_rejected(self):
        broken = {k: v for k, v in VISION_PROFILE.items() if k != "base_url"}
        with pytest.raises(ValidationError):
            _llm(profiles=[broken])

    def test_extra_profile_field_rejected(self):
        """档案字段拼写错误直接报错（extra=forbid），防止静默失效。"""
        with pytest.raises(ValidationError):
            _llm(profiles=[dict(VISION_PROFILE, capability=["vision"])])

    def test_non_string_assignment_value_rejected(self):
        with pytest.raises(ValidationError):
            _llm(assignments={"ocr": 123})

    def test_assignment_value_whitespace_stripped(self):
        """指派值去首尾空白，与档案 id 规范化对齐（手改配置不产生误报）。"""
        llm = _llm(profiles=[VISION_PROFILE], assignments={"ocr": "  vision-pro  "})
        assert llm.assignments["ocr"] == "vision-pro"
        assert llm.resolve_role("ocr").id == "vision-pro"

    def test_assignment_to_reserved_builtin_id_rejected(self):
        """assignments 引用内置保留 id 同样拒绝（内置档案不可被指派）。"""
        with pytest.raises(ValidationError):
            _llm(profiles=[VISION_PROFILE], assignments={"ocr": BUILTIN_PROFILE_ID})

    def test_valid_vision_assignment_passes(self):
        llm = _llm(
            profiles=[VISION_PROFILE, REASONING_PROFILE],
            assignments={"ocr": "vision-pro", "agent": "deepthink"},
        )
        assert llm.assignments["ocr"] == "vision-pro"
        assert llm.assignments["agent"] == "deepthink"


class TestAssignmentsNormalization:
    """指派表规范化：固定槽位键集合、缺失补空串、null 视为空串。"""

    def test_default_assignments_all_empty(self):
        llm = _llm()
        assert llm.assignments == {role: "" for role in LLM_ROLE_KEYS}
        assert len(llm.assignments) == len(LLM_ROLE_KEYS)

    def test_partial_assignments_filled(self):
        llm = _llm(profiles=[VISION_PROFILE], assignments={"ocr": "vision-pro"})
        assert llm.assignments["ocr"] == "vision-pro"
        assert llm.assignments["default"] == ""
        assert len(llm.assignments) == len(LLM_ROLE_KEYS)

    def test_null_assignment_value_coerced_to_empty(self):
        llm = _llm(assignments={"ocr": None})
        assert llm.assignments["ocr"] == ""

    def test_absent_assignments_uses_default_factory(self):
        llm = LLMConfig(**TOP_LLM)
        assert llm.assignments == {role: "" for role in LLM_ROLE_KEYS}


class TestTwoLevelResolution:
    """两级解析：role 指派优先 → default 指派 → 内置档案。"""

    def _llm_with_profiles(self, **assignments) -> LLMConfig:
        return _llm(
            profiles=[VISION_PROFILE, REASONING_PROFILE],
            assignments=dict(assignments),
        )

    def test_role_assignment_takes_priority(self):
        llm = self._llm_with_profiles(default="deepthink", ocr="vision-pro")
        assert llm.resolve_role("ocr").id == "vision-pro"

    def test_empty_role_falls_back_to_default_assignment(self):
        # default 指派 vision 档案（非 vision 会被 OCR 门禁拒绝整个配置）
        llm = self._llm_with_profiles(default="vision-pro")
        assert llm.resolve_role("writing_chat").id == "vision-pro"

    def test_empty_both_falls_back_to_builtin(self):
        llm = self._llm_with_profiles()
        resolved = llm.resolve_role("writing_chat")
        assert resolved.id == BUILTIN_PROFILE_ID
        assert resolved.model == TOP_LLM["model"]

    def test_default_slot_resolution(self):
        llm = self._llm_with_profiles(default="vision-pro")
        assert llm.resolve_role("default").id == "vision-pro"
        empty = self._llm_with_profiles()
        assert empty.resolve_role("default").id == BUILTIN_PROFILE_ID

    def test_ocr_via_default_vision_profile_passes(self):
        llm = self._llm_with_profiles(default="vision-pro")
        assert llm.resolve_role("ocr").id == "vision-pro"

    def test_unknown_role_raises_value_error(self):
        llm = self._llm_with_profiles()
        with pytest.raises(ValueError, match="未知"):
            llm.resolve_role("nope")


class TestProfileOverrideFields:
    """档案可选覆盖项：缺省继承顶层（None / 0 / {}），边界约束与顶层一致。"""

    def test_override_defaults_mean_inherit(self):
        profile = ModelProfile(**VISION_PROFILE)
        assert profile.temperature is None
        assert profile.max_tokens is None
        assert profile.request_interval is None
        assert profile.max_concurrency is None
        assert profile.context_window_tokens == 0
        assert profile.models_probe_url == ""
        assert profile.extra_body == {}

    def test_override_bounds_enforced(self):
        with pytest.raises(ValidationError):
            ModelProfile(**VISION_PROFILE, max_tokens=0)
        with pytest.raises(ValidationError):
            ModelProfile(**VISION_PROFILE, max_concurrency=0)
        with pytest.raises(ValidationError):
            ModelProfile(**VISION_PROFILE, request_interval=-1)
        with pytest.raises(ValidationError):
            ModelProfile(**VISION_PROFILE, temperature=3)
        with pytest.raises(ValidationError):
            # 上下文窗口与顶层语义一致：非负（0 = 自动探测/继承）
            ModelProfile(**VISION_PROFILE, context_window_tokens=-1)

    def test_override_values_roundtrip(self):
        profile = ModelProfile(
            **VISION_PROFILE,
            temperature=0.7,
            max_tokens=8192,
            extra_body={"reasoning_effort": "low"},
        )
        assert profile.temperature == 0.7
        assert profile.max_tokens == 8192
        assert profile.extra_body == {"reasoning_effort": "low"}

    def test_empty_capabilities_allowed(self):
        base = {k: v for k, v in VISION_PROFILE.items() if k != "capabilities"}
        profile = ModelProfile(**base, capabilities=[])
        assert profile.capabilities == []

    def test_text_fields_whitespace_stripped(self):
        """展示名 / base_url / model 加载期去首尾空白，避免请求期才报错。"""
        profile = ModelProfile(
            **{
                **VISION_PROFILE,
                "name": "  视觉模型  ",
                "base_url": "  https://vision.example.com/v1  ",
                "model": " vision-model ",
            }
        )
        assert profile.name == "视觉模型"
        assert profile.base_url == "https://vision.example.com/v1"
        assert profile.model == "vision-model"

    def test_blank_name_rejected(self):
        with pytest.raises(ValidationError):
            ModelProfile(**{**VISION_PROFILE, "name": "   "})


class TestLegacyConfigCompat:
    """存量兼容：旧单模型结构（仅顶层字段）经 load_config 加载行为不变。"""

    def test_legacy_llm_load_semantics_unchanged(self, tmp_path):
        path = _write_config(tmp_path, SAMPLE_CONFIG)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
        llm = cfg_module.get_config().llm
        # 顶层字段语义不变
        for key, value in TOP_LLM.items():
            assert getattr(llm, key) == value
        # 新结构为空 + 迁移视图可用（OCR 解析到内置 vision 档案，不产生新失败）
        assert llm.profiles == []
        assert llm.assignments == {role: "" for role in LLM_ROLE_KEYS}
        assert llm.resolve_role("ocr").capabilities == ["vision", "reasoning"]

    def test_legacy_config_save_roundtrip_keeps_top_level(self, tmp_path):
        """save_config 落盘后顶层字段值不变（新增键以空值自然落盘）。"""
        path = _write_config(tmp_path, SAMPLE_CONFIG)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
            cfg_module.save_config()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in TOP_LLM.items():
            assert data["llm"][key] == value
        assert data["llm"]["profiles"] == []

    def test_new_structure_loads_end_to_end(self, tmp_path):
        data = {
            **SAMPLE_CONFIG,
            "llm": {
                **TOP_LLM,
                "profiles": [VISION_PROFILE, REASONING_PROFILE],
                "assignments": {"ocr": "vision-pro", "agent": "deepthink"},
            },
        }
        path = _write_config(tmp_path, data)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
        llm = cfg_module.get_config().llm
        assert llm.resolve_role("ocr").id == "vision-pro"
        assert llm.resolve_role("agent").id == "deepthink"
        assert llm.resolve_role("typo_check").id == BUILTIN_PROFILE_ID
        assert [p.id for p in llm.effective_profiles()] == [
            "vision-pro",
            "deepthink",
        ]

    def test_invalid_new_structure_rejected_on_load(self, tmp_path):
        """启动加载即拒绝非法指派（硬校验门禁），而非运行期失败。"""
        data = {
            **SAMPLE_CONFIG,
            "llm": {
                **TOP_LLM,
                "profiles": [REASONING_PROFILE],
                "assignments": {"ocr": "deepthink"},
            },
        }
        path = _write_config(tmp_path, data)
        with mock.patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError):
                cfg_module.load_config(path)


class TestConfigExampleConsistency:
    """config.example.json 的 llm 节与新结构一致且能通过校验门禁。"""

    def test_example_llm_section_valid(self):
        project_root = Path(__file__).resolve().parent.parent
        with open(project_root / "config.example.json", encoding="utf-8") as f:
            data = json.load(f)
        llm = LLMConfig(**data["llm"])
        assert {p.id for p in llm.effective_profiles()} >= {
            "deepthink",
            "vision-pro",
        }
        # 示例未指派任何槽位：全部解析回内置默认档案（拷贝即用，行为同旧版）
        for role in LLM_ROLE_KEYS:
            assert llm.resolve_role(role).id == BUILTIN_PROFILE_ID

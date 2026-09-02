"""逐段检查基类 LLM 槽位角色钩子回归（multi-model-plan §6，P3 交付）。

覆盖：
    - 基类默认角色为 default；五个子类各自声明独立槽位
    - 子类声明的槽位经模型路由器（P2 两级解析）路由到指派档案
    - 基类批量检查流程按"子类 llm_role"实时取客户端（而非硬编码 default）
    - writing_chat 消费链（_deps 再导出点 / context 探测）固定 writing_chat 槽位
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core import config as cfg_module
from backend.core import model_router
from backend.core.base_paragraph_check import BaseParagraphCheckService
from backend.core.config import BUILTIN_PROFILE_ID, AppConfig, LLMConfig
from backend.core.model_router import get_client_for, reset_client_pool
from backend.modules.code_review.service import CodeReviewService
from backend.modules.expression_opt.service import ExpressionOptService
from backend.modules.paragraph_translate.service import ParagraphTranslateService
from backend.modules.terminology_check.service import TerminologyCheckService
from backend.modules.typo_check.service import TypoCheckService

# 与 test_model_router 一致的顶层字段基线
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

FAST_PROFILE = {
    "id": "fast-model",
    "name": "快速模型",
    "base_url": "https://fast.example.com/v1",
    "api_key": "key-fast",
    "model": "fast-model",
}

REVIEW_PROFILE = {
    "id": "review-model",
    "name": "审查模型",
    "base_url": "https://review.example.com/v1",
    "api_key": "key-review",
    "model": "review-model",
}

# 子类 → 期望槽位（与 config.LLM_ROLE_KEYS 对齐）
ROLE_OF_SERVICE = [
    (TypoCheckService, "typo_check"),
    (ExpressionOptService, "expression_opt"),
    (CodeReviewService, "code_review"),
    (TerminologyCheckService, "terminology_check"),
    (ParagraphTranslateService, "paragraph_translate"),
]


@pytest.fixture(autouse=True)
def _clean_pool_and_config():
    """池与 config 全局状态在每个用例前后复位，避免跨用例串扰。"""
    reset_client_pool()
    yield
    reset_client_pool()
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def _install_config(monkeypatch, **llm_overrides) -> AppConfig:
    """安装受控的内存配置（get_client_for 内部 get_config 与路由器同源）。"""
    data = dict(TOP_LLM)
    data.update(llm_overrides)
    cfg = AppConfig(llm=LLMConfig(**data))
    monkeypatch.setattr(model_router, "get_config", lambda: cfg)
    return cfg


# ── 角色声明 ─────────────────────────────────────────────────────────────


class TestRoleDeclaration:
    def test_base_class_defaults_to_default_role(self):
        assert BaseParagraphCheckService.llm_role == "default"

    @pytest.mark.parametrize(("svc_cls", "role"), ROLE_OF_SERVICE)
    def test_subclass_declares_its_own_role(self, svc_cls, role):
        assert svc_cls.llm_role == role


# ── 槽位路由（经 P2 两级解析）────────────────────────────────────────────


class TestRoleRouting:
    def test_subclass_roles_route_to_assigned_profiles(self, monkeypatch):
        """有指派时：各子类槽位解析到各自档案（逐段四槽共用快速档案，
        code_review 独立档案）。"""
        _install_config(
            monkeypatch,
            profiles=[FAST_PROFILE, REVIEW_PROFILE],
            assignments={
                "typo_check": "fast-model",
                "expression_opt": "fast-model",
                "terminology_check": "fast-model",
                "paragraph_translate": "fast-model",
                "code_review": "review-model",
            },
        )
        for svc_cls, _ in ROLE_OF_SERVICE:
            client = get_client_for(svc_cls().llm_role)
            expected = "review-model" if svc_cls is CodeReviewService else "fast-model"
            assert client._profile_id == expected

    def test_unassigned_roles_fall_back_to_builtin_profile(self, monkeypatch):
        """无指派时：全部子类槽位两级回退到内置默认档案（功能不中断）。"""
        _install_config(monkeypatch, profiles=[FAST_PROFILE])
        for svc_cls, _ in ROLE_OF_SERVICE:
            assert get_client_for(svc_cls.llm_role)._profile_id == BUILTIN_PROFILE_ID


# ── 基类取客户端走子类槽位（非硬编码 default）────────────────────────────


class _MinimalCheckService(BaseParagraphCheckService):
    """最小子类：无 marker 保护（跳过上下文存储），仅验证取客户端的槽位。"""

    llm_role = "expression_opt"
    use_marker_protection = False

    def _get_system_prompt(self) -> str:
        return ""

    def _build_user_content(self, text: str) -> str:
        return text

    def _get_model_cls(self):
        return None

    def _get_llm_params(self) -> dict:
        return {}

    def _get_batch_size(self) -> int:
        return 1

    def _build_success_result(
        self, idx, validated, original_text, file_path, plain_text
    ):
        return {}


class TestBatchFetchUsesSubclassRole:
    @pytest.mark.asyncio
    async def test_run_batch_check_fetches_client_with_subclass_role(self, monkeypatch):
        captured_roles = []
        sentinel_client = MagicMock(name="llm_client")
        # 批次编排信号量容量取自客户端合并视图（含档案覆盖项）
        sentinel_client.resolved_max_concurrency.return_value = 1

        def fake_get_client_for(role):
            captured_roles.append(role)
            return sentinel_client

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "key-test"
        mock_cfg.llm.max_concurrency = 1
        mock_cfg.llm.request_interval = 0
        monkeypatch.setattr(
            "backend.core.base_paragraph_check.get_client_for",
            fake_get_client_for,
        )
        monkeypatch.setattr(
            "backend.core.base_paragraph_check.get_config", lambda: mock_cfg
        )

        svc = _MinimalCheckService("minimal_check")
        svc._get_files = lambda *args, **kwargs: ["main.tex"]
        svc._collect_para_data = lambda *args, **kwargs: []

        result = await svc._run_batch_check("main.tex")

        assert captured_roles == ["expression_opt"]
        assert result["results"] == []
        assert result["file_path"] == "main.tex"


# ── writing_chat 消费链固定槽位（_deps 再导出点 / context 上下文探测）──────


class TestWritingChatChainRole:
    def test_deps_get_llm_client_routes_to_writing_chat_role(self, monkeypatch):
        """_deps 再导出点（4 个 consumer 统一 patch 点）固定走 writing_chat 槽。"""
        from backend.modules.writing_chat.service import _deps

        sentinel = MagicMock(name="llm_client")
        captured_roles: list[str] = []

        def fake_get_client_for(role: str):
            captured_roles.append(role)
            return sentinel

        monkeypatch.setattr(_deps, "get_client_for", fake_get_client_for)

        assert _deps.get_llm_client() is sentinel
        assert captured_roles == ["writing_chat"]

    @pytest.mark.asyncio
    async def test_context_window_probe_routes_to_writing_chat_role(self, monkeypatch):
        """上下文窗口动态探测（should_compress_async）固定走 writing_chat 槽。"""
        from backend.modules.writing_chat.context import ConversationContext

        ctx = ConversationContext("sess-roles", 0.0, 0.0, [], 0, "main.tex")

        mock_cfg = MagicMock()
        mock_cfg.features.writing_chat.compression_threshold_tokens = 0
        monkeypatch.setattr(
            "backend.modules.writing_chat.context.get_config", lambda: mock_cfg
        )

        sentinel = MagicMock(name="llm_client")
        sentinel.get_context_window = AsyncMock(return_value=123456)
        captured_roles: list[str] = []

        def fake_get_client_for(role: str):
            captured_roles.append(role)
            return sentinel

        monkeypatch.setattr(
            "backend.core.model_router.get_client_for",
            fake_get_client_for,
        )

        ctx.should_compress = MagicMock(return_value=True)

        assert await ctx.should_compress_async() is True
        assert captured_roles == ["writing_chat"]
        sentinel.get_context_window.assert_awaited_once()

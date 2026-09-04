"""POST /api/llm/test 模型连通性测试端点（deploy-followups-plan §4.4，阶段四）。

覆盖：
    - 请求校验：profile_id / role 二选一（都传或都不传 → 422）、未知槽位
      422、未知档案 404 not_found
    - 双粒度解析：role 经两级解析（指派槽 → default 槽 → 内置档案）验证
      "实际生效档案"；profile_id 直取档案本身
    - 执行语义：chat 以最小补全调用（"ping" / max_tokens=1 / max_retries=1，
      快速失败）；响应含 ok / latency_ms / model / profile_id / role?
    - 失败三态：缺 key（api_key_missing）、超时（llm_test_timeout）、
      上游 HTTP 错误（llm_test_upstream + status）与未知错误兜底
      （llm_test_failed）；错误响应绝不泄露密钥材料
    - 掩码协议回归：测试路径只读配置——内存 api_key 不变、磁盘字节级不变、
      掩码串绝不落盘 / 外发
    - 多用户鉴权与治理豁免：匿名 401、普通用户 403（真实计费属管理面）、
      管理员 200 且不消耗个人限流窗口 / 并发租约（admission_exempt_scope）

所有用例 Mock LLMClient.chat，严禁真实网络。
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest import mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import APIStatusError

from backend.core import config as cfg_module
from backend.core.config import BUILTIN_PROFILE_ID, LLM_ROLE_KEYS
from backend.core.llm_client import LLMClient

# 与 test_llm_routes_api / test_config_model_profiles 一致的顶层字段基线
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
    "api_key": "vision-secret-1234",
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

NO_KEY_PROFILE = {
    "id": "no-key",
    "name": "未配 Key 档案",
    "base_url": "https://bare.example.com/v1",
    "api_key": "",
    "model": "bare-model",
    "capabilities": ["reasoning"],
}


@pytest.fixture(autouse=True)
def _reset_config():
    """每次测试后重置 config 模块全局状态。"""
    yield
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def _write_config(tmp_path: Path, llm_overrides: dict | None = None) -> str:
    data = {
        "server": {"host": "127.0.0.1", "port": 8000, "dev_mode": True},
        "llm": dict(TOP_LLM),
        "logging": {"level": "INFO", "dir": "logs/"},
    }
    if llm_overrides:
        data["llm"].update(llm_overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _load(tmp_path: Path, llm_overrides: dict | None = None):
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg_module.load_config(_write_config(tmp_path, llm_overrides))


def _mock_chat(return_value: str = "pong"):
    """Mock LLMClient.chat（类级，覆盖池内全部实例；严禁真实网络）。"""
    return mock.patch.object(
        LLMClient, "chat", new_callable=mock.AsyncMock, return_value=return_value
    )


# ── 请求校验：二选一 / 未知目标 ──────────────────────────────────────────


class TestLlmTestValidation:
    @pytest.mark.asyncio
    async def test_both_targets_rejected(self, tmp_path):
        """profile_id 与 role 同时传：422 语义错误（invalid_request）。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with pytest.raises(HTTPException) as exc_info:
            await test_llm_connectivity(
                LlmTestRequest(profile_id="deepthink", role="agent")
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_neither_target_rejected(self, tmp_path):
        """两者都不传：422 语义错误。"""
        _load(tmp_path)
        from main import LlmTestRequest, test_llm_connectivity

        with pytest.raises(HTTPException) as exc_info:
            await test_llm_connectivity(LlmTestRequest())
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_unknown_role_rejected(self, tmp_path):
        """未知槽位（不在 LLM_ROLE_KEYS）：422。"""
        _load(tmp_path)
        from main import LlmTestRequest, test_llm_connectivity

        assert "magic_slot" not in LLM_ROLE_KEYS  # 前置：测的确实是未知槽位
        with pytest.raises(HTTPException) as exc_info:
            await test_llm_connectivity(LlmTestRequest(role="magic_slot"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_unknown_profile_not_found(self, tmp_path):
        """profile_id 不存在：404 not_found（不静默回落顶层配置）。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with pytest.raises(HTTPException) as exc_info:
            await test_llm_connectivity(LlmTestRequest(profile_id="ghost"))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "not_found"


# ── 双粒度解析与执行语义 ─────────────────────────────────────────────────


class TestLlmTestResolution:
    @pytest.mark.asyncio
    async def test_role_falls_back_to_builtin(self, tmp_path):
        """role 粒度两级解析：全空指派 → 内置默认档案（default 回落）。"""
        _load(tmp_path)
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat() as chat_mock:
            resp = await test_llm_connectivity(LlmTestRequest(role="agent"))

        assert resp["ok"] is True
        assert isinstance(resp["latency_ms"], int) and resp["latency_ms"] >= 0
        assert resp["profile_id"] == BUILTIN_PROFILE_ID
        assert resp["model"] == TOP_LLM["model"]
        assert resp["role"] == "agent"
        assert "error" not in resp
        # 最小补全契约："ping" / max_tokens=1 / max_retries=1（快速失败）
        chat_mock.assert_awaited_once()
        kwargs = chat_mock.await_args.kwargs
        assert kwargs["messages"] == [{"role": "user", "content": "ping"}]
        assert kwargs["max_tokens"] == 1
        assert kwargs["max_retries"] == 1

    @pytest.mark.asyncio
    async def test_role_resolves_assigned_profile(self, tmp_path):
        """role 粒度：指派槽直接生效（agent → deepthink）。"""
        _load(
            tmp_path,
            {
                "profiles": [dict(VISION_PROFILE), dict(REASONING_PROFILE)],
                "assignments": {"default": "", "agent": "deepthink"},
            },
        )
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(LlmTestRequest(role="agent"))

        assert resp["ok"] is True
        assert resp["profile_id"] == "deepthink"
        assert resp["model"] == "think-model"
        assert resp["role"] == "agent"

    @pytest.mark.asyncio
    async def test_profile_id_tests_profile_itself(self, tmp_path):
        """profile_id 粒度：直测该档案本身，响应不含 role 字段。"""
        _load(
            tmp_path,
            {
                "profiles": [dict(VISION_PROFILE), dict(REASONING_PROFILE)],
                "assignments": {"default": "", "agent": "deepthink"},
            },
        )
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="vision-pro"))

        assert resp["ok"] is True
        assert resp["profile_id"] == "vision-pro"
        assert resp["model"] == "vision-model"
        assert "role" not in resp

    @pytest.mark.asyncio
    async def test_builtin_profile_id_allowed(self, tmp_path):
        """profile_id=__default__（内置档案）也可直测。"""
        _load(tmp_path)
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(
                LlmTestRequest(profile_id=BUILTIN_PROFILE_ID)
            )
        assert resp["ok"] is True
        assert resp["model"] == TOP_LLM["model"]

    @pytest.mark.asyncio
    async def test_response_has_no_secret_fields(self, tmp_path):
        """成功响应不含 api_key / base_url 等敏感字段与密钥材料。"""
        _load(tmp_path, {"profiles": [dict(VISION_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="vision-pro"))
        dumped = json.dumps(resp, ensure_ascii=False)
        assert "api_key" not in dumped
        assert "base_url" not in dumped
        assert VISION_PROFILE["api_key"] not in dumped


# ── 失败三态：缺 key / 超时 / 上游与未知错误 ─────────────────────────────


class TestLlmTestFailureModes:
    @pytest.mark.asyncio
    async def test_api_key_missing(self, tmp_path):
        """档案未配 Key：结构化 api_key_missing，且不发起任何调用。"""
        _load(tmp_path, {"profiles": [dict(NO_KEY_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat() as chat_mock:
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="no-key"))

        assert resp["ok"] is False
        assert resp["error"]["error"] == "api_key_missing"
        assert resp["latency_ms"] == 0
        assert resp["profile_id"] == "no-key"
        chat_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_classified(self, tmp_path):
        """超时（含 15s 硬上限路径的超时类异常）→ llm_test_timeout。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat() as chat_mock:
            chat_mock.side_effect = TimeoutError()
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="deepthink"))

        assert resp["ok"] is False
        assert resp["error"]["error"] == "llm_test_timeout"
        assert isinstance(resp["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_hard_timeout_enforced(self, tmp_path):
        """超时硬上限真实生效（回归）：挂起的 chat 被 asyncio.timeout 击穿。

        直接 mock chat 抛 TimeoutError 只覆盖异常分类，无法证明超时上限
        本身存在且生效——若实现遗漏 asyncio.timeout 包裹，挂起调用会无限
        等待。此处把上限 patch 为 50ms、chat 真实挂起 30s：端点必须提前
        返回 llm_test_timeout 而非等满挂起时长。
        """
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        async def hang_forever(self, **kwargs):
            await asyncio.sleep(30)
            return "never"  # pragma: no cover — 必须先被超时取消

        started = time.perf_counter()
        with (
            mock.patch.object(LLMClient, "chat", hang_forever),
            mock.patch("main._LLM_TEST_TIMEOUT_SECONDS", 0.05),
        ):
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="deepthink"))
        elapsed = time.perf_counter() - started

        assert resp["ok"] is False
        assert resp["error"]["error"] == "llm_test_timeout"
        # 未等满 30s 挂起时长（远小于它即返回），证明硬上限击穿了挂起调用
        assert elapsed < 5
        assert resp["latency_ms"] < 5000

    @pytest.mark.asyncio
    async def test_upstream_http_error_sanitized(self, tmp_path):
        """上游 4xx：llm_test_upstream + 有限状态码，响应体不透传。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        upstream_detail = "ups turbid secret-echo key-think leak"
        upstream_error = APIStatusError(
            upstream_detail,
            response=httpx.Response(
                401,
                request=httpx.Request("POST", "https://think.example.com/v1"),
            ),
            body={"error": {"message": upstream_detail}},
        )

        with _mock_chat() as chat_mock:
            chat_mock.side_effect = upstream_error
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="deepthink"))

        assert resp["ok"] is False
        assert resp["error"]["error"] == "llm_test_upstream"
        assert resp["error"]["status"] == 401
        dumped = json.dumps(resp, ensure_ascii=False)
        # 上游响应体 / 异常文本 / 密钥材料一律不回显
        assert upstream_detail not in dumped
        assert REASONING_PROFILE["api_key"] not in dumped
        assert TOP_LLM["api_key"] not in dumped

    @pytest.mark.asyncio
    async def test_unexpected_error_falls_back(self, tmp_path):
        """未知异常兜底 llm_test_failed，且不泄露异常文本。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat() as chat_mock:
            chat_mock.side_effect = RuntimeError("boom with key-think inside")
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="deepthink"))

        assert resp["ok"] is False
        assert resp["error"]["error"] == "llm_test_failed"
        assert REASONING_PROFILE["api_key"] not in json.dumps(resp)
        assert "boom" not in json.dumps(resp)


# ── 掩码协议回归：测试路径只读，绝不落盘 / 变更配置 ─────────────────────


class TestLlmTestMaskProtocol:
    @pytest.mark.asyncio
    async def test_success_leaves_config_untouched(self, tmp_path):
        """测试成功后：内存 api_key 保持真实值，磁盘配置字节级不变。"""
        _load(tmp_path, {"profiles": [dict(VISION_PROFILE), dict(REASONING_PROFILE)]})
        disk_before = (tmp_path / "config.json").read_text("utf-8")
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="vision-pro"))
        assert resp["ok"] is True

        cfg = cfg_module.get_config()
        by_id = {p.id: p.api_key for p in cfg.llm.profiles}
        assert by_id == {
            "vision-pro": "vision-secret-1234",
            "deepthink": "key-think",
        }
        assert (tmp_path / "config.json").read_text("utf-8") == disk_before

    @pytest.mark.asyncio
    async def test_failure_leaves_config_untouched(self, tmp_path):
        """测试失败路径同样只读：配置不落盘、不变更。"""
        _load(tmp_path, {"profiles": [dict(REASONING_PROFILE)]})
        disk_before = (tmp_path / "config.json").read_text("utf-8")
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat() as chat_mock:
            chat_mock.side_effect = TimeoutError()
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="deepthink"))
        assert resp["ok"] is False
        assert (tmp_path / "config.json").read_text("utf-8") == disk_before

    @pytest.mark.asyncio
    async def test_masked_key_never_persisted_or_echoed(self, tmp_path):
        """内存出现 *** 掩码串（模拟前端回传泄漏形态）时：测试路径不落盘、
        不外发掩码串（响应仅含档案元数据）。"""
        _load(tmp_path, {"profiles": [dict(VISION_PROFILE)]})
        disk_before = (tmp_path / "config.json").read_text("utf-8")
        assert "vision-secret-1234" in disk_before  # 磁盘是真实值
        # 运行期在内存中注入掩码串（正常流程不会发生：掩码只存在于 GET
        # 响应；此处验证即便发生，测试路径也不会把它落盘 / 外发）
        cfg_module.get_config().llm.profiles[0].api_key = "***1234"
        from main import LlmTestRequest, test_llm_connectivity

        with _mock_chat():
            resp = await test_llm_connectivity(LlmTestRequest(profile_id="vision-pro"))
        assert resp["ok"] is True
        assert "***" not in json.dumps(resp)
        # 磁盘字节级不变（掩码串未落盘，真实值未被覆盖）
        assert (tmp_path / "config.json").read_text("utf-8") == disk_before


# ── 多用户模式：鉴权门禁与治理豁免 ───────────────────────────────────────


class TestLlmTestAuthMultiUser:
    """多用户模式：管理员诊断端点（require_admin_if_multiuser）。

    跟随 test_llm_routes_api.TestLlmRoutesAuthMultiUser 形态：临时多用户
    环境 + 真实端点函数挂载 + 认证路由，验证匿名 401 / 普通用户 403 /
    管理员 200；并锁定治理豁免——诊断调用不落限流事件与并发租约。
    """

    def test_auth_gate_and_governance_exempt(self, tmp_path):
        from fastapi import FastAPI

        from backend.core.multiuser import auth as auth_core
        from backend.core.multiuser import db as db_module
        from backend.core.multiuser import llm_governance as gov
        from backend.core.multiuser import userspace
        from backend.core.security_middleware import AccessSecurityMiddleware
        from backend.modules.auth.router import auth_router
        from tests.test_multiuser.conftest import build_mu_env, csrf

        build_mu_env(tmp_path)
        try:
            app = FastAPI()
            app.add_middleware(AccessSecurityMiddleware)

            from main import test_llm_connectivity

            app.post("/api/llm/test")(test_llm_connectivity)
            app.include_router(auth_router)
            client = TestClient(app)

            admin = auth_core.create_user(
                "admin", "admin-pass-123", role="admin", status="active"
            )
            auth_core.create_user("plain", "password-123", role="user", status="active")

            # 匿名 → 401（中间件会话认证 + 依赖双保险）
            r = client.post("/api/llm/test", json={"role": "default"})
            assert r.status_code == 401

            def _login(username: str, password: str) -> None:
                r = client.post(
                    "/api/auth/login",
                    json={"username": username, "password": password},
                )
                assert r.status_code == 200, r.text

            # 普通用户 → 403 no_permission（触发真实计费属管理面操作）；
            # 请求全程挂 chat mock，被拒路径不得触发任何 LLM 调用
            _login("plain", "password-123")
            with _mock_chat() as chat_mock:
                r = client.post(
                    "/api/llm/test",
                    json={"role": "default"},
                    headers=csrf(client),
                )
                chat_mock.assert_not_awaited()
            assert r.status_code == 403
            assert r.json()["detail"]["error"] == "no_permission"

            # 管理员 → 200
            _login("admin", "admin-pass-123")
            with _mock_chat():
                r = client.post(
                    "/api/llm/test",
                    json={"role": "default"},
                    headers=csrf(client),
                )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["profile_id"] == BUILTIN_PROFILE_ID
            assert body["role"] == "default"

            # 治理豁免（有意偏离）：诊断调用不消耗管理员限流窗口 / 租约
            assert gov.active_counts(admin["id"]) == {
                "rate_events": 0,
                "leases": 0,
            }
        finally:
            userspace.set_current_user_id(None)
            db_module.close_db()

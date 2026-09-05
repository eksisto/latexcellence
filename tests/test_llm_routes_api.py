"""GET /api/llm/routes 端点与 /api/config 保存校验生效路径（multi-model P4）。

覆盖：
    - 9 个角色槽位齐全，两级解析正确（指派槽 → default 槽回退 → 内置档案）
    - 解析实时进行（内存改指派立即反映，热重载语义，不缓存）
    - 响应不含 api_key 等敏感字段
    - 鉴权门禁（§7.2 裁决）：单用户共享访问令牌（中间件）；多用户模式
      已登录即可读（require_login_if_multiuser：匿名 401，普通用户与
      管理员均 200）——模型徽标对所有登录用户展示，响应无敏感字段
    - /api/config 保存校验门禁：非法 profiles / assignments 被拒（400，
      错误消息含具体槽位与档案名，旧配置不被覆盖）；profiles api_key
      掩码回传语义（*** 按档案 id 保留原值 / 未知 id 置空 / 空串透传）
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.core import config as cfg_module
from backend.core.config import BUILTIN_PROFILE_ID, LLM_ROLE_KEYS

# 与 test_config_model_profiles / test_model_router 一致的顶层字段基线
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

TEXT_PROFILE = {
    "id": "text-fast",
    "name": "快速文本模型",
    "base_url": "https://text.example.com/v1",
    "api_key": "key-text",
    "model": "text-model",
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


# ── GET /api/llm/routes：解析结果 ────────────────────────────────────────


class TestLlmRoutesResolution:
    @pytest.mark.asyncio
    async def test_routes_cover_all_roles(self, tmp_path):
        """固定覆盖 9 个角色槽位，顺序与 LLM_ROLE_KEYS 一致。"""
        _load(tmp_path)
        from main import get_llm_routes

        resp = await get_llm_routes()
        routes = resp["routes"]
        assert [r["role"] for r in routes] == list(LLM_ROLE_KEYS)
        assert len(routes) == 9
        for item in routes:
            assert set(item) == {
                "role",
                "profile_id",
                "profile_name",
                "model",
                "capabilities",
            }

    @pytest.mark.asyncio
    async def test_two_level_resolution(self, tmp_path):
        """指派槽生效、未指派槽两级回退 default 槽、全空回退内置档案。"""
        _load(
            tmp_path,
            {
                "profiles": [VISION_PROFILE, REASONING_PROFILE],
                "assignments": {
                    "default": "",
                    "agent": "deepthink",
                    "ocr": "vision-pro",
                },
            },
        )
        from main import get_llm_routes

        by_role = {r["role"]: r for r in (await get_llm_routes())["routes"]}

        # 一级：角色指派直接生效
        assert by_role["ocr"]["profile_id"] == "vision-pro"
        assert by_role["ocr"]["profile_name"] == "视觉模型"
        assert by_role["ocr"]["model"] == "vision-model"
        assert by_role["ocr"]["capabilities"] == ["vision"]
        assert by_role["agent"]["profile_id"] == "deepthink"

        # 二级：default 槽为空 → 内置默认档案兜底
        assert by_role["default"]["profile_id"] == BUILTIN_PROFILE_ID
        assert by_role["writing_chat"]["profile_id"] == BUILTIN_PROFILE_ID

        # 全空解析：内置默认档案（顶层字段合成，视为全能力）
        builtin = by_role["writing_chat"]
        assert builtin["capabilities"] == ["vision", "reasoning"]
        assert builtin["model"] == TOP_LLM["model"]

    @pytest.mark.asyncio
    async def test_empty_role_falls_back_to_default_assignment(self, tmp_path):
        """default 槽指派了档案时，空槽位解析到该档案。"""
        _load(
            tmp_path,
            {
                "profiles": [VISION_PROFILE, REASONING_PROFILE],
                "assignments": {"default": "vision-pro", "ocr": ""},
            },
        )
        from main import get_llm_routes

        by_role = {r["role"]: r for r in (await get_llm_routes())["routes"]}
        # 注意：default → vision-pro 后，ocr 空槽两级回退同样落到 vision-pro
        assert by_role["ocr"]["profile_id"] == "vision-pro"
        assert by_role["typo_check"]["profile_id"] == "vision-pro"
        assert by_role["paragraph_translate"]["profile_id"] == "vision-pro"

    @pytest.mark.asyncio
    async def test_resolution_is_live_no_cache(self, tmp_path):
        """解析实时进行：内存改指派后立即反映（热重载语义，不缓存）。"""
        _load(tmp_path, {"profiles": [REASONING_PROFILE]})
        from main import get_llm_routes

        by_role = {r["role"]: r for r in (await get_llm_routes())["routes"]}
        assert by_role["agent"]["profile_id"] == BUILTIN_PROFILE_ID

        cfg_module.get_config().llm.assignments["agent"] = "deepthink"
        by_role = {r["role"]: r for r in (await get_llm_routes())["routes"]}
        assert by_role["agent"]["profile_id"] == "deepthink"

    @pytest.mark.asyncio
    async def test_response_never_contains_api_key(self, tmp_path):
        """响应不含 api_key 字段，也不泄露任何档案密钥材料。"""
        _load(
            tmp_path,
            {
                "profiles": [VISION_PROFILE, REASONING_PROFILE],
                "assignments": {"ocr": "vision-pro", "agent": "deepthink"},
            },
        )
        from main import get_llm_routes

        resp = await get_llm_routes()
        dumped = json.dumps(resp, ensure_ascii=False)
        assert "api_key" not in dumped
        for secret in (
            TOP_LLM["api_key"],
            VISION_PROFILE["api_key"],
            REASONING_PROFILE["api_key"],
        ):
            assert secret not in dumped


# ── GET /api/llm/routes：鉴权门禁与 /api/config 同策略 ───────────────────


class TestLlmRoutesAuthSingleUser:
    """单用户模式：共享访问令牌中间件对 /api/llm/routes 与 /api/config 一致。"""

    def test_access_token_required_and_accepted(self, tmp_path):
        _load(tmp_path)
        cfg_module.get_config().security.access_token = "route-token"
        from main import app as main_app

        client = TestClient(main_app)
        # 缺失 / 错误令牌 → 401（与 /api/config 同被中间件拦截）
        assert client.get("/api/llm/routes").status_code == 401
        assert client.get("/api/config").status_code == 401
        assert (
            client.get(
                "/api/llm/routes", headers={"X-Latexcellence-Token": "wrong"}
            ).status_code
            == 401
        )
        # 正确令牌 → 200，返回 9 个角色槽位
        r = client.get(
            "/api/llm/routes", headers={"X-Latexcellence-Token": "route-token"}
        )
        assert r.status_code == 200
        assert [item["role"] for item in r.json()["routes"]] == list(LLM_ROLE_KEYS)

    def test_open_access_when_token_unset(self, tmp_path):
        """未配置访问令牌（纯本地开发）时无需令牌即可访问。"""
        _load(tmp_path)
        assert cfg_module.get_config().security.access_token == ""
        from main import app as main_app

        client = TestClient(main_app)
        assert client.get("/api/llm/routes").status_code == 200


class TestLlmRoutesAuthMultiUser:
    """多用户模式：已登录即可读（require_login_if_multiuser），匿名 401。

    跟随 tests/test_multiuser 的既有测试形态：临时多用户环境 + 真实端点
    函数挂载 + 认证路由，验证匿名 401 / 普通用户 200 / 管理员 200
    （multi-model §7.2：模型徽标对多用户模式所有登录用户展示，
    端点不要求管理员；严于中间件会话门禁由依赖显式声明兜底）。
    """

    def test_logged_in_users_can_read(self, tmp_path):
        from fastapi import FastAPI

        from backend.core.multiuser import auth as auth_core
        from backend.core.multiuser import db as db_module
        from backend.core.security_middleware import AccessSecurityMiddleware
        from backend.modules.auth.router import auth_router
        from main import get_llm_routes
        from tests.test_multiuser.conftest import build_mu_env

        build_mu_env(tmp_path)
        try:
            app = FastAPI()
            app.add_middleware(AccessSecurityMiddleware)
            app.get("/api/llm/routes")(get_llm_routes)
            app.include_router(auth_router)
            client = TestClient(app)

            def _login(username: str, password: str) -> None:
                r = client.post(
                    "/api/auth/login", json={"username": username, "password": password}
                )
                assert r.status_code == 200, r.text

            # 匿名 → 401（中间件会话认证 + 依赖双保险）
            assert client.get("/api/llm/routes").status_code == 401

            # 普通用户 → 200（模型为服务端全局配置，对用户只读）
            auth_core.create_user("plain", "password-123", role="user", status="active")
            _login("plain", "password-123")
            r = client.get("/api/llm/routes")
            assert r.status_code == 200
            assert [item["role"] for item in r.json()["routes"]] == list(LLM_ROLE_KEYS)
            # 对非管理员同样不泄露敏感字段
            assert "api_key" not in json.dumps(r.json(), ensure_ascii=False)

            # 管理员 → 200
            auth_core.create_user(
                "admin", "admin-pass-123", role="admin", status="active"
            )
            _login("admin", "admin-pass-123")
            r = client.get("/api/llm/routes")
            assert r.status_code == 200
            assert [item["role"] for item in r.json()["routes"]] == list(LLM_ROLE_KEYS)
        finally:
            db_module.close_db()


# ── POST /api/config：保存校验门禁生效路径锁定 ───────────────────────────


class TestConfigSaveValidationGate:
    """非法 profiles / assignments 经 /api/config 保存被拒且错误可读。"""

    def _load_with_profiles(self, tmp_path: Path) -> None:
        _load(
            tmp_path,
            {"profiles": [dict(VISION_PROFILE), dict(TEXT_PROFILE)]},
        )

    @pytest.mark.asyncio
    async def test_ocr_slot_non_vision_profile_rejected(self, tmp_path):
        """OCR 槽指派到非 vision 档案：400，错误含槽位与档案名。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        with pytest.raises(HTTPException) as exc_info:
            await update_config({"llm": {"assignments": {"ocr": "text-fast"}}})

        assert exc_info.value.status_code == 400
        assert '槽位 "ocr"' in exc_info.value.detail
        assert "text-fast" in exc_info.value.detail
        assert "快速文本模型" in exc_info.value.detail
        # 保存失败不破坏现有配置
        assert cfg_module.get_config().llm.assignments["ocr"] == ""

    @pytest.mark.asyncio
    async def test_assignment_missing_profile_rejected(self, tmp_path):
        """assignments 引用不存在的档案：400，错误含槽位与档案 id。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        with pytest.raises(HTTPException) as exc_info:
            await update_config({"llm": {"assignments": {"agent": "ghost"}}})

        assert exc_info.value.status_code == 400
        assert '槽位 "agent"' in exc_info.value.detail
        assert "ghost" in exc_info.value.detail
        assert cfg_module.get_config().llm.assignments["agent"] == ""

    @pytest.mark.asyncio
    async def test_duplicate_profile_ids_rejected(self, tmp_path):
        """档案 id 重复：400，错误含重复 id。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        with pytest.raises(HTTPException) as exc_info:
            await update_config(
                {
                    "llm": {
                        "profiles": [
                            dict(VISION_PROFILE),
                            dict(REASONING_PROFILE),
                            dict(REASONING_PROFILE, model="think-model-v2"),
                        ]
                    }
                }
            )

        assert exc_info.value.status_code == 400
        assert "deepthink" in exc_info.value.detail
        # profiles 整表替换未生效（保存被拒，原档案保留）
        assert [p.id for p in cfg_module.get_config().llm.profiles] == [
            "vision-pro",
            "text-fast",
        ]

    @pytest.mark.asyncio
    async def test_invalid_profile_id_rejected(self, tmp_path):
        """档案 id 格式非法（含空格）：400，错误含非法 id。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        bad = dict(REASONING_PROFILE, id="bad id!")
        with pytest.raises(HTTPException) as exc_info:
            await update_config({"llm": {"profiles": [dict(VISION_PROFILE), bad]}})

        assert exc_info.value.status_code == 400
        assert "bad id!" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_capabilities_rejected(self, tmp_path):
        """capabilities 含闭集之外的值：400，错误含非法值。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        bad = dict(REASONING_PROFILE, capabilities=["reasoning", "telepathy"])
        with pytest.raises(HTTPException) as exc_info:
            await update_config({"llm": {"profiles": [dict(VISION_PROFILE), bad]}})

        assert exc_info.value.status_code == 400
        assert "telepathy" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_unknown_assignment_slot_rejected(self, tmp_path):
        """assignments 出现未知槽位：400，错误含槽位名。"""
        self._load_with_profiles(tmp_path)
        from main import update_config

        with pytest.raises(HTTPException) as exc_info:
            await update_config({"llm": {"assignments": {"magic_slot": "vision-pro"}}})

        assert exc_info.value.status_code == 400
        assert "magic_slot" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_full_table_save_readback_and_masking(self, tmp_path):
        """合法 profiles + assignments 整表保存：GET 回读一致且 api_key 脱敏。"""
        self._load_with_profiles(tmp_path)
        from main import get_full_config, update_config

        assignments = {
            "default": "",
            "agent": "deepthink",
            "ocr": "vision-pro",
            "writing_chat": "",
            "typo_check": "",
            "expression_opt": "",
            "code_review": "",
            "terminology_check": "",
            "paragraph_translate": "",
        }
        profiles = [dict(VISION_PROFILE), dict(REASONING_PROFILE)]
        resp = await update_config(
            {"llm": {"profiles": profiles, "assignments": assignments}}
        )
        assert resp == {"status": "ok"}

        # 内存配置生效
        cfg = cfg_module.get_config()
        assert cfg.llm.assignments == assignments
        assert [p.id for p in cfg.llm.profiles] == ["vision-pro", "deepthink"]

        # GET /api/config 回读一致，档案 api_key 脱敏为 ***xxxx
        data = await get_full_config()
        assert data["llm"]["assignments"] == assignments
        by_id = {p["id"]: p for p in data["llm"]["profiles"]}
        assert by_id["vision-pro"]["api_key"] == "***1234"
        assert by_id["deepthink"]["api_key"].startswith("***")
        dumped = json.dumps(data, ensure_ascii=False)
        assert "vision-secret-1234" not in dumped
        assert "key-think" not in dumped

        # 磁盘持久化为真实值（掩码串绝不落盘）
        on_disk = json.loads(Path(tmp_path / "config.json").read_text("utf-8"))
        assert on_disk["llm"]["assignments"] == assignments
        disk_by_id = {p["id"]: p for p in on_disk["llm"]["profiles"]}
        assert disk_by_id["vision-pro"]["api_key"] == "vision-secret-1234"
        assert disk_by_id["deepthink"]["api_key"] == "key-think"


class TestConfigProfileKeyMaskRoundTrip:
    """profiles api_key 掩码回传语义锁定（update_config_from_dict 档案分支）。

    GET /api/config 返回 "***" + 后 4 位掩码；前端原样回传保存时必须：
    "***" 前缀按档案 id 匹配保留原值（掩码串绝不落盘）、id 匹配不到任何
    现有档案时置空（防掩码串被当作真实 Key 写入）、空串按用户输入透传
    （档案 Key 允许为空，适配免鉴权端点）。
    """

    def _load_with_vision(self, tmp_path: Path) -> None:
        _load(tmp_path, {"profiles": [dict(VISION_PROFILE)]})

    @pytest.mark.asyncio
    async def test_masked_key_kept_by_profile_id(self, tmp_path):
        """*** 前缀 + 已存在档案 id → 保留原值，内存与磁盘均无掩码串。"""
        self._load_with_vision(tmp_path)
        from main import update_config

        resp = await update_config(
            {"llm": {"profiles": [dict(VISION_PROFILE, api_key="***1234")]}}
        )
        assert resp == {"status": "ok"}

        cfg = cfg_module.get_config()
        assert [p.api_key for p in cfg.llm.profiles] == ["vision-secret-1234"]
        on_disk = json.loads(Path(tmp_path / "config.json").read_text("utf-8"))
        assert on_disk["llm"]["profiles"][0]["api_key"] == "vision-secret-1234"
        assert "***" not in json.dumps(on_disk)

    @pytest.mark.asyncio
    async def test_masked_key_with_unknown_id_cleared(self, tmp_path):
        """*** 前缀 + id 匹配不到现有档案 → 置空，掩码串绝不写入。"""
        self._load_with_vision(tmp_path)
        from main import update_config

        newcomer = dict(TEXT_PROFILE, id="brand-new", api_key="***6789")
        resp = await update_config(
            {"llm": {"profiles": [dict(VISION_PROFILE), newcomer]}}
        )
        assert resp == {"status": "ok"}

        cfg = cfg_module.get_config()
        by_id = {p.id: p.api_key for p in cfg.llm.profiles}
        assert by_id == {"vision-pro": "vision-secret-1234", "brand-new": ""}
        on_disk = json.loads(Path(tmp_path / "config.json").read_text("utf-8"))
        disk_by_id = {p["id"]: p["api_key"] for p in on_disk["llm"]["profiles"]}
        assert disk_by_id["brand-new"] == ""
        assert "***" not in json.dumps(on_disk)

    @pytest.mark.asyncio
    async def test_empty_key_passes_through(self, tmp_path):
        """空串（非掩码）按用户输入透传：允许清空档案 Key。"""
        self._load_with_vision(tmp_path)
        from main import update_config

        resp = await update_config(
            {"llm": {"profiles": [dict(VISION_PROFILE, api_key="")]}}
        )
        assert resp == {"status": "ok"}

        cfg = cfg_module.get_config()
        assert [p.api_key for p in cfg.llm.profiles] == [""]
        on_disk = json.loads(Path(tmp_path / "config.json").read_text("utf-8"))
        assert on_disk["llm"]["profiles"][0]["api_key"] == ""

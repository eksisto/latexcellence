"""多用户模式配置（P0）：配置模型、校验、示例文件一致性与状态端点。"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from backend.core import config as cfg_module
from backend.core.config import AppConfig, MultiUserConfig

SAMPLE_CONFIG = {
    "server": {"host": "0.0.0.0", "port": 8000, "dev_mode": True},
    "llm": {"api_key": "", "base_url": "https://x/v1", "model": "m"},
    "logging": {"level": "INFO", "dir": "logs/"},
}


@pytest.fixture(autouse=True)
def _reset_config():
    """每次测试后重置 config 模块全局状态"""
    yield
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def _write_config(tmp_path, *, multi_user=None, overrides=None):
    data = json.loads(json.dumps(SAMPLE_CONFIG))
    if overrides:
        data.update(overrides)
    if multi_user is not None:
        data["multi_user"] = multi_user
    path = tmp_path / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return str(path)


def _load(tmp_path, *, multi_user=None, overrides=None) -> AppConfig:
    path = _write_config(tmp_path, multi_user=multi_user, overrides=overrides)
    # 清空环境变量避免本机 LX_LLM_KEY 干扰
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg_module.load_config(path)
    return cfg_module.get_config()


class TestMultiUserDefaults:
    """旧配置（不含 multi_user）必须保持向后兼容，默认关闭。"""

    def test_absent_multi_user_defaults_to_disabled(self, tmp_path):
        cfg = _load(tmp_path)
        assert cfg.multi_user.enabled is False
        # 默认值与方案文档一致
        assert cfg.multi_user.db_path == "data/latexcellence.db"
        assert cfg.multi_user.users_root == "data/users/"
        assert cfg.multi_user.staging_root == "data/staging/"
        assert cfg.multi_user.default_project_quota_mb == 500
        assert cfg.multi_user.default_derived_quota_mb == 100

    def test_model_defaults_match_plan(self):
        mu = MultiUserConfig()
        assert mu.max_upload_archive_mb == 500
        assert mu.max_upload_files == 20000
        assert mu.max_upload_file_size_mb == 100
        assert mu.max_compression_ratio == 1000
        assert mu.recycle_bin_days == 7
        assert mu.session_ttl_days == 7
        assert mu.remember_ttl_days == 30
        assert mu.llm_rate_limit_per_minute == 20
        assert mu.llm_max_concurrency_per_user == 2
        assert mu.login_rate_limit_per_minute == 5
        assert mu.registration_enabled is True
        assert mu.admin_username_env == "LATEXCELLENCE_ADMIN_USERNAME"
        assert mu.admin_password_env == "LATEXCELLENCE_ADMIN_PASSWORD"


class TestMultiUserValidation:
    """多用户配置的 Pydantic 校验。"""

    def test_enabled_section_loads(self, tmp_path):
        cfg = _load(
            tmp_path,
            multi_user={"enabled": True, "users_root": "data/users2/"},
        )
        assert cfg.multi_user.enabled is True
        assert cfg.multi_user.users_root == "data/users2/"
        # 未显式给出的字段使用默认值
        assert cfg.multi_user.default_project_quota_mb == 500

    @pytest.mark.parametrize(
        "field",
        [
            "default_project_quota_mb",
            "default_derived_quota_mb",
            "max_upload_archive_mb",
            "max_upload_files",
            "max_upload_file_size_mb",
            "max_compression_ratio",
            "recycle_bin_days",
            "session_ttl_days",
            "remember_ttl_days",
            "llm_rate_limit_per_minute",
            "llm_max_concurrency_per_user",
            "login_rate_limit_per_minute",
        ],
    )
    def test_zero_or_negative_rejected(self, field):
        for bad in (0, -1):
            with pytest.raises(Exception):
                MultiUserConfig(**{field: bad})

    def test_invalid_field_type_rejected(self):
        with pytest.raises(Exception):
            MultiUserConfig(default_project_quota_mb="huge")

    def test_unknown_key_rejected(self):
        """未知配置键应当报错而不是被静默忽略。"""
        with pytest.raises(Exception):
            MultiUserConfig(no_such_option=True)

    def test_quota_override_via_update(self, tmp_path):
        """update_config_from_dict 支持部分更新 multi_user（enabled 除外）。

        enabled 受启动锚定：认证模式只能经重启切换，保存路径的翻转
        请求固定回启动值（与热重载路径同一语义）。"""
        path = _write_config(tmp_path, multi_user={"enabled": False})
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
        cfg_module.update_config_from_dict(
            {"multi_user": {"enabled": True, "default_project_quota_mb": 250}}
        )
        cfg = cfg_module.get_config()
        assert cfg.multi_user.enabled is False  # 启动锚定：翻转被拒绝
        assert cfg.multi_user.default_project_quota_mb == 250
        # 其余字段不受部分更新影响
        assert cfg.multi_user.default_derived_quota_mb == 100


class TestConfigExampleConsistency:
    """config.example.json 必须能通过 AppConfig 校验（配置模板与实际模型一致）。"""

    def test_example_file_loads(self):
        project_root = Path(__file__).resolve().parent.parent
        example_path = project_root / "config.example.json"
        with open(example_path, encoding="utf-8") as f:
            data = json.load(f)
        cfg = AppConfig(**data)
        assert cfg.multi_user.enabled is False
        assert cfg.multi_user.max_upload_archive_mb == 500


class TestConfigStatusEndpoint:
    """状态端点暴露 multi_user 模式标志（前端据此判断单/多用户模式）。"""

    @pytest.mark.asyncio
    async def test_status_reports_multi_user_disabled(self, tmp_path):
        path = _write_config(tmp_path, multi_user={"enabled": False})
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
            from main import config_status

            resp = await config_status()
        assert resp["multi_user"] is False

    @pytest.mark.asyncio
    async def test_status_reports_multi_user_enabled(self, tmp_path):
        path = _write_config(tmp_path, multi_user={"enabled": True})
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(path)
            from main import config_status

            resp = await config_status()
        assert resp["multi_user"] is True

"""backend/core/env.py 的 .env 加载器测试。"""

import json
import os

import pytest

from backend.core.env import load_env_file


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """清空测试用环境变量并重置加载器的默认路径幂等状态。"""
    monkeypatch.delenv("LATEXCELLENCE_ENV_LOADER_TEST_KEY", raising=False)
    monkeypatch.delenv("LATEXCELLENCE_ENV_LOADER_TEST_ONLY", raising=False)
    import backend.core.env as env_module

    monkeypatch.setattr(env_module, "_loaded_path", None)


def test_load_env_file_populates_missing_vars(tmp_path, _clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LATEXCELLENCE_ENV_LOADER_TEST_KEY=from-file\n",
        encoding="utf-8",
    )

    assert load_env_file(env_file) is True
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-file"


def test_real_env_takes_priority_over_file(tmp_path, _clean_env, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LATEXCELLENCE_ENV_LOADER_TEST_KEY=from-file\n"
        "LATEXCELLENCE_ENV_LOADER_TEST_ONLY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LATEXCELLENCE_ENV_LOADER_TEST_KEY", "from-real-env")

    assert load_env_file(env_file) is True
    # 真实环境变量优先：.env 只补缺，不覆盖
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-real-env"
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_ONLY"] == "from-file"


def test_missing_file_returns_false(tmp_path, _clean_env):
    assert load_env_file(tmp_path / "nonexistent.env") is False


def test_default_path_is_idempotent(tmp_path, _clean_env, monkeypatch):
    """默认路径只加载一次；显式传 path 的调用不受幂等限制。"""
    import backend.core.env as env_module

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LATEXCELLENCE_ENV_LOADER_TEST_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_module, "_DEFAULT_ENV_PATH", env_file)

    assert load_env_file() is True
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-file"

    # 修改文件后再次默认加载：幂等跳过，不重新读取
    env_file.write_text(
        "LATEXCELLENCE_ENV_LOADER_TEST_KEY=changed\n",
        encoding="utf-8",
    )
    assert load_env_file() is False
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-file"

    # 显式传路径允许重新读取文件，但 override=False 语义下
    # 已注入的值依然不被文件覆盖（环境变量优先是全局约定）
    assert load_env_file(env_file) is True
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-file"


def test_parse_config_file_triggers_env_loading(tmp_path, _clean_env, monkeypatch):
    """回归：.env 加载收口在 config._parse_config_file，任何入口经
    load_config / 热重载自动生效，无需入口点单独调用。"""
    import backend.core.config as config_module
    import backend.core.env as env_module

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LATEXCELLENCE_ENV_LOADER_TEST_KEY=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_module, "_DEFAULT_ENV_PATH", env_file)

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "server": {"host": "0.0.0.0", "port": 8000, "dev_mode": True},
                "llm": {"api_key": "", "base_url": "https://x/v1", "model": "m"},
            }
        ),
        encoding="utf-8",
    )
    config_module._parse_config_file(config_file)
    assert os.environ["LATEXCELLENCE_ENV_LOADER_TEST_KEY"] == "from-file"

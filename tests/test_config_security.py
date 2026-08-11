"""测试配置安全策略：环境变量 API Key 防落盘"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from backend.core import config as cfg_module

SAMPLE_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "dev_mode": True,
        "vite_dev_url": "http://localhost:5173",
    },
    "llm": {
        "api_key": "file-key-123",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "temperature": 0.3,
        "max_tokens": 4096,
        "request_interval": 1.5,
        "max_concurrency": 5,
    },
    "features": {
        "typo_check": {"enabled": True, "batch_size": 5},
        "expression_opt": {
            "enabled": True,
            "batch_size": 3,
            "default_prompt": "...",
            "custom_prompts_dir": "prompts/expression/",
        },
        "formula_ocr": {"enabled": True, "max_image_size_mb": 10},
        "table_ocr": {"enabled": True, "max_image_size_mb": 10},
        "template_gen": {"enabled": True, "output_dir": "templates/"},
        "code_review": {"enabled": True, "batch_size": 5},
        "compile": {"enabled": True, "latexmk_timeout": 120},
        "git_diff": {"enabled": True},
        "format_converter": {"enabled": True},
        "style_modify": {"enabled": True},
        "terminology_check": {"enabled": True, "batch_size": 5},
        "writing_chat": {
            "enabled": True,
            "compression_threshold_tokens": 200000,
            "sessions_dir": "sessions/",
            "custom_prompts_dir": "prompts/writing_chat/",
            "summary_temperature": 0.2,
            "summary_max_tokens": 50000,
        },
        "bib_manager": {
            "enabled": True,
            "format_configs_dir": "formats/",
            "providers": {
                "crossref": {"enabled": True},
                "arxiv": {"enabled": True},
                "datacite": {"enabled": True},
                "openalex": {"enabled": False},
                "europepmc": {"enabled": False},
                "semantic_scholar": {"enabled": False},
            },
        },
    },
    "logging": {"level": "INFO", "dir": "logs/"},
}


@pytest.fixture(autouse=True)
def _reset_config():
    """每次测试后重置 config 模块全局状态"""
    yield
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._api_key_from_env = False


def _write_config(tmp_path, overrides=None):
    data = dict(SAMPLE_CONFIG)
    if overrides:
        _deep_merge(data, overrides)
    path = tmp_path / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return str(path)


def _deep_merge(base, updates):
    for k, v in updates.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _read_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestEnvVarKeyNotLeakedToDisk:
    """核心测试：环境变量 API Key 在 save_config() 后不写入磁盘"""

    def test_save_config_strips_env_var_key(self, tmp_path):
        """环境变量 Key → save_config() 后磁盘文件 api_key 为空"""
        config_path = _write_config(
            tmp_path, overrides={"llm": {"api_key": "file-key-123"}}
        )
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-key-456"}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is True
            assert cfg_module.get_config().llm.api_key == "env-key-456"
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "", "环境变量 Key 不应写入磁盘"

    def test_file_key_still_persisted(self, tmp_path):
        """无环境变量 → 文件中的 api_key 正常持久化"""
        config_path = _write_config(
            tmp_path, overrides={"llm": {"api_key": "file-key-123"}}
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is False
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "file-key-123", "文件 Key 应正常持久化"

    def test_env_var_no_file_key(self, tmp_path):
        """环境变量 Key + 文件无 Key → save_config() 不写入"""
        config_path = _write_config(tmp_path, overrides={"llm": {"api_key": ""}})
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-only-key"}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is True
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "", "磁盘不应有 Key"


class TestUpdateConfigFromDict:
    """测试 update_config_from_dict 的 Key 处理逻辑"""

    def test_masked_key_keeps_existing_env_key_no_leak(self, tmp_path):
        """前端传掩码 Key + 环境变量 → 保存后磁盘无 Key"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-key-789"}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is True
            cfg_module.update_config_from_dict({"llm": {"api_key": "***6789"}})
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "", "掩码保存不应写入环境变量 Key"

    def test_new_key_from_frontend_persists(self, tmp_path):
        """用户手动输入新 Key → 持久化到磁盘"""
        config_path = _write_config(tmp_path, overrides={"llm": {"api_key": "old-key"}})
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is False
            cfg_module.update_config_from_dict({"llm": {"api_key": "new-manual-key"}})
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "new-manual-key", "手动输入 Key 应持久化"
        assert not cfg_module._api_key_from_env

    def test_new_key_overrides_env_var_marking(self, tmp_path):
        """用户手动输入 Key 覆盖环境变量 → 持久化新 Key"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-key"}, clear=True):
            cfg_module.load_config(config_path)
            assert cfg_module._api_key_from_env is True
            cfg_module.update_config_from_dict(
                {"llm": {"api_key": "user-override-key"}}
            )
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "user-override-key", "手动输入应持久化"
        assert not cfg_module._api_key_from_env
        assert cfg_module.get_config().llm.api_key == "user-override-key"

    def test_empty_key_from_frontend_keeps_existing(self, tmp_path):
        """前端传空 Key → 保留原 Key 不变"""
        config_path = _write_config(
            tmp_path, overrides={"llm": {"api_key": "existing-key"}}
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict({"llm": {"api_key": ""}})
        assert cfg_module.get_config().llm.api_key == "existing-key"

    def test_update_other_fields_no_key_impact(self, tmp_path):
        """修改非 Key 字段时 Key 不受影响"""
        config_path = _write_config(
            tmp_path, overrides={"llm": {"api_key": "stable-key"}}
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict({"server": {"port": 9999}})
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "stable-key"
        assert result["server"]["port"] == 9999

    def test_masked_openalex_key_keeps_existing(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "scholarly_research": {
                            "providers": {"openalex": {"api_key": "openalex-secret"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "scholarly_research": {
                                "providers": {"openalex": {"api_key": "***cret"}}
                            }
                        }
                    }
                }
            )

        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["scholarly_research"]["providers"][
                "openalex"
            ]["api_key"]
            == "openalex-secret"
        )

    def test_empty_openalex_key_clears_existing(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "scholarly_research": {
                            "providers": {"openalex": {"api_key": "openalex-secret"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "scholarly_research": {
                                "providers": {"openalex": {"api_key": ""}}
                            }
                        }
                    }
                }
            )

        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["scholarly_research"]["providers"][
                "openalex"
            ]["api_key"]
            == ""
        )


class TestOpenAlexKeyMasking:
    @pytest.mark.asyncio
    async def test_full_config_masks_openalex_key(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "scholarly_research": {
                            "providers": {
                                "openalex": {"api_key": "openalex-secret-1234"}
                            }
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            from main import get_full_config

            response = await get_full_config()

        masked = response["features"]["function_calling"]["scholarly_research"][
            "providers"
        ]["openalex"]["api_key"]
        assert masked == "***1234"
        assert "openalex-secret" not in json.dumps(response)


class TestTavilyKeyMasking:
    """Tavily 搜索 API Key 的掩码与保留逻辑。"""

    def test_masked_tavily_key_keeps_existing(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {
                            "engines": {"tavily": {"api_key": "tvly-secret-1234"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "web_search": {
                                "engines": {"tavily": {"api_key": "***1234"}}
                            }
                        }
                    }
                }
            )

        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["web_search"]["engines"]["tavily"][
                "api_key"
            ]
            == "tvly-secret-1234"
        )

    def test_empty_tavily_key_clears_existing(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {
                            "engines": {"tavily": {"api_key": "tvly-secret-1234"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "web_search": {"engines": {"tavily": {"api_key": ""}}}
                        }
                    }
                }
            )

        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["web_search"]["engines"]["tavily"][
                "api_key"
            ]
            == ""
        )

    @pytest.mark.asyncio
    async def test_full_config_masks_tavily_key(self, tmp_path):
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {
                            "engines": {"tavily": {"api_key": "tvly-secret-1234"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            from main import get_full_config

            response = await get_full_config()

        masked = response["features"]["function_calling"]["web_search"]["engines"][
            "tavily"
        ]["api_key"]
        assert masked == "***1234"
        assert "tvly-secret-1234" not in json.dumps(response)


class TestConfigApiValidation:
    @pytest.mark.asyncio
    async def test_invalid_plot_rounds_returns_400(self, tmp_path):
        """越界的配置值应返回 400，而不是触发 500。"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            from fastapi import HTTPException
            from main import update_config

            with pytest.raises(HTTPException) as exc_info:
                await update_config(
                    {"features": {"plot_gen": {"agent_max_rounds": 100}}}
                )

        assert exc_info.value.status_code == 400
        assert "agent_max_rounds" in exc_info.value.detail
        assert cfg_module.get_config().features.plot_gen.agent_max_rounds == 20

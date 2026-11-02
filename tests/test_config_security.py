"""测试配置安全策略：环境变量 API Key 防落盘"""

import json
import os
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
        "max_tokens": 8192,
        "request_interval": 1.5,
        "max_concurrency": 5,
    },
    "features": {
        "typo_check": {"enabled": True, "batch_size": 5},
        "expression_opt": {
            "enabled": True,
            "batch_size": 3,
            "default_prompt": "...",
            "custom_prompts_dir": "data/prompts/expression/",
        },
        "formula_ocr": {"enabled": True, "max_image_size_mb": 10},
        "table_ocr": {"enabled": True, "max_image_size_mb": 10},
        "template_gen": {"enabled": True, "output_dir": "data/templates/"},
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
            "custom_prompts_dir": "data/prompts/writing_chat/",
            "summary_temperature": 0.2,
            "summary_token_budget": 50000,
        },
        "bib_manager": {
            "enabled": True,
            "format_configs_dir": "data/formats/",
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
    cfg_module._secrets_from_env = set()


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
            assert "llm.api_key" in cfg_module._secrets_from_env
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
            assert "llm.api_key" not in cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "file-key-123", "文件 Key 应正常持久化"

    def test_env_var_no_file_key(self, tmp_path):
        """环境变量 Key + 文件无 Key → save_config() 不写入"""
        config_path = _write_config(tmp_path, overrides={"llm": {"api_key": ""}})
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-only-key"}, clear=True):
            cfg_module.load_config(config_path)
            assert "llm.api_key" in cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "", "磁盘不应有 Key"


class TestSmtpPasswordEnvVar:
    """SMTP 密码环境变量（LATEXCELLENCE_SMTP_PASSWORD）：与 llm.api_key 同一安全策略"""

    def test_save_config_strips_env_var_smtp_password(self, tmp_path):
        """环境变量密码 → save_config() 后磁盘文件 smtp.password 为空"""
        config_path = _write_config(
            tmp_path,
            overrides={
                "smtp": {"enabled": True, "host": "smtp.test", "password": "file-pass"}
            },
        )
        with mock.patch.dict(
            os.environ,
            {cfg_module.SMTP_PASSWORD_ENV: "env-pass-456"},
            clear=True,
        ):
            cfg_module.load_config(config_path)
            assert "smtp.password" in cfg_module._secrets_from_env
            assert cfg_module.get_config().smtp.password == "env-pass-456"
            # llm.api_key 未被环境变量覆盖时仍正常持久化（互不影响）
            assert "llm.api_key" not in cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["smtp"]["password"] == "", "环境变量密码不应写入磁盘"
        assert result["smtp"]["host"] == "smtp.test"

    def test_file_smtp_password_still_persisted(self, tmp_path):
        """无环境变量 → 文件中的 smtp.password 正常持久化"""
        config_path = _write_config(
            tmp_path, overrides={"smtp": {"enabled": True, "password": "file-pass"}}
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            assert "smtp.password" not in cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        assert result["smtp"]["password"] == "file-pass", "文件密码应正常持久化"

    def test_masked_smtp_password_keeps_env_value_no_leak(self, tmp_path):
        """前端传掩码密码 + 环境变量 → 内存保留原值，保存后磁盘无密码"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(
            os.environ,
            {cfg_module.SMTP_PASSWORD_ENV: "env-pass-789"},
            clear=True,
        ):
            cfg_module.load_config(config_path)
            assert "smtp.password" in cfg_module._secrets_from_env
            assert cfg_module.get_config().smtp.password == "env-pass-789"
            cfg_module.update_config_from_dict({"smtp": {"password": "***6789"}})
            assert cfg_module.get_config().smtp.password == "env-pass-789"
        result = _read_config(config_path)
        assert result["smtp"]["password"] == "", "掩码保存不应写入环境变量密码"
        assert "env-pass-789" not in json.dumps(result)

    def test_new_smtp_password_overrides_env_marking(self, tmp_path):
        """用户手动输入真实密码覆盖环境变量 → 持久化新密码"""
        config_path = _write_config(
            tmp_path, overrides={"smtp": {"enabled": True, "password": "old-pass"}}
        )
        with mock.patch.dict(
            os.environ,
            {cfg_module.SMTP_PASSWORD_ENV: "env-pass"},
            clear=True,
        ):
            cfg_module.load_config(config_path)
            assert "smtp.password" in cfg_module._secrets_from_env
            cfg_module.update_config_from_dict({"smtp": {"password": "manual-pass"}})
        result = _read_config(config_path)
        assert result["smtp"]["password"] == "manual-pass", "手动输入应持久化"
        assert "smtp.password" not in cfg_module._secrets_from_env
        assert cfg_module.get_config().smtp.password == "manual-pass"


class TestUpdateConfigFromDict:
    """测试 update_config_from_dict 的 Key 处理逻辑"""

    def test_masked_key_keeps_existing_env_key_no_leak(self, tmp_path):
        """前端传掩码 Key + 环境变量 → 保存后磁盘无 Key"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-key-789"}, clear=True):
            cfg_module.load_config(config_path)
            assert "llm.api_key" in cfg_module._secrets_from_env
            cfg_module.update_config_from_dict({"llm": {"api_key": "***6789"}})
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "", "掩码保存不应写入环境变量 Key"

    def test_new_key_from_frontend_persists(self, tmp_path):
        """用户手动输入新 Key → 持久化到磁盘"""
        config_path = _write_config(tmp_path, overrides={"llm": {"api_key": "old-key"}})
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            assert "llm.api_key" not in cfg_module._secrets_from_env
            cfg_module.update_config_from_dict({"llm": {"api_key": "new-manual-key"}})
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "new-manual-key", "手动输入 Key 应持久化"
        assert "llm.api_key" not in cfg_module._secrets_from_env

    def test_new_key_overrides_env_var_marking(self, tmp_path):
        """用户手动输入 Key 覆盖环境变量 → 持久化新 Key"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(os.environ, {"LX_LLM_KEY": "env-key"}, clear=True):
            cfg_module.load_config(config_path)
            assert "llm.api_key" in cfg_module._secrets_from_env
            cfg_module.update_config_from_dict(
                {"llm": {"api_key": "user-override-key"}}
            )
        result = _read_config(config_path)
        assert result["llm"]["api_key"] == "user-override-key", "手动输入应持久化"
        assert "llm.api_key" not in cfg_module._secrets_from_env
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


class TestSearchApiKeyEnvVars:
    """搜索类 API Key 环境变量（Tavily / 学术数据源）：与 llm.api_key 同一安全策略

    - LATEXCELLENCE_TAVILY_API_KEY → web_search.engines.tavily.api_key
    - LATEXCELLENCE_<PROVIDER>_API_KEY → scholarly_research.providers.<provider>.api_key
    环境变量优先级高于 config.json，且注入值永不写盘。
    """

    _TAVILY_PATH = "features.function_calling.web_search.engines.tavily.api_key"
    _S2_PATH = (
        "features.function_calling.scholarly_research.providers."
        "semantic_scholar.api_key"
    )

    def test_tavily_env_key_overrides_and_never_persisted(self, tmp_path):
        """环境变量 Tavily Key → 内存生效，save_config() 后磁盘为空"""
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {"engines": {"tavily": {"api_key": "file-tvly"}}}
                    }
                }
            },
        )
        with mock.patch.dict(
            os.environ, {cfg_module.TAVILY_API_KEY_ENV: "env-tvly-456"}, clear=True
        ):
            cfg_module.load_config(config_path)
            assert self._TAVILY_PATH in cfg_module._secrets_from_env
            assert (
                cfg_module.get_config().features.function_calling.web_search.engines.tavily.api_key
                == "env-tvly-456"
            )
            # 未被环境变量覆盖的 llm.api_key 不受影响，仍正常持久化
            assert "llm.api_key" not in cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["web_search"]["engines"]["tavily"][
                "api_key"
            ]
            == ""
        ), "环境变量 Tavily Key 不应写入磁盘"

    def test_scholarly_provider_env_key_overrides_and_never_persisted(self, tmp_path):
        """环境变量学术数据源 Key → 内存生效，save_config() 后磁盘为空"""
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "scholarly_research": {
                            "providers": {"semantic_scholar": {"api_key": "file-s2"}}
                        }
                    }
                }
            },
        )
        with mock.patch.dict(
            os.environ,
            {"LATEXCELLENCE_SEMANTIC_SCHOLAR_API_KEY": "env-s2-456"},
            clear=True,
        ):
            cfg_module.load_config(config_path)
            assert self._S2_PATH in cfg_module._secrets_from_env
            provider_cfg = (
                cfg_module.get_config().features.function_calling.scholarly_research.providers.semantic_scholar
            )
            assert provider_cfg.api_key == "env-s2-456"
            cfg_module.save_config()
        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["scholarly_research"]["providers"][
                "semantic_scholar"
            ]["api_key"]
            == ""
        ), "环境变量学术数据源 Key 不应写入磁盘"

    def test_multiple_search_keys_injected_together(self, tmp_path):
        """多个搜索 Key 环境变量同时注入；文件中未注入的 Key 正常落盘"""
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {"engines": {"tavily": {"api_key": "file-tvly"}}},
                        "scholarly_research": {
                            "providers": {"openalex": {"api_key": "file-oa"}}
                        },
                    }
                }
            },
        )
        with mock.patch.dict(
            os.environ,
            {
                cfg_module.TAVILY_API_KEY_ENV: "env-tvly",
                "LATEXCELLENCE_OPENALEX_API_KEY": "env-oa",
            },
            clear=True,
        ):
            cfg_module.load_config(config_path)
            cfg_fn = cfg_module.get_config().features.function_calling
            assert cfg_fn.web_search.engines.tavily.api_key == "env-tvly"
            assert cfg_fn.scholarly_research.providers.openalex.api_key == "env-oa"
            cfg_module.save_config()
        result = _read_config(config_path)
        providers = result["features"]["function_calling"]["scholarly_research"][
            "providers"
        ]
        assert (
            result["features"]["function_calling"]["web_search"]["engines"]["tavily"][
                "api_key"
            ]
            == ""
        )
        assert providers["openalex"]["api_key"] == ""
        assert "env-tvly" not in json.dumps(result)
        assert "env-oa" not in json.dumps(result)

    def test_no_search_env_keys_file_values_persisted(self, tmp_path):
        """无环境变量 → 文件中的搜索 Key 正常持久化"""
        config_path = _write_config(
            tmp_path,
            overrides={
                "features": {
                    "function_calling": {
                        "web_search": {"engines": {"tavily": {"api_key": "file-tvly"}}},
                        "scholarly_research": {
                            "providers": {"openalex": {"api_key": "file-oa"}}
                        },
                    }
                }
            },
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            assert not cfg_module._secrets_from_env
            cfg_module.save_config()
        result = _read_config(config_path)
        engines = result["features"]["function_calling"]["web_search"]["engines"]
        providers = result["features"]["function_calling"]["scholarly_research"][
            "providers"
        ]
        assert engines["tavily"]["api_key"] == "file-tvly"
        assert providers["openalex"]["api_key"] == "file-oa"

    def test_masked_search_key_save_keeps_env_value_no_leak(self, tmp_path):
        """前端传掩码搜索 Key + 环境变量 → 内存保留原值，保存后磁盘为空"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(
            os.environ, {cfg_module.TAVILY_API_KEY_ENV: "env-tvly-789"}, clear=True
        ):
            cfg_module.load_config(config_path)
            assert (
                cfg_module.get_config().features.function_calling.web_search.engines.tavily.api_key
                == "env-tvly-789"
            )
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "web_search": {
                                "engines": {"tavily": {"api_key": "***6789"}}
                            }
                        }
                    }
                }
            )
            assert (
                cfg_module.get_config().features.function_calling.web_search.engines.tavily.api_key
                == "env-tvly-789"
            )
        result = _read_config(config_path)
        assert (
            result["features"]["function_calling"]["web_search"]["engines"]["tavily"][
                "api_key"
            ]
            == ""
        ), "掩码保存不应写入环境变量 Key"
        assert "env-tvly-789" not in json.dumps(result)

    def test_manual_search_key_overrides_env_marking(self, tmp_path):
        """用户手动输入真实搜索 Key 覆盖环境变量 → 持久化新 Key"""
        config_path = _write_config(tmp_path)
        with mock.patch.dict(
            os.environ,
            {"LATEXCELLENCE_OPENALEX_API_KEY": "env-oa"},
            clear=True,
        ):
            cfg_module.load_config(config_path)
            assert (
                cfg_module.get_config().features.function_calling.scholarly_research.providers.openalex.api_key
                == "env-oa"
            )
            cfg_module.update_config_from_dict(
                {
                    "features": {
                        "function_calling": {
                            "scholarly_research": {
                                "providers": {"openalex": {"api_key": "manual-oa"}}
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
            == "manual-oa"
        ), "手动输入应持久化"
        assert (
            cfg_module.get_config().features.function_calling.scholarly_research.providers.openalex.api_key
            == "manual-oa"
        )


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


class TestProfileApiKeyMasking:
    """模型档案（llm.profiles）api_key 与顶层 llm.api_key 同一掩码协议：

    GET /api/config 脱敏为 ***xxxx；update 回传掩码值按档案 id 保留原值，
    掩码串绝不落盘（多模型配置 P1 引入的 secret 面与既有 Key 同等防护）。
    """

    PROFILE_VISION = {
        "id": "vision-pro",
        "name": "视觉模型",
        "base_url": "https://vision.example.com/v1",
        "api_key": "vision-secret-1234",
        "model": "vision-model",
        "capabilities": ["vision"],
    }

    def _write_profile_config(self, tmp_path):
        return _write_config(
            tmp_path,
            overrides={"llm": {"profiles": [dict(self.PROFILE_VISION)]}},
        )

    @pytest.mark.asyncio
    async def test_full_config_masks_profile_api_key(self, tmp_path):
        """GET /api/config 不得明文返回档案 api_key（仅后 4 位）"""
        config_path = self._write_profile_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            from main import get_full_config

            response = await get_full_config()

        masked = response["llm"]["profiles"][0]["api_key"]
        assert masked == "***1234"
        assert "vision-secret" not in json.dumps(response)

    def test_masked_profile_key_roundtrip_keeps_value(self, tmp_path):
        """前端回传掩码值 → 按档案 id 保留原 Key，掩码串不落盘"""
        config_path = self._write_profile_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "llm": {
                        "profiles": [
                            dict(self.PROFILE_VISION, api_key="***1234"),
                        ]
                    }
                }
            )
        result = _read_config(config_path)
        assert result["llm"]["profiles"][0]["api_key"] == "vision-secret-1234"

    def test_masked_profile_key_with_unknown_id_cleared(self, tmp_path):
        """掩码值但 id 匹配不到现有档案 → 置空（掩码串不得被当作真实 Key）"""
        config_path = self._write_profile_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "llm": {
                        "profiles": [
                            dict(
                                self.PROFILE_VISION, id="new-profile", api_key="***1234"
                            ),
                        ]
                    }
                }
            )
        result = _read_config(config_path)
        assert result["llm"]["profiles"][0]["api_key"] == ""

    def test_new_profile_key_persists(self, tmp_path):
        """真实新 Key 按用户输入持久化（解除掩码协议）"""
        config_path = self._write_profile_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "llm": {
                        "profiles": [
                            dict(self.PROFILE_VISION, api_key="brand-new-key"),
                        ]
                    }
                }
            )
        result = _read_config(config_path)
        assert result["llm"]["profiles"][0]["api_key"] == "brand-new-key"

    def test_empty_profile_key_clears_existing(self, tmp_path):
        """空串按用户输入处理：可清空档案 Key（区别于顶层 llm.api_key 保留语义）"""
        config_path = self._write_profile_config(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg_module.load_config(config_path)
            cfg_module.update_config_from_dict(
                {
                    "llm": {
                        "profiles": [
                            dict(self.PROFILE_VISION, api_key=""),
                        ]
                    }
                }
            )
        result = _read_config(config_path)
        assert result["llm"]["profiles"][0]["api_key"] == ""

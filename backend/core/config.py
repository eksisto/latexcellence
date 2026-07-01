"""
配置文件管理模块。

职责：
    1. 定义 config.json 对应的 Pydantic 模型（带默认值和类型校验）
    2. 加载 JSON 配置文件并校验
    3. 支持环境变量 OPENAI_API_KEY 覆盖配置中的 API Key（安全优先）
    4. 运行时热重载支持

安全策略：
    - config.json 包含 API Key，必须加入 .gitignore
    - 提供 config.example.json 作为脱敏模板，可安全提交 Git
    - 环境变量 OPENAI_API_KEY > config.json 中的 api_key
"""

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class ServerConfig(BaseModel):
    """服务器配置"""
    host: str = "0.0.0.0"              # 监听地址（0.0.0.0 表示所有网卡）
    port: int = 8000                    # HTTP 端口
    dev_mode: bool = True               # 是否开发模式（影响前端挂载方式）
    vite_dev_url: str = "http://localhost:5173"  # Vite dev server 地址


class LLMConfig(BaseModel):
    """大语言模型 API 配置"""
    api_key: str = ""                   # OpenAI API Key（也可通过环境变量提供）
    base_url: str = "https://api.openai.com/v1"  # API 端点（兼容第三方代理）
    model: str = "gpt-4o"               # 模型名称
    temperature: float = 0.3            # 生成温度（越低越确定，越高越创意）
    max_tokens: int = 4096              # 单次生成最大 token 数
    request_interval: float = 1.5       # 请求间隔（秒），避免触发 rate limit


class ProjectConfig(BaseModel):
    """LaTeX 项目管理配置"""
    default_open_dir: str = ""          # 默认项目目录（留空则每次手动选择）
    backup_on_modify: bool = True       # 修改前是否自动创建 .bak 备份
    auto_save_interval: int = 120       # 自动保存间隔（秒）


class TypoCheckConfig(BaseModel):
    """错字查找模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 5                 # 每批发送给 LLM 的段落数
    custom_prompts_dir: str = "prompts/typo/"  # 用户自定义提示词存放目录


class ExpressionOptConfig(BaseModel):
    """表达优化模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 3                 # 每批发送给 LLM 的段落数
    default_prompt: str = "请优化以下段落的学术表达..."  # 默认提示词


class FormulaOCRConfig(BaseModel):
    """公式识别模块配置"""
    enabled: bool = True                # 是否启用
    max_image_size_mb: int = 10         # 上传图片最大尺寸（MB）


class TemplateGenConfig(BaseModel):
    """模板生成模块配置"""
    enabled: bool = True                # 是否启用
    output_dir: str = "templates/"      # 生成模板的输出目录


class FeaturesConfig(BaseModel):
    """功能特性总开关"""
    typo_check: TypoCheckConfig = TypoCheckConfig()
    expression_opt: ExpressionOptConfig = ExpressionOptConfig()
    formula_ocr: FormulaOCRConfig = FormulaOCRConfig()
    template_gen: TemplateGenConfig = TemplateGenConfig()


class LoggingConfig(BaseModel):
    """日志系统配置"""
    level: str = "INFO"                 # 日志级别（DEBUG/INFO/WARNING/ERROR）
    dir: str = "logs/"                  # 日志文件输出目录


class AppConfig(BaseModel):
    """应用总配置——顶层模型，包含所有子配置"""
    server: ServerConfig = ServerConfig()
    llm: LLMConfig = LLMConfig()
    project: ProjectConfig = ProjectConfig()
    features: FeaturesConfig = FeaturesConfig()
    logging: LoggingConfig = LoggingConfig()


# 模块级全局变量：单例模式存储配置对象和路径
_config: Optional[AppConfig] = None
_config_path: Optional[Path] = None


def load_config(config_path: str = "config.json") -> AppConfig:
    """
    加载并校验配置文件。

    流程：
        1. 读取 JSON 文件
        2. 检查环境变量 OPENAI_API_KEY，若存在则覆盖配置中的 key
        3. 通过 Pydantic 进行类型校验和默认值填充
        4. 缓存到全局变量供后续调用

    参数：
        config_path: 配置文件路径，默认为 "config.json"

    返回：
        校验后的 AppConfig 对象
    """
    global _config, _config_path
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 安全策略：环境变量优先级高于配置文件
    if "llm" in data and "api_key" in data["llm"]:
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key:
            data["llm"]["api_key"] = env_key

    # Pydantic 自动校验类型+填充默认值
    _config = AppConfig(**data)
    _config_path = path.resolve()
    return _config


def get_config() -> AppConfig:
    """
    获取当前缓存的配置对象。
    必须在调用 load_config() 之后才能调用。
    """
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    return _config


def reload_config() -> AppConfig:
    """
    热重载配置：从上次加载的路径重新读取并解析配置文件。
    适用于运行时修改 config.json 后在不重启服务的情况下刷新配置。
    """
    if _config_path is None:
        raise RuntimeError("Config has never been loaded.")
    return load_config(str(_config_path))

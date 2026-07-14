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

import copy
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
    """文本校对模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 5                 # 每批发送给 LLM 的段落数
    custom_prompts_dir: str = "prompts/typo/"  # 用户自定义提示词存放目录


class ExpressionOptConfig(BaseModel):
    """表达优化模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 3                 # 每批发送给 LLM 的段落数
    default_prompt: str = "请优化以下段落的学术表达..."  # 默认提示词
    custom_prompts_dir: str = "prompts/expression/"  # 用户自定义提示词存放目录


class FormulaOCRConfig(BaseModel):
    """公式识别模块配置"""
    enabled: bool = True                # 是否启用
    max_image_size_mb: int = 10         # 上传图片最大尺寸（MB）


class TableOCRConfig(BaseModel):
    """表格 OCR 识别模块配置"""
    enabled: bool = True                # 是否启用
    max_image_size_mb: int = 10         # 上传图片最大尺寸（MB）


class TemplateGenConfig(BaseModel):
    """模板生成模块配置"""
    enabled: bool = True                # 是否启用
    output_dir: str = "templates/"      # 生成模板的输出目录


class FormattingConfig(BaseModel):
    """源码格式化模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 5                 # 每批发送给 LLM 的段落数


class CompileConfig(BaseModel):
    """自动编译模块配置"""
    enabled: bool = True                # 是否启用
    latexmk_timeout: int = 120          # latexmk 编译超时时间（秒）


class GitDiffConfig(BaseModel):
    """Git 差异高亮模块配置"""
    enabled: bool = True                # 是否启用


class MarkdownConverterConfig(BaseModel):
    """Markdown 转 LaTeX 模块配置"""
    enabled: bool = True                # 是否启用


class StyleModifyConfig(BaseModel):
    """LaTeX 样式修改模块配置"""
    enabled: bool = True                # 是否启用


class TerminologyCheckConfig(BaseModel):
    """术语一致性检查模块配置"""
    enabled: bool = True                # 是否启用
    batch_size: int = 5                 # 每批发送给 LLM 的段落数


class FeaturesConfig(BaseModel):
    """功能特性总开关"""
    typo_check: TypoCheckConfig = TypoCheckConfig()
    expression_opt: ExpressionOptConfig = ExpressionOptConfig()
    formula_ocr: FormulaOCRConfig = FormulaOCRConfig()
    table_ocr: TableOCRConfig = TableOCRConfig()
    template_gen: TemplateGenConfig = TemplateGenConfig()
    formatting: FormattingConfig = FormattingConfig()
    compile: CompileConfig = CompileConfig()
    git_diff: GitDiffConfig = GitDiffConfig()
    markdown_converter: MarkdownConverterConfig = MarkdownConverterConfig()
    style_modify: StyleModifyConfig = StyleModifyConfig()
    terminology_check: TerminologyCheckConfig = TerminologyCheckConfig()


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


def save_config() -> None:
    """
    将当前缓存的配置对象持久化写回 config.json。

    常用于运行时修改配置（如记录最近打开的项目路径）后保存到磁盘。
    """
    global _config, _config_path
    if _config is None or _config_path is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    data = _config.model_dump()
    with open(_config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def update_and_save(**kwargs) -> AppConfig:
    """
    更新配置字段并持久化到磁盘。

    用法：
        update_and_save(project__default_open_dir="/path/to/project")

    参数：
        **kwargs: 使用双下划线分隔嵌套字段路径，
                  如 llm__temperature=0.5 修改 llm.temperature
    """
    global _config
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")

    for key, value in kwargs.items():
        parts = key.split("__")
        target = _config
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], value)

    save_config()
    return _config


def _deep_merge(base: dict, updates: dict) -> dict:
    """
    递归深度合并两个字典。

    参数：
        base: 基础字典（被合并的目标）
        updates: 要合并的新值

    返回：
        合并后的新字典（不修改原字典）
    """
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def update_config_from_dict(updates: dict) -> AppConfig:
    """
    用字典数据更新配置并持久化。

    支持部分更新：只需传入需要修改的字段即可，未传入的字段保持不变。
    若传入的 llm.api_key 以 "***" 开头（前端掩码），则保留原值不变。

    参数：
        updates: 包含要更新字段的字典，支持嵌套结构

    返回：
        更新后的 AppConfig 对象
    """
    global _config, _config_path
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")

    current = _config.model_dump()

    # 若 api_key 被掩码，保留原值
    if "llm" in updates and isinstance(updates["llm"], dict):
        api_key = updates["llm"].get("api_key", "")
        if api_key.startswith("***") or api_key == "":
            updates["llm"]["api_key"] = current["llm"]["api_key"]

    merged = _deep_merge(current, updates)
    _config = AppConfig(**merged)
    save_config()
    return _config

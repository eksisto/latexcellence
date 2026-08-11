"""
配置文件管理模块。

职责：
    1. 定义 config.json 对应的 Pydantic 模型（带默认值和类型校验）
    2. 加载 JSON 配置文件并校验
    3. 支持环境变量 LX_LLM_KEY 覆盖配置中的 API Key（安全优先）
    4. 运行时热重载支持

安全策略：
    - config.json 可包含 API Key（首次安装时方便），但必须加入 .gitignore
    - 提供 config.example.json 作为脱敏模板，可安全提交 Git
    - 环境变量 LX_LLM_KEY 优先级最高，且从环境变量注入的 Key **禁止写回磁盘**
    - save_config() 自动识别 Key 来源，环境变量 Key 永不落盘
"""

import copy
import json
import os
from pathlib import Path


from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """服务器配置"""

    host: str = "0.0.0.0"  # 监听地址（0.0.0.0 表示所有网卡）
    port: int = 8000  # HTTP 端口
    dev_mode: bool = True  # 是否开发模式（影响前端挂载方式）
    vite_dev_url: str = "http://localhost:5173"  # Vite dev server 地址


class LLMConfig(BaseModel):
    """大语言模型 API 配置"""

    api_key: str = ""  # OpenAI API Key（也可通过环境变量提供）
    base_url: str = "https://api.deepseek.com"  # API 端点（兼容第三方代理）
    model: str = "deepseek-flash"  # 模型名称
    temperature: float = Field(0.3, le=2)  # 生成温度（越低越确定，越高越创意）
    max_tokens: int = Field(8192, ge=1)  # 单次生成最大 token 数
    request_interval: float = Field(1.5, ge=0)  # 请求间隔（秒），避免触发 rate limit
    max_concurrency: int = Field(5, ge=1)  # 最大并发 LLM 请求数（Semaphore）


class TypoCheckConfig(BaseModel):
    """文本校对模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 5  # 每批发送给 LLM 的段落数


class ExpressionOptConfig(BaseModel):
    """表达优化模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 3  # 每批发送给 LLM 的段落数
    default_prompt: str = "请优化以下段落的学术表达..."  # 默认提示词
    custom_prompts_dir: str = "prompts/expression/"  # 用户自定义提示词存放目录


class FormulaOCRConfig(BaseModel):
    """公式识别模块配置"""

    enabled: bool = True  # 是否启用
    max_image_size_mb: int = 10  # 上传图片最大尺寸（MB）
    format_latex: bool = True  # 是否启用统一 LaTeX 格式化层（latexindent / 内置兜底）


class TableOCRConfig(BaseModel):
    """表格 OCR 识别模块配置"""

    enabled: bool = True  # 是否启用
    max_image_size_mb: int = 10  # 上传图片最大尺寸（MB）
    format_latex: bool = True  # 是否启用统一 LaTeX 格式化层（latexindent / 内置兜底）


class TemplateGenConfig(BaseModel):
    """模板生成模块配置"""

    enabled: bool = True  # 是否启用
    output_dir: str = "templates/"  # 生成模板的输出目录
    # ── 自主生成 Agent 配置 ──
    agent_max_rounds: int = Field(30, ge=1, le=50)  # Agent 最大工具调用轮数（防发散）
    agent_max_files: int = Field(30, ge=1, le=200)  # 单次生成的最大文件数
    agent_max_file_size_kb: int = Field(512, ge=1)  # 单个文件大小上限（KB）
    agent_compile_enabled: bool = True  # Agent 是否调用 latexmk 做编译验证
    agent_compile_timeout: int = Field(120, ge=1)  # Agent 编译超时（秒）
    # ── 沙箱自动清理配置 ──
    cleanup_enabled: bool = True  # 是否启用过期沙箱自动清理
    cleanup_interval_seconds: int = Field(
        3600, ge=60
    )  # 清理检查间隔（秒），默认 1 小时
    cleanup_retention_days: int = Field(7, ge=1)  # 沙箱保留天数，超过则删除


class CodeReviewConfig(BaseModel):
    """LaTeX 代码审查模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 5  # 每批发送给 LLM 的段落数


class CompileConfig(BaseModel):
    """自动编译模块配置"""

    enabled: bool = True  # 是否启用
    latexmk_timeout: int = 120  # latexmk 编译超时时间（秒）
    engine: str = "auto"  # 编译引擎：auto/pdflatex/xelatex/lualatex
    pdfa: bool = False  # 是否启用 PDF/A-2b 归档格式输出


class GitDiffConfig(BaseModel):
    """Git 差异高亮模块配置"""

    enabled: bool = True  # 是否启用
    default_engine: str = "difflib"  # 默认 diff 引擎：difflib / latexdiff
    default_markup_type: str = "UNDERLINE"  # latexdiff 默认标记类型
    default_math_markup: str = (
        "fine"  # latexdiff 数学公式标记粒度：fine/coarse/off/whole
    )
    default_color_add: str = "#D7E8B1"  # 新增内容颜色（HEX）
    default_color_del: str = "#FFA3A3"  # 删除内容颜色（HEX）


class FormatConverterConfig(BaseModel):
    """文档格式转换模块配置（Markdown/DOCX/HTML ↔ LaTeX）"""

    enabled: bool = True  # 是否启用
    allowed_extensions: list[str] = [
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".docx",
    ]  # 允许上传的文件扩展名
    max_file_size_mb: int = 20  # 上传文件最大尺寸（MB）


class StyleModifyAgentConfig(BaseModel):
    """style_modify 项目 Agent 配置"""

    enabled: bool = True  # 是否启用项目 Agent 模式
    sessions_dir: str = "style_modify_sessions/"  # 会话缓存目录
    max_rounds: int = Field(20, ge=1, le=50)  # Agent 最大工具轮数
    max_tool_calls: int = Field(100, ge=1, le=500)  # 单轮会话工具调用上限
    max_compile_runs: int = Field(10, ge=1, le=100)  # 单轮会话编译次数上限
    max_file_size_kb: int = Field(1024, ge=1)  # 单个候选文件大小上限（KB）
    max_session_size_kb: int = Field(10240, ge=1)  # 单会话候选总量上限（KB）
    max_tool_output_chars: int = Field(12000, ge=500)  # 工具输出回传上限
    shadow_compile_timeout: int = Field(120, ge=1)  # shadow 编译超时（秒）
    allowed_suffixes: list[str] = Field(
        default_factory=lambda: [".tex", ".sty", ".cls", ".bib"]
    )  # 允许修改的文件后缀
    cleanup_enabled: bool = True  # 是否启用过期会话自动清理
    cleanup_interval_seconds: int = Field(3600, ge=60)  # 清理检查间隔（秒）
    cleanup_retention_days: int = Field(7, ge=1)  # 会话保留天数


class StyleModifyConfig(BaseModel):
    """LaTeX 样式修改模块配置"""

    enabled: bool = True  # 是否启用
    agent: StyleModifyAgentConfig = StyleModifyAgentConfig()  # 项目 Agent 配置


class TerminologyCheckConfig(BaseModel):
    """术语一致性检查模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 5  # 每批发送给 LLM 的段落数


class ParagraphTranslateConfig(BaseModel):
    """逐段翻译模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 5  # 每批发送给 LLM 的段落数


class ProjectToolsConfig(BaseModel):
    """项目文件浏览工具集配置（Function Calling 全局工具）"""

    enabled: bool = True  # 是否启用


class EditToolsConfig(BaseModel):
    """段落编辑工具集配置（Function Calling 全局工具）"""

    enabled: bool = True
    require_confirmation: bool = True  # 敏感操作是否需要前端确认弹窗


class WritingChatConfig(BaseModel):
    """写作助手 Chat 模块配置"""

    enabled: bool = True  # 是否启用
    compression_threshold_tokens: int = 200000  # 触发上下文压缩的累计 token 阈值
    sessions_dir: str = "writing_chat_sessions/"  # 会话持久化目录
    custom_prompts_dir: str = "prompts/writing_chat/"  # 助手提示词存放目录
    summary_temperature: float = 0.2  # 生成压缩摘要时的温度
    summary_max_tokens: int = 50000  # 压缩摘要最大生成 token 数
    # 是否在聊天中启用联网搜索（受全局 web_search.enabled 约束）
    web_search_enabled: bool = True


class DuckDuckGoConfig(BaseModel):
    """DuckDuckGo 搜索引擎配置"""

    enabled: bool = True


class SearXNGConfig(BaseModel):
    """SearXNG 搜索引擎配置"""

    enabled: bool = True
    base_url: str = ""  # SearXNG 实例地址（如 http://localhost:8080）


class TavilyConfig(BaseModel):
    """Tavily 搜索引擎配置"""

    enabled: bool = False
    api_key: str = ""  # Tavily API Key（https://app.tavily.com 获取）


class WebSearchEnginesConfig(BaseModel):
    """联网搜索引擎集合 — 每个引擎独立开关和参数"""

    duckduckgo: DuckDuckGoConfig = DuckDuckGoConfig()
    searxng: SearXNGConfig = SearXNGConfig()
    tavily: TavilyConfig = TavilyConfig()


class QueryRewritingConfig(BaseModel):
    """查询重写模块配置"""

    enabled: bool = True  # 是否启用 LLM 查询重写


class WebSearchConfig(BaseModel):
    """联网搜索模块配置"""

    enabled: bool = False  # 是否启用（默认关闭，用户手动开启）
    primary_engine: str = (
        "duckduckgo"  # 主搜索引擎标识（duckduckgo / searxng / tavily）
    )
    max_results: int = Field(5, ge=1, le=20)  # 默认返回结果数上限
    timeout_seconds: int = Field(10, ge=1)  # 单次搜索超时（秒）
    engines: WebSearchEnginesConfig = WebSearchEnginesConfig()
    deep_read_pages: int = Field(2, ge=0, le=5)  # Deep Read 抓取前 N 条正文（0=禁用）
    deep_read_max_chars: int = Field(4000, ge=500, le=20000)  # 每页最大抓取字符数
    query_rewriting: QueryRewritingConfig = QueryRewritingConfig()


class ScholarlyResearchProviderConfig(BaseModel):
    """单个学术数据源配置。"""

    enabled: bool = True
    api_key: str = ""


class ScholarlyResearchProvidersConfig(BaseModel):
    """学术研究数据源集合。"""

    openalex: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig()
    semantic_scholar: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False, api_key=""
    )
    crossref: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False
    )
    arxiv: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False
    )
    core: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False, api_key=""
    )
    unpaywall: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False, api_key=""
    )
    pubmed: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False, api_key=""
    )
    datacite: ScholarlyResearchProviderConfig = ScholarlyResearchProviderConfig(
        enabled=False
    )


class ScholarlyResearchConfig(BaseModel):
    """学术论文检索与引文追踪工具配置。"""

    enabled: bool = False
    primary_source: str = "openalex"
    max_results: int = Field(20, ge=1, le=100)
    timeout_seconds: int = Field(30, ge=1)
    providers: ScholarlyResearchProvidersConfig = ScholarlyResearchProvidersConfig()


class BibProviderConfig(BaseModel):
    """单个 DOI 数据源配置"""

    enabled: bool = True


class BibProvidersConfig(BaseModel):
    """DOI 数据源集合配置"""

    crossref: BibProviderConfig = BibProviderConfig()
    arxiv: BibProviderConfig = BibProviderConfig()
    datacite: BibProviderConfig = BibProviderConfig()
    openalex: BibProviderConfig = BibProviderConfig(enabled=False)
    europepmc: BibProviderConfig = BibProviderConfig(enabled=False)
    semantic_scholar: BibProviderConfig = BibProviderConfig(enabled=False)


class BibManagerConfig(BaseModel):
    """参考文献管理模块配置"""

    enabled: bool = True  # 是否启用
    format_configs_dir: str = "formats/"  # 保存的格式配置文件目录
    providers: BibProvidersConfig = BibProvidersConfig()  # DOI 数据源配置


class PlotGenConfig(BaseModel):
    """科研绘图 Agent 配置"""

    enabled: bool = True
    output_dir: str = "plots/"
    agent_max_rounds: int = Field(20, ge=1, le=50)
    agent_max_files: int = Field(30, ge=1, le=200)
    agent_max_file_size_kb: int = Field(1024, ge=1)
    max_data_size_mb: int = Field(50, ge=1)
    max_data_rows: int = Field(1_000_000, ge=1)
    sandbox_exec_timeout: int = Field(60, ge=1)
    sandbox_max_stdout_chars: int = Field(8000, ge=500)
    python_interpreter: str = ""
    tikz_compile_enabled: bool = True
    tikz_compile_timeout: int = Field(120, ge=1)
    default_style: str = "nature"
    cleanup_enabled: bool = True
    cleanup_interval_seconds: int = Field(3600, ge=60)
    cleanup_retention_days: int = Field(7, ge=1)


class FunctionCallingConfig(BaseModel):
    """Function Calling 工具配置（全局工具，LLM 自主调用）"""

    web_search: WebSearchConfig = WebSearchConfig()
    project_tools: ProjectToolsConfig = ProjectToolsConfig()
    edit_tools: EditToolsConfig = EditToolsConfig()
    scholarly_research: ScholarlyResearchConfig = ScholarlyResearchConfig()


class FeaturesConfig(BaseModel):
    """功能特性总开关"""

    typo_check: TypoCheckConfig = TypoCheckConfig()
    expression_opt: ExpressionOptConfig = ExpressionOptConfig()
    formula_ocr: FormulaOCRConfig = FormulaOCRConfig()
    table_ocr: TableOCRConfig = TableOCRConfig()
    template_gen: TemplateGenConfig = TemplateGenConfig()
    code_review: CodeReviewConfig = CodeReviewConfig()
    compile: CompileConfig = CompileConfig()
    git_diff: GitDiffConfig = GitDiffConfig()
    format_converter: FormatConverterConfig = FormatConverterConfig()
    style_modify: StyleModifyConfig = StyleModifyConfig()
    terminology_check: TerminologyCheckConfig = TerminologyCheckConfig()
    paragraph_translate: ParagraphTranslateConfig = ParagraphTranslateConfig()
    writing_chat: WritingChatConfig = WritingChatConfig()
    bib_manager: BibManagerConfig = BibManagerConfig()
    plot_gen: PlotGenConfig = PlotGenConfig()
    function_calling: FunctionCallingConfig = FunctionCallingConfig()


class LoggingConfig(BaseModel):
    """日志系统配置"""

    level: str = "INFO"  # 日志级别（DEBUG/INFO/WARNING/ERROR）
    dir: str = "logs/"  # 日志文件输出目录


class HttpConfig(BaseModel):
    """HTTP 请求全局配置"""

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    )


class AppConfig(BaseModel):
    """应用总配置——顶层模型，包含所有子配置"""

    server: ServerConfig = ServerConfig()
    llm: LLMConfig = LLMConfig()
    http: HttpConfig = HttpConfig()
    features: FeaturesConfig = FeaturesConfig()
    logging: LoggingConfig = LoggingConfig()


# 模块级全局变量：单例模式存储配置对象和路径
_config: AppConfig | None = None
_config_path: Path | None = None
_api_key_from_env: bool = False  # 标记当前 api_key 是否来自环境变量（防落盘）


def load_config(config_path: str = "config.json") -> AppConfig:
    """
    加载并校验配置文件。

    流程：
        1. 读取 JSON 文件
        2. 检查环境变量 LX_LLM_KEY，若存在则覆盖配置中的 key
        3. 通过 Pydantic 进行类型校验和默认值填充
        4. 缓存到全局变量供后续调用

    参数：
        config_path: 配置文件路径，默认为 "config.json"

    返回：
        校验后的 AppConfig 对象
    """
    global _config, _config_path, _api_key_from_env
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 安全策略：环境变量优先级高于配置文件，且标记来源防止落盘
    env_key = os.environ.get("LX_LLM_KEY", "")
    if env_key:
        if "llm" not in data:
            data["llm"] = {}
        data["llm"]["api_key"] = env_key
        _api_key_from_env = True
    else:
        _api_key_from_env = False

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


def save_config() -> None:
    """
    将当前缓存的配置对象持久化写回 config.json。

    安全策略：
        - 若 api_key 来自环境变量（_api_key_from_env = True），
          落盘前将其清空，防止泄漏到磁盘。
        - 若 api_key 由用户通过 UI 手动设置，则正常持久化。
    """
    global _config, _config_path
    if _config is None or _config_path is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    data = _config.model_dump()
    # 环境变量来源的 API Key 不得写入磁盘
    if _api_key_from_env:
        data["llm"]["api_key"] = ""
    with open(_config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    若传入完整的真实 Key，则视为用户手动设置，更新内存状态并允许持久化。
    来自环境变量的 Key 不会因本次操作而写回磁盘（由 save_config() 保障）。

    参数：
        updates: 包含要更新字段的字典，支持嵌套结构

    返回：
        更新后的 AppConfig 对象
    """
    global _config, _config_path, _api_key_from_env
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")

    current = _config.model_dump()

    # 处理 api_key：掩码/空 → 保留原值；真实 Key → 更新并标记为用户设置
    if "llm" in updates and isinstance(updates["llm"], dict):
        api_key = updates["llm"].get("api_key", "")
        if api_key.startswith("***") or api_key == "":
            updates["llm"]["api_key"] = current["llm"]["api_key"]
        else:
            # 用户提供了真实 Key，更新内存并解除环境变量标记
            _api_key_from_env = False

    # 所有已启用 API Key 的数据源均通过设置页以掩码返回，掩码表示保留原配置。
    function_calling = updates.get("features", {}).get("function_calling", {})
    scholarly_research = function_calling.get("scholarly_research", {})
    providers = scholarly_research.get("providers", {})
    for provider_key in ("openalex", "semantic_scholar", "core", "unpaywall", "pubmed"):
        p = providers.get(provider_key, {})
        if isinstance(p, dict) and "api_key" in p:
            k = p.get("api_key", "")
            if k.startswith("***"):
                p["api_key"] = current["features"]["function_calling"][
                    "scholarly_research"
                ]["providers"][provider_key]["api_key"]

    # Tavily 搜索 API Key：前端掩码 → 保留原值；空值/真实 Key → 按用户输入处理
    web_search = function_calling.get("web_search", {})
    engines = web_search.get("engines", {})
    tavily = engines.get("tavily", {})
    if isinstance(tavily, dict) and "api_key" in tavily:
        k = tavily.get("api_key", "")
        if k.startswith("***"):
            tavily["api_key"] = current["features"]["function_calling"]["web_search"][
                "engines"
            ]["tavily"]["api_key"]

    merged = _deep_merge(current, updates)
    _config = AppConfig(**merged)
    save_config()

    # LLM 配置变更时，重置 LLMClient 单例使新配置立即生效
    if "llm" in updates:
        from backend.core.llm_client import get_llm_client

        get_llm_client().reset()

    # 联网搜索配置变更时，重置 SearchManager 单例使新配置立即生效
    if "features" in updates and "function_calling" in updates.get("features", {}):
        from backend.utils.web_search import reset_search_manager

        reset_search_manager()

    return _config

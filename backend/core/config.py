"""
配置文件管理模块。

职责：
    1. 定义 config.json 对应的 Pydantic 模型（带默认值和类型校验）
    2. 加载 JSON 配置文件并校验
    3. 支持环境变量覆盖配置中的敏感项（见 SECRET_ENV_PATHS 注册表：
       LLM Key / SMTP 密码 / Tavily 搜索与学术数据源 API Key）（安全优先）
    4. 运行时热重载支持

安全策略：
    - config.json 可包含 API Key（首次安装时方便），但必须加入 .gitignore
    - 提供 config.example.json 作为脱敏模板，可安全提交 Git
    - 注册表中的敏感项均可经环境变量注入，环境变量优先级最高，
      且从环境变量注入的值**禁止写回磁盘**
    - save_config() 自动识别敏感项来源，环境变量注入值永不落盘
"""

import copy
import ipaddress
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal


from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from backend.utils.file_ops import atomic_write


class ServerConfig(BaseModel):
    """服务器配置"""

    host: str = "0.0.0.0"  # 监听地址（0.0.0.0 表示所有网卡）
    port: int = 8000  # HTTP 端口
    dev_mode: bool = True  # 是否开发模式（影响前端挂载方式）
    vite_dev_url: str = "http://localhost:5173"  # Vite dev server 地址
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://latexcellence.example.com",
    ]  # CORS 白名单（前端部署域名 + 本地开发地址）


class SecurityConfig(BaseModel):
    """跨域访问安全配置"""

    access_token: str = ""  # 共享访问令牌（留空表示不校验，用于纯本地开发）


# ── 多模型支持：模型档案 + 槽位指派（multi-model-plan §4）──────────────

# 内置默认档案的保留 id：由 llm 顶层字段在读取侧合成（自动迁移，不回写文件），
# 用户自定义档案禁止占用该 id。
BUILTIN_PROFILE_ID = "__default__"

# 固定槽位（角色）集合：assignments 只允许这些键。
# 空串值 = 跟随 default 槽；default 槽空串 = 内置默认档案。
LLM_ROLE_KEYS: tuple[str, ...] = (
    "default",
    "agent",
    "ocr",
    "writing_chat",
    "typo_check",
    "expression_opt",
    "code_review",
    "terminology_check",
    "paragraph_translate",
)

# 档案能力声明闭集：vision 参与槽位硬校验（OCR 必需）；reasoning 为提示性标注。
LLM_CAPABILITIES: tuple[str, ...] = ("vision", "reasoning")

# 档案 id 格式：非空，仅字母 / 数字 / 下划线 / 连字符
_PROFILE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def default_assignments() -> dict[str, str]:
    """返回全部槽位为空串的指派表（每次构造新对象，避免共享可变默认）。"""
    return {role: "" for role in LLM_ROLE_KEYS}


class ModelProfile(BaseModel):
    """模型档案：一个可独立调用的 LLM 连接（多模型支持，multi-model-plan §4）。

    连接三件套（base_url / api_key / model）必填；capabilities 为闭集能力声明。
    可选覆盖项（temperature / max_tokens / request_interval / max_concurrency /
    context_window_tokens / models_probe_url / extra_body）缺省或为 null / 0 / {}
    时表示继承 llm 顶层默认值，「档案覆盖项 + 顶层字段」的合并视图由客户端层
    在每次请求时实时解析（P2 落实），配置层不做缓存。
    extra="forbid"：档案字段拼写错误在加载期直接报错，防止静默失效。
    """

    model_config = ConfigDict(extra="forbid")

    # 唯一标识：^[a-zA-Z0-9_-]+$；__default__ 为内置档案保留 id
    id: str
    name: str  # 展示名
    base_url: str  # API 端点（必填）
    api_key: str  # API Key（必填，可为空串以兼容免鉴权本地端点）
    model: str  # 模型名称（必填）
    # 能力声明（闭集：vision / reasoning），空列表表示未声明任何能力
    capabilities: list[str] = Field(default_factory=list)
    # ── 以下为可选覆盖项：null / 0 / {} 表示继承 llm 顶层默认值 ──
    temperature: float | None = Field(default=None, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    request_interval: float | None = Field(default=None, ge=0)  # 每档案独立节流
    max_concurrency: int | None = Field(default=None, ge=1)  # 每档案独立 Semaphore
    context_window_tokens: int | None = 0  # 0 / null = 继承顶层（顶层 0 = 自动探测）
    models_probe_url: str = ""  # 空串 = 继承顶层
    extra_body: dict[str, Any] = Field(default_factory=dict)  # 空 {} = 继承顶层

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        """档案 id 非空且格式合法；保留 id 由 LLMConfig 层门禁拒绝。"""
        value = v.strip()
        if not value:
            raise ValueError("模型档案 id 不能为空")
        if not _PROFILE_ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"模型档案 id 非法：{value!r}，仅允许字母、数字、下划线和连字符"
            )
        return value

    @field_validator("base_url", "model")
    @classmethod
    def _validate_connection_fields(cls, v: str, info: ValidationInfo) -> str:
        """连接三件套中的 base_url / model 不得为空（api_key 允许空串）。"""
        if not v.strip():
            raise ValueError(f"模型档案 {info.field_name} 不能为空")
        return v

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        """能力声明闭集校验：仅允许 vision / reasoning。"""
        invalid = [item for item in v if item not in LLM_CAPABILITIES]
        if invalid:
            raise ValueError(
                f"模型档案能力声明含非法值：{invalid}，仅允许："
                f"{'、'.join(LLM_CAPABILITIES)}"
            )
        return v


class LLMConfig(BaseModel):
    """大语言模型 API 配置"""

    api_key: str = ""  # OpenAI API Key（也可通过环境变量提供）
    base_url: str = "https://api.deepseek.com"  # API 端点（兼容第三方代理）
    model: str = "deepseek-flash"  # 模型名称
    temperature: float = Field(0.3, le=2)  # 生成温度（越低越确定，越高越创意）
    max_tokens: int = Field(16384, ge=1)  # 单次生成最大 token 数
    request_interval: float = Field(1.5, ge=0)  # 请求间隔（秒），避免触发 rate limit
    max_concurrency: int = Field(5, ge=1)  # 最大并发 LLM 请求数（Semaphore）
    # 当前模型的上下文窗口 token 数；0 表示自动探测（API 探测 → 错误信息学习 →
    # 内置模型表 → 默认值）。非 0 时跳过探测直接使用，适合代理不暴露元数据或
    # 自定义模型别名的情况。
    context_window_tokens: int = Field(0, ge=0)
    # 可选的 /models 探测直连地址（如 http://<host>:8000/v1/models），
    # 用于绕过不透传 /models 路由的网关；为空时使用 base_url。
    # 仅当探测地址与 base_url 同源时才携带 API key，跨域探测不发送密钥。
    models_probe_url: str = ""
    # 透传给 OpenAI 兼容 API 的额外请求体参数（openai SDK extra_body 语义），
    # 用于厂商私有参数，例如思维链模型降档 {"reasoning_effort": "low"}、
    # 通义 {"enable_thinking": false}、智谱 {"thinking": {"reasoning_budget": "low"}}。
    # 留空 {} 时不发送任何额外参数，不支持的模型不会因此报错（按需显式开启）。
    extra_body: dict[str, Any] = Field(default_factory=dict)
    # ── 多模型支持（multi-model-plan §4）──
    # 模型档案列表：为空时读取侧自动合成内置默认档案（builtin_default_profile /
    # effective_profiles），不回写配置文件，旧单模型配置零迁移成本。
    profiles: list[ModelProfile] = Field(default_factory=list)
    # 槽位指派表：固定 9 个角色键（LLM_ROLE_KEYS），值为档案 id；空串 = 跟随
    # default 槽（default 槽空串 = 内置默认档案）。缺失键加载时自动补空串。
    assignments: dict[str, str] = Field(default_factory=default_assignments)

    @model_validator(mode="before")
    @classmethod
    def _normalize_assignments(cls, data):
        """指派表规范化：未知槽位直接拒绝、缺失槽位补空串、null 视为空串。"""
        if not isinstance(data, dict):
            return data
        raw = data.get("assignments")
        if raw is None:
            return data  # 缺省时由字段默认工厂补全
        if not isinstance(raw, dict):
            raise ValueError("llm.assignments 必须是对象（槽位 → 档案 id 的映射）")
        unknown = sorted(str(key) for key in raw if key not in LLM_ROLE_KEYS)
        if unknown:
            raise ValueError(
                f"llm.assignments 含未知槽位：{'、'.join(unknown)}；"
                f"支持的槽位：{'、'.join(LLM_ROLE_KEYS)}"
            )
        normalized = default_assignments()
        for role in LLM_ROLE_KEYS:
            value = raw.get(role)
            # null 视为空串；其余交由 Pydantic str 类型校验
            normalized[role] = "" if value is None else value
        data = dict(data)
        data["assignments"] = normalized
        return data

    @model_validator(mode="after")
    def _validate_profiles_and_assignments(self) -> "LLMConfig":
        """多模型配置硬校验门禁（multi-model-plan §5.3）。

        启动加载与 /api/config 保存共用同一道门禁（Pydantic 校验失败一律拒绝）：
        1. 档案 id 唯一，且不得占用内置档案保留 id（格式由 ModelProfile 层校验）；
        2. assignments 引用的档案必须存在；
        3. OCR 槽两级解析结果必须具备 vision 能力（视觉硬需求，无替代）。
        """
        seen: dict[str, int] = {}
        for index, profile in enumerate(self.profiles, start=1):
            if profile.id == BUILTIN_PROFILE_ID:
                raise ValueError(
                    f'模型档案 id "{BUILTIN_PROFILE_ID}" 为内置默认档案的'
                    "保留 id，请改用其他 id"
                )
            if profile.id in seen:
                raise ValueError(
                    f'模型档案 id 重复："{profile.id}"（第 {seen[profile.id]} 与'
                    f"第 {index} 个档案），请为每个档案使用唯一 id"
                )
            seen[profile.id] = index

        available = "、".join(seen) if seen else "（无，请先在 profiles 中定义档案）"
        for role in LLM_ROLE_KEYS:
            target = self.assignments[role]
            if target and target not in seen:
                raise ValueError(
                    f'槽位 "{role}" 指派的模型档案 "{target}" 不存在；'
                    f"当前可用档案：{available}"
                )

        ocr_profile = self.resolve_role("ocr")
        if "vision" not in ocr_profile.capabilities:
            raise ValueError(
                f'槽位 "ocr" 解析到的模型档案 "{ocr_profile.id}"'
                f"（{ocr_profile.name}）缺少 vision 能力；OCR 功能必须使用"
                "视觉模型，请为该档案勾选 vision 能力、改派其他视觉档案，"
                "或清空该槽位指派（留空时跟随 default 槽）"
            )
        return self

    def builtin_default_profile(self) -> ModelProfile:
        """由顶层字段合成内置默认档案（id=__default__），每次调用构造新对象。

        自动迁移核心（multi-model-plan §4）：不回写文件、不做任何缓存，仅在
        读取侧合成。存量单模型一直承担包括 OCR 在内的全部调用，故内置档案
        视为 vision + reasoning 全能力，保证旧配置迁移后零感知（不产生新的
        校验失败）。
        """
        return ModelProfile(
            id=BUILTIN_PROFILE_ID,
            name="全局默认模型",
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            capabilities=list(LLM_CAPABILITIES),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            request_interval=self.request_interval,
            max_concurrency=self.max_concurrency,
            context_window_tokens=self.context_window_tokens,
            models_probe_url=self.models_probe_url,
            extra_body=dict(self.extra_body),
        )

    def effective_profiles(self) -> list[ModelProfile]:
        """有效档案列表（自动迁移视图）：profiles 为空时合成内置档案。

        profiles 非空时原样返回用户档案（不合成、不追加内置档案）；内置默认
        档案始终是两级解析的最终兜底（见 resolve_role），与本列表是否包含它
        无关——本列表面向档案管理 / 展示场景。
        """
        if self.profiles:
            return list(self.profiles)
        return [self.builtin_default_profile()]

    def resolve_role(self, role: str) -> ModelProfile:
        """两级槽位解析（multi-model-plan §4），纯函数式、供路由器（P2）复用。

        规则：assignments[role] 非空 → 该档案；为空 → assignments["default"]
        指派的档案；仍为空 → 内置默认档案（顶层字段合成）。未知角色抛
        ValueError。
        """
        if role not in LLM_ROLE_KEYS:
            raise ValueError(
                f'未知的模型槽位："{role}"；支持的槽位：{"、".join(LLM_ROLE_KEYS)}'
            )
        target = self.assignments.get(role) or self.assignments.get("default") or ""
        if target:
            profile_map = {profile.id: profile for profile in self.profiles}
            profile = profile_map.get(target)
            if profile is None:
                # 正常情况下已被校验门禁拦截，此处为防御性兜底
                raise ValueError(f'槽位 "{role}" 指派的模型档案 "{target}" 不存在')
            return profile
        return self.builtin_default_profile()


class TypoCheckConfig(BaseModel):
    """文本校对模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 5  # 每批发送给 LLM 的段落数


class ExpressionOptConfig(BaseModel):
    """表达优化模块配置"""

    enabled: bool = True  # 是否启用
    batch_size: int = 3  # 每批发送给 LLM 的段落数
    default_prompt: str = "请优化以下段落的学术表达..."  # 默认提示词
    custom_prompts_dir: str = "data/prompts/expression/"  # 用户自定义提示词存放目录


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


class FontWhitelistEntry(BaseModel):
    """白名单字体条目（template_gen 编译沙箱可用字体）"""

    name: str  # LaTeX（fontspec/xeCJK）中使用的字体名
    aliases: list[str] = Field(default_factory=list)  # 可替代的常见用户叫法
    note: str = ""  # 用途备注（如 "Latin serif" / "CJK 宋体"）


# template_gen 编译沙箱（docker/tex 镜像）内置字体白名单默认值，
# 须与镜像实际安装的字体保持一致；私有化部署可按环境在 config 覆盖。
_DEFAULT_FONT_WHITELIST: tuple[dict[str, Any], ...] = (
    {
        "name": "Latin Modern",
        "note": "default Latin text/math fonts, no \\setmainfont needed",
    },
    {
        "name": "TeX Gyre Termes",
        "aliases": ["Times New Roman"],
        "note": "Latin serif, Times-compatible",
    },
    {
        "name": "TeX Gyre Heros",
        "aliases": ["Helvetica", "Arial"],
        "note": "Latin sans-serif",
    },
    {"name": "TeX Gyre Pagella", "aliases": ["Palatino"], "note": "Latin serif"},
    {
        "name": "TeX Gyre Schola",
        "aliases": ["Century Schoolbook"],
        "note": "Latin serif",
    },
    {
        "name": "TeX Gyre Adventor",
        "aliases": ["Avant Garde"],
        "note": "Latin sans-serif",
    },
    {"name": "TeX Gyre Bonum", "aliases": ["Bookman"], "note": "Latin serif"},
    {
        "name": "FandolSong",
        "aliases": ["宋体", "SimSun"],
        "note": "CJK serif, ctex default fontset",
    },
    {"name": "FandolHei", "aliases": ["黑体", "SimHei"], "note": "CJK sans-serif"},
    {"name": "FandolFang", "aliases": ["仿宋", "FangSong"], "note": "CJK"},
    {"name": "FandolKai", "aliases": ["楷体", "KaiTi"], "note": "CJK"},
    {"name": "Noto Sans CJK SC", "aliases": ["思源黑体"], "note": "CJK sans-serif"},
    {
        "name": "Noto Serif CJK SC",
        "aliases": ["思源宋体", "Source Han Serif"],
        "note": "CJK serif",
    },
)


def default_font_whitelist() -> list[FontWhitelistEntry]:
    """返回字体白名单默认值（每次构造新对象，避免共享可变默认）。"""
    return [
        FontWhitelistEntry.model_validate(entry) for entry in _DEFAULT_FONT_WHITELIST
    ]


class TemplateGenConfig(BaseModel):
    """模板生成模块配置"""

    enabled: bool = True  # 是否启用
    output_dir: str = "data/templates/"  # 生成模板的输出目录
    # ── 自主生成 Agent 配置 ──
    agent_max_rounds: int = Field(50, ge=1, le=70)  # Agent 最大工具调用轮数（防发散）
    agent_max_files: int = Field(30, ge=1, le=200)  # 单次生成的最大文件数
    agent_max_file_size_kb: int = Field(512, ge=1)  # 单个文件大小上限（KB）
    agent_compile_enabled: bool = True  # Agent 是否调用 latexmk 做编译验证
    agent_compile_timeout: int = Field(120, ge=1)  # Agent 编译超时（秒）
    # 字体白名单：注入 template_gen 的规划提示词，Agent 只允许使用名单内字体；
    # 置空列表时回退到内置默认值（需与 docker/tex 镜像内置字体一致）
    font_whitelist: list[FontWhitelistEntry] = Field(
        default_factory=default_font_whitelist
    )
    # ── 沙箱自动清理配置 ──
    cleanup_enabled: bool = True  # 是否启用过期沙箱自动清理
    cleanup_interval_seconds: int = Field(
        3600, ge=60
    )  # 清理检查间隔（秒），默认 1 小时
    cleanup_retention_days: int = Field(7, ge=1)  # 沙箱保留天数，超过则删除


class TemplateLibraryConfig(BaseModel):
    """模板库模块配置（内置模板 + 用户私有模板库）"""

    enabled: bool = True  # 是否启用（false 时 /api/templates 端点整体关闭）
    user_library_dir: str = (
        "data/template_library/"  # 单用户模式模板库目录（多用户按用户空间隔离）
    )
    max_upload_mb: int = Field(20, ge=1)  # 上传模板 zip 大小上限（MB）
    # 上传模板包内允许保存的文件扩展名白名单（LaTeX 相关收窄，含图形/字体素材）；
    # 无扩展名文件（.gitignore/Makefile 等）豁免校验，上传解压后统一清理不入库
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [
            ".tex",
            ".sty",
            ".cls",
            ".bib",
            ".bst",
            ".bbx",
            ".cbx",
            ".clo",
            ".def",
            ".cfg",
            ".fd",
            ".dtx",
            ".ins",
            ".ltx",
            ".tikz",
            ".md",
            ".txt",
            ".json",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".eps",
            ".svg",
            ".pdf",
            ".ttf",
            ".otf",
        ]
    )


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


class GitManageConfig(BaseModel):
    """Git 版本管理模块配置"""

    enabled: bool = True  # 是否启用
    git_dir_max_mb: int = Field(
        200, ge=1
    )  # 单项目 .git 目录大小上限（MB），超限拒绝提交/上传


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
    sessions_dir: str = "data/style_modify_sessions/"  # 会话缓存目录
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
    """写作助手模块配置"""

    enabled: bool = True  # 是否启用
    # 触发上下文压缩的累计 token 阈值；0 表示基于模型上下文窗口动态推导
    # （window - max_tokens - headroom - keep_recent - summary_budget）
    compression_threshold_tokens: int = 0
    # 动态推导触发值时保留的窗口余量（token）
    compression_headroom_tokens: int = 30000
    # 压缩时原样保留的最近消息 token 预算
    compression_keep_recent_tokens: int = 20000
    # 压缩时原样保留的最近轮数（与 token 预算取先满足者）
    compression_keep_recent_turns: int = 5
    sessions_dir: str = "data/writing_chat_sessions/"  # 会话持久化目录
    custom_prompts_dir: str = "data/prompts/writing_chat/"  # 助手提示词存放目录
    summary_temperature: float = 0.2  # 生成压缩摘要时的温度
    # 压缩摘要最大生成 token 数（新字段；旧配置读取 summary_max_tokens 迁移）
    summary_token_budget: int = 6000
    # 已废弃，仅兼容旧配置；存在且 summary_token_budget 未显式给出时迁移为预算值
    summary_max_tokens: int = 50000
    # 可选的独立摘要模型；为空时跟随主模型
    summary_model: str = ""
    # 是否在聊天中启用联网搜索（受全局 web_search.enabled 约束）
    web_search_enabled: bool = True
    # 是否将结构化网页证据接入工具上下文、会话持久化和引用校验
    evidence_bundle_enabled: bool = True
    # 单轮持久化并注入 LLM 的网页证据字符预算
    evidence_max_chars: int = Field(24000, ge=2000, le=100000)
    # 单轮持久化的证据片段数量上限
    evidence_max_items: int = Field(12, ge=1, le=50)

    @model_validator(mode="before")
    @classmethod
    def _migrate_summary_max_tokens(cls, data):
        """旧配置只含 summary_max_tokens 时迁移到 summary_token_budget 并告警。"""
        if isinstance(data, dict) and "summary_token_budget" not in data:
            if "summary_max_tokens" in data:
                from backend.core.logger import get_logger

                get_logger("config").warning(
                    "writing_chat.summary_max_tokens is deprecated; "
                    "using it as summary_token_budget"
                )
                data = dict(data)
                data["summary_token_budget"] = data["summary_max_tokens"]
        return data


class DuckDuckGoConfig(BaseModel):
    """DuckDuckGo 搜索引擎配置"""

    enabled: bool = True


class SearXNGConfig(BaseModel):
    """SearXNG 搜索引擎配置"""

    enabled: bool = True
    base_url: str = ""  # SearXNG 实例地址（如 http://localhost:8080）


class TavilyConfig(BaseModel):
    """Tavily 搜索引擎配置"""

    enabled: bool = True
    # Tavily API Key（https://app.tavily.com 获取）；也可经环境变量
    # LATEXCELLENCE_TAVILY_API_KEY 注入（优先于配置文件且永不写盘，
    # 与 LX_LLM_KEY 同一安全策略）
    api_key: str = ""


class WebSearchEnginesConfig(BaseModel):
    """联网搜索引擎集合 — 每个引擎独立开关和参数"""

    duckduckgo: DuckDuckGoConfig = DuckDuckGoConfig()
    searxng: SearXNGConfig = SearXNGConfig()
    tavily: TavilyConfig = TavilyConfig()


class QueryRewritingConfig(BaseModel):
    """查询重写模块配置"""

    enabled: bool = True  # 是否启用 LLM 查询重写


class WebSearchRankingConfig(BaseModel):
    """搜索聚合的确定性排序配置。"""

    mode: Literal["rrf", "legacy"] = "rrf"
    source_type_boosts: dict[str, float] = Field(
        default_factory=lambda: {
            "government": 0.015,
            "official_docs": 0.01,
            "academic": 0.01,
        }
    )


class WebBrowserConfig(BaseModel):
    """可选浏览器回退的资源边界。"""

    enabled: bool = False
    pool_size: int = Field(1, ge=1, le=4)
    page_timeout_seconds: int = Field(15, ge=3, le=60)
    max_pages_per_request: int = Field(1, ge=1, le=3)
    stable_window_ms: int = Field(750, ge=200, le=5000)


class WebPageReadingConfig(BaseModel):
    """网页结构化抓取、抽取和缓存配置。"""

    strategy: Literal["legacy", "structured"] = "legacy"
    cache_enabled: bool = True
    cache_dir: str = "data/web_cache/"
    cache_ttl_seconds: int = Field(3600, ge=60, le=604800)
    max_cache_entry_mb: int = Field(5, ge=1, le=50)
    max_response_mb: int = Field(10, ge=1, le=50)
    extractor_version: str = "html-v1"
    quality_threshold: float = Field(0.35, ge=0, le=1)
    max_redirects: int = Field(5, ge=0, le=10)
    max_retries: int = Field(2, ge=0, le=5)
    backoff_base_seconds: float = Field(0.25, ge=0, le=5)
    max_retry_after_seconds: float = Field(10, ge=0, le=60)
    per_domain_concurrency: int = Field(2, ge=1, le=8)
    min_domain_interval_ms: int = Field(100, ge=0, le=10000)
    circuit_failure_threshold: int = Field(3, ge=1, le=20)
    circuit_cooldown_seconds: float = Field(30, ge=1, le=600)
    browser: WebBrowserConfig = WebBrowserConfig()


class WebSearchConfig(BaseModel):
    """联网搜索模块配置"""

    enabled: bool = False  # 是否启用（默认关闭，用户手动开启）
    primary_engine: str = "tavily"  # 主搜索引擎标识（tavily / searxng / duckduckgo）
    strategy: Literal["legacy", "aggregate"] = "legacy"
    max_results: int = Field(10, ge=1, le=20)  # 默认返回结果数上限
    timeout_seconds: int = Field(10, ge=1)  # 单次搜索超时（秒）
    overall_timeout_seconds: float = Field(12.0, ge=1, le=120)
    per_engine_timeout_seconds: float = Field(8.0, ge=0.5, le=60)
    max_parallel_queries: int = Field(4, ge=1, le=8)
    domain_result_cap: int = Field(3, ge=1, le=20)
    engines: WebSearchEnginesConfig = WebSearchEnginesConfig()
    deep_read_pages: int = Field(
        0, ge=0, le=5
    )  # 自动 Deep Read 前 N 条（0=禁用，由 open_page 按需读取）
    deep_read_max_chars: int = Field(10000, ge=500, le=20000)  # 每页最大抓取字符数
    query_rewriting: QueryRewritingConfig = QueryRewritingConfig()
    ranking: WebSearchRankingConfig = WebSearchRankingConfig()
    page_reading: WebPageReadingConfig = WebPageReadingConfig()


class ScholarlyResearchProviderConfig(BaseModel):
    """单个学术数据源配置。"""

    enabled: bool = True
    # 数据源 API Key（仅部分 Provider 需要）。使用 Key 的 Provider 均可经
    # 环境变量 LATEXCELLENCE_<PROVIDER 大写>_API_KEY 注入（如
    # LATEXCELLENCE_OPENALEX_API_KEY），优先于配置文件且永不写盘，
    # 与 LX_LLM_KEY 同一安全策略（完整清单见 SECRET_ENV_PATHS）
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
    ranking_mode: Literal["legacy", "rrf"] = "legacy"
    full_text_enabled: bool = False
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
    format_configs_dir: str = "data/formats/"  # 保存的格式配置文件目录
    providers: BibProvidersConfig = BibProvidersConfig()  # DOI 数据源配置


class PlotGenConfig(BaseModel):
    """科研绘图配置"""

    enabled: bool = True
    output_dir: str = "data/plots/"
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


class EditorConfig(BaseModel):
    """网页编辑器模块配置（editor-plan §5.3）"""

    enabled: bool = True  # 是否启用编辑器（工作台入口与 /editor 路由）
    # 编辑器侧 AI 助手独立子开关：管理员可在保留编辑器的同时单独关闭
    # AI 面板以控制 LLM 开销（决策 Q26）；关闭时服务端拒绝助手端点
    assistant_enabled: bool = True


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
    template_library: TemplateLibraryConfig = TemplateLibraryConfig()
    code_review: CodeReviewConfig = CodeReviewConfig()
    compile: CompileConfig = CompileConfig()
    git_diff: GitDiffConfig = GitDiffConfig()
    git_manage: GitManageConfig = GitManageConfig()
    format_converter: FormatConverterConfig = FormatConverterConfig()
    style_modify: StyleModifyConfig = StyleModifyConfig()
    terminology_check: TerminologyCheckConfig = TerminologyCheckConfig()
    paragraph_translate: ParagraphTranslateConfig = ParagraphTranslateConfig()
    writing_chat: WritingChatConfig = WritingChatConfig()
    bib_manager: BibManagerConfig = BibManagerConfig()
    plot_gen: PlotGenConfig = PlotGenConfig()
    editor: EditorConfig = EditorConfig()
    function_calling: FunctionCallingConfig = FunctionCallingConfig()


class MultiUserConfig(BaseModel):
    """多用户模式配置（默认关闭；开启后强制登录、用户空间隔离）

    安全相关字段（路径、配额、限流）实行严格校验：未知键报错（extra=forbid），
    防止部署时因配置项拼写错误而静默使用默认值导致安全预期失效。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False  # 是否启用多用户模式
    db_path: str = "data/latexcellence.db"  # SQLite 数据库路径
    users_root: str = "data/users/"  # 用户空间根目录（<users_root>/<user_id>/）
    staging_root: str = "data/staging/"  # 上传解压暂存区
    default_project_quota_mb: int = Field(500, ge=1)  # 默认项目空间配额（MB）
    default_derived_quota_mb: int = Field(100, ge=1)  # 默认衍生数据配额（MB）
    max_upload_archive_mb: int = Field(500, ge=1)  # 压缩包大小上限（MB）
    max_upload_files: int = Field(20000, ge=1)  # 解压文件数上限
    max_upload_file_size_mb: int = Field(100, ge=1)  # 单文件大小上限（MB）
    max_compression_ratio: int = Field(1000, ge=1)  # 压缩比上限（防压缩炸弹）
    recycle_bin_days: int = Field(7, ge=1)  # 回收站保留天数
    session_ttl_days: int = Field(7, ge=1)  # 会话默认有效期（天）
    remember_ttl_days: int = Field(30, ge=1)  # “记住我”会话有效期（天）
    llm_rate_limit_per_minute: int = Field(20, ge=1)  # 每用户 LLM 请求限流（次/分钟）
    llm_max_concurrency_per_user: int = Field(2, ge=1)  # 每用户最大并发 LLM 任务数
    login_rate_limit_per_minute: int = Field(
        5, ge=1
    )  # 登录限流（次/分钟，按用户名+IP）
    registration_enabled: bool = True  # 是否开放注册（需管理员审批）
    # ── 注册自动审批（IP 策略，详见 multiuser/registration_policy.py）──
    # 开关打开后按注册 IP 判定「无恶意」即自动通过审批（status=active），
    # 否则回落人工待审批——任何检查不通过都只降级为人工，绝不直接拒绝。
    auto_approve_enabled: bool = False  # 自动审批总开关（false 时全部转人工）
    # 同一注册 IP 允许的账号数上限：历史所有未删除账号均计入
    # （含 pending/disabled）；共享出口 IP 的机构部署请调大或改用白名单
    auto_approve_max_accounts_per_ip: int = Field(1, ge=1)
    auto_approve_allowed_cidrs: list[str] = []  # IP/CIDR 可信白名单（如校园网出口网段）
    auto_approve_geo_db_path: str = (
        ""  # GeoLite2 .mmdb 离线库路径（空 = 不启用地理规则）
    )
    auto_approve_ip2region_db_path: str = (
        ""  # ip2region .xdb 离线库路径（通常为 ip2region_v4.xdb；地址族由
        # 文件头自动识别，中国 IP 归属直接采用其结果——国内城市精度优于
        # GeoLite2，非/未知中国回落）
    )
    auto_approve_ip2region_db_path_v6: str = (
        ""  # 另一地址族的 ip2region .xdb 补充库（如 ip2region_v6.xdb；
        # 查询按注册 IP 的地址族路由到对应库，该族未配置时回落 GeoLite2）
    )
    auto_approve_allowed_countries: list[str] = (
        []
    )  # 国家码白名单（ISO 3166，如 ["CN"]）
    auto_approve_allowed_cities: list[str] = (
        []
    )  # 城市名白名单（匹配库内 en/zh-CN 名称）
    # 真实客户端 IP 解析：反代部署时配置代理的 IP/CIDR，命中才信任
    # X-Forwarded-For；直连部署保持为空（恒用 TCP 对端地址，防伪造头绕过策略）
    trusted_proxies: list[str] = []
    register_rate_limit_per_hour: int = Field(20, ge=1)  # 每 IP 注册请求限流（次/小时）

    @field_validator("auto_approve_allowed_cidrs", "trusted_proxies")
    @classmethod
    def _validate_cidr_entries(cls, v: list[str]) -> list[str]:
        """IP/CIDR 条目在加载期强校验：拼错的网段若静默失效会架空整个审批防线。"""
        cleaned: list[str] = []
        for entry in v:
            text = (entry or "").strip()
            if not text:
                continue
            try:
                ipaddress.ip_network(text, strict=False)
            except ValueError as e:
                raise ValueError(f"Invalid IP/CIDR entry: {entry!r}") from e
            cleaned.append(text)
        return cleaned

    @field_validator("auto_approve_allowed_countries", "auto_approve_allowed_cities")
    @classmethod
    def _clean_name_list(cls, v: list[str]) -> list[str]:
        return [entry.strip() for entry in v if (entry or "").strip()]

    admin_username_env: str = (
        "LATEXCELLENCE_ADMIN_USERNAME"  # 首个管理员用户名环境变量名
    )
    admin_password_env: str = "LATEXCELLENCE_ADMIN_PASSWORD"  # 首个管理员密码环境变量名
    # 反向补丁编辑日志（v2 P3）：快照存 <users_root>/<owner_id>/.edit_logs/，
    # 不进项目目录、不进下载包、不占项目/衍生数据配额
    edit_log_max_ops: int = Field(
        50, ge=1
    )  # 每 (project, actor) 撤销栈保留的最大操作条数（按组整体淘汰）
    edit_log_max_op_mb: int = Field(
        8, ge=1
    )  # 单 op 快照总量上限（MB），超出只记索引并标记过大不可撤
    edit_log_ttl_hours: int = Field(
        24, ge=1
    )  # 编辑日志栈无活动保留时长（小时），超时由维护循环整栈清理
    # 写作会话保留期（v2 P4）：0 = 不自动删，仅统计用量；>0 时由维护循环
    # 删除超过该天数未活动的 writing_chat 会话文件
    writing_chat_retention_days: int = Field(0, ge=0)
    # 分片上传会话（v2 P5）：会话从最后活动算起的保留时长（小时），
    # 超时由维护循环清理会话行与 staging 分片目录
    upload_session_ttl_hours: int = Field(24, ge=1)
    # 单个分片大小上限（MB）；前端切片大小与后端 PUT 校验均以此对齐
    chunk_part_max_mb: int = Field(8, ge=1)
    # 配额预警阈值（v2 P6）：用量百分比达到 warn/critical 时经 SMTP 通知，
    # 迟滞回差——低于 reset 阈值后清除预警标记，再次越线可重新发送
    quota_warn_percent: int = Field(80, ge=1, le=100)
    quota_critical_percent: int = Field(95, ge=1, le=100)
    quota_alert_reset_percent: int = Field(75, ge=1, le=100)


class SmtpConfig(BaseModel):
    """SMTP 邮件通知配置（多用户 v2 P6）

    enabled=false（或 host 为空）时所有通知点静默跳过（优雅降级，
    不阻塞注册/审批/找回以外的主流程），自助找回入口在前端隐藏。

    password 为敏感项：设置页以掩码返回，保存时 `***` 前缀表示保留原值
    （与 llm.api_key / security.access_token 同一掩码协议）。
    也可经环境变量 LATEXCELLENCE_SMTP_PASSWORD 注入（优先于配置文件，
    且永不写盘，与 LX_LLM_KEY 同一安全策略）。
    安全相关字段实行严格校验（extra=forbid）。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False  # 是否启用 SMTP 邮件通知
    host: str = ""  # SMTP 服务器地址（空 = 未配置）
    port: int = Field(465, ge=1, le=65535)  # SMTP 端口
    encryption: Literal["ssl", "starttls", "none"] = (
        "ssl"  # 加密方式：ssl(465 隐式 TLS) / starttls / none
    )
    username: str = ""  # 认证用户名（留空 = 匿名）
    password: str = ""  # 认证密码（设置页掩码展示）
    from_address: str = ""  # 发件人地址（空时回退 username）
    from_name: str = "LaTeXcellence"  # 发件人显示名
    site_base_url: str = ""  # 站点对外地址（找回链接前缀；空 = 相对路径提示）
    max_send_per_minute: int = Field(10, ge=1)  # 全局发送限速（封/分钟）
    retry_attempts: int = Field(3, ge=1)  # 投递失败重试次数（指数退避）
    # 邮件模板语言（站点默认语言）：zh / en 两套模板存于 mail 模块集中管理
    template_language: Literal["zh", "en"] = "zh"


class SandboxConfig(BaseModel):
    """Docker 沙箱配置（多用户 v2 P1a）

    外部命令执行（latexmk/texfot、git/latexdiff、pandoc、Python 绘图、
    latexindent）统一经 SandboxRunner 执行：

    - mode=auto：探测到 Docker daemon 即用容器执行，不可达时回落本地进程；
    - mode=docker：daemon 不可达直接报错（503 sandbox_unavailable），不静默回落，
      保证生产环境隔离承诺明确；
    - mode=local：保持既有 subprocess 行为（开发机无 Docker 的默认路径）。

    安全相关字段实行严格校验（extra=forbid），防止拼写错误静默使用默认值。
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "docker", "local"] = "auto"  # 沙箱运行模式
    image_tex: str = (
        "latexcellence-tex:latest"  # TeX 编译/git/latexdiff/latexindent 镜像
    )
    image_convert: str = "latexcellence-convert:latest"  # pandoc 转换镜像
    image_plot: str = "latexcellence-plot:latest"  # Python 绘图镜像
    cpus: float = Field(1.0, gt=0)  # 单容器 CPU 上限（核数）
    memory_mb: int = Field(1024, ge=64)  # 单容器内存上限（MB）
    pids_limit: int = Field(256, ge=16)  # 单容器进程数上限
    tmpfs_mb: int = Field(512, ge=32)  # 容器内 /tmp tmpfs 容量（MB）
    max_concurrent_global: int = Field(4, ge=1)  # 全局并发沙箱上限
    max_concurrent_per_user: int = Field(1, ge=1)  # 每用户并发沙箱上限（多用户模式）
    startup_grace_seconds: int = Field(10, ge=1)  # 容器启动宽限期（秒）


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
    security: SecurityConfig = SecurityConfig()
    llm: LLMConfig = LLMConfig()
    http: HttpConfig = HttpConfig()
    features: FeaturesConfig = FeaturesConfig()
    logging: LoggingConfig = LoggingConfig()
    multi_user: MultiUserConfig = MultiUserConfig()
    sandbox: SandboxConfig = SandboxConfig()
    smtp: SmtpConfig = SmtpConfig()


# ── 敏感项环境变量注入（安全策略）────────────────────────────────────
# 与 LX_LLM_KEY / LATEXCELLENCE_SMTP_PASSWORD 同一安全策略：环境变量
# 优先于配置文件，注入值仅在内存生效、save_config() 落盘前自动清空对应
# 字段（永不写盘），供 Compose/systemd 等部署形态免改配置注入凭证。

# SMTP 授权密码注入的环境变量名
SMTP_PASSWORD_ENV = "LATEXCELLENCE_SMTP_PASSWORD"
# Tavily 联网搜索 API Key 注入的环境变量名
TAVILY_API_KEY_ENV = "LATEXCELLENCE_TAVILY_API_KEY"


def _scholarly_provider_path(provider: str) -> str:
    """学术数据源 api_key 在配置中的点分路径。"""
    return (
        "features.function_calling.scholarly_research.providers." f"{provider}.api_key"
    )


# 敏感项环境变量注册表：环境变量名 → 配置点分路径。
# 覆盖 LLM Key、SMTP 密码、Tavily 搜索 Key 与学术数据源 Key——仅收录实际
# 消费 API Key 的数据源（crossref / arxiv / datacite 无鉴权字段消费方）。
SECRET_ENV_PATHS: dict[str, str] = {
    "LX_LLM_KEY": "llm.api_key",  # 历史命名，保持兼容
    SMTP_PASSWORD_ENV: "smtp.password",
    TAVILY_API_KEY_ENV: ("features.function_calling.web_search.engines.tavily.api_key"),
    "LATEXCELLENCE_OPENALEX_API_KEY": _scholarly_provider_path("openalex"),
    "LATEXCELLENCE_SEMANTIC_SCHOLAR_API_KEY": _scholarly_provider_path(
        "semantic_scholar"
    ),
    "LATEXCELLENCE_CORE_API_KEY": _scholarly_provider_path("core"),
    "LATEXCELLENCE_UNPAYWALL_API_KEY": _scholarly_provider_path("unpaywall"),
    "LATEXCELLENCE_PUBMED_API_KEY": _scholarly_provider_path("pubmed"),
}

# 更新载荷为空字符串时视为"保留原值"的敏感项。
# llm.api_key 沿用历史语义：前端意外清空输入框不应抹掉已配置的 Key；
# 其余敏感项空值按用户输入处理（可清空）。
_KEEP_ON_EMPTY_SECRET_PATHS: frozenset[str] = frozenset({"llm.api_key"})


def _get_nested(data: dict, path: str) -> Any:
    """按点分路径读取嵌套字典值；任一层缺失返回 None。"""
    node: Any = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _set_nested(data: dict, path: str, value: Any) -> None:
    """按点分路径写入嵌套字典值（中间层缺失时创建空字典）。"""
    keys = path.split(".")
    node = data
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


# 模块级全局变量：单例模式存储配置对象和路径
_config: AppConfig | None = None
_config_path: Path | None = None
# 当前由环境变量注入、落盘前必须清空的敏感字段集合（SECRET_ENV_PATHS 的值域）
_secrets_from_env: set[str] = set()
# 热重载探测（v2 P7 多实例）：记录上次加载时 config.json 的 (mtime_ns, size)
# 指纹，get_config() 命中变化即重载——多副本部署下管理员改配置后无需逐个
# 重启（save_config 写盘后同步刷新指纹，避免自身触发无意义重载）。
_config_fingerprint: tuple[int, int] | None = None
# multi_user.enabled 的启动锚定值：该开关决定认证中间件 / 数据库初始化 /
# 用户空间布局等启动期形态，热切换会造成半初始化状态，**必须重启生效**。
# 热重载时检测到磁盘值与锚定值不同则保持锚定值并告警（文档维持此约束）。
_boot_multi_user_enabled: bool | None = None
_reload_lock = threading.Lock()


def _record_config_fingerprint() -> None:
    """记录当前 config_path 的 (mtime_ns, size) 指纹（文件缺失时置 None）。"""
    global _config_fingerprint
    if _config_path is None:
        _config_fingerprint = None
        return
    try:
        st = _config_path.stat()
        _config_fingerprint = (st.st_mtime_ns, st.st_size)
    except OSError:
        _config_fingerprint = None


def _fingerprint_changed() -> bool:
    """config.json 自上次加载后是否发生变化（无法 stat 视为未变化）。"""
    if _config_path is None or _config_fingerprint is None:
        return False
    try:
        st = _config_path.stat()
    except OSError:
        return False
    return (st.st_mtime_ns, st.st_size) != _config_fingerprint


def load_config(config_path: str = "config.json") -> AppConfig:
    """
    加载并校验配置文件。

    流程：
        1. 读取 JSON 文件
        2. 应用 SECRET_ENV_PATHS 注册表中的敏感项环境变量覆盖
           （LX_LLM_KEY / LATEXCELLENCE_SMTP_PASSWORD / Tavily 与学术
           数据源 Key 等），并记录注入来源
        3. 通过 Pydantic 进行类型校验和默认值填充
        4. 缓存到全局变量供后续调用

    参数：
        config_path: 配置文件路径，默认为 "config.json"

    返回：
        校验后的 AppConfig 对象
    """
    global _config, _config_path, _secrets_from_env
    global _boot_multi_user_enabled
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cfg, secrets_from_env = _parse_config_file(path)
    _secrets_from_env = secrets_from_env
    _config = cfg
    _config_path = path.resolve()
    _boot_multi_user_enabled = cfg.multi_user.enabled
    _record_config_fingerprint()
    return cfg


def _parse_config_file(path: Path) -> tuple[AppConfig, set[str]]:
    """读取并校验配置文件（不触碰模块级状态）。

    返回 (AppConfig, 由环境变量注入的敏感项路径集合)。
    热重载路径复用本函数，保证在发布新配置对象**之前**完成
    multi_user.enabled 锚定，避免并发 get_config() 短暂观察到未锚定的
    运行模式值。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 安全策略：敏感项环境变量优先级高于配置文件，并记录来源防止落盘
    secrets_from_env: set[str] = set()
    for env_name, secret_path in SECRET_ENV_PATHS.items():
        value = os.environ.get(env_name, "")
        if not value:
            continue
        _set_nested(data, secret_path, value)
        secrets_from_env.add(secret_path)

    # Pydantic 自动校验类型+填充默认值
    return AppConfig(**data), secrets_from_env


def get_config() -> AppConfig:
    """
    获取当前缓存的配置对象。
    必须在调用 load_config() 之后才能调用。

    v2 P7 热重载：探测 config.json 的 mtime+size 指纹，变化即从磁盘重载
    （校验失败保持旧配置并告警一次）。``multi_user.enabled`` 除外——其
    启动锚定值不随热重载漂移，变更仍需重启（见模块级常量注释）。
    """
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    if _fingerprint_changed():
        _reload_from_disk()
    return _config


def _reload_from_disk() -> None:
    """按磁盘内容重载配置（get_config 检测到指纹变化时调用）。

    进程内串行化；加载失败（JSON 损坏 / 校验不过）时记录告警并刷新指纹，
    保持旧配置继续服务，待下次修改后自动重试。解析经 _parse_config_file
    纯函数完成：先锚定 multi_user.enabled 再一次性发布新配置对象，
    不经过 load_config 的全局赋值，避免未锚定中间态外泄给并发读侧。
    """
    global _config, _secrets_from_env
    with _reload_lock:
        if not _fingerprint_changed():
            return  # 双检：另一线程可能已完成重载
        assert _config_path is not None
        pinned_enabled = _boot_multi_user_enabled
        from backend.core.logger import get_logger

        log = get_logger("config")
        log.info("Config file changed on disk (%s), reloading", _config_path.name)
        try:
            cfg, secrets_from_env = _parse_config_file(_config_path)
        except Exception as e:  # noqa: BLE001 - 坏配置不阻断服务
            log.error("Config reload failed (%s); keeping previous configuration", e)
            _record_config_fingerprint()
            return
        # multi_user.enabled 锚定：热重载不改变运行模式（重启生效），
        # 启动锚定值本身保持不变
        if pinned_enabled is not None and cfg.multi_user.enabled != pinned_enabled:
            cfg.multi_user.enabled = pinned_enabled
            log.warning(
                "multi_user.enabled changed on disk but requires a restart; "
                "keeping boot-time value (%s)",
                pinned_enabled,
            )
        _secrets_from_env = secrets_from_env
        _config = cfg
        _record_config_fingerprint()


def save_config() -> None:
    """
    将当前缓存的配置对象持久化写回 config.json。

    安全策略：
        - 凡由环境变量注入的敏感项（_secrets_from_env 集合：llm.api_key /
          smtp.password / tavily 与学术数据源 api_key），落盘前自动清空，
          防止泄漏到磁盘。
        - 若敏感项由用户通过 UI 手动设置，则正常持久化。
    """
    global _config, _config_path
    if _config is None or _config_path is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    data = _config.model_dump()
    # 环境变量来源的敏感项不得写入磁盘（model_dump 必然产出全部字段，
    # 直接按注册表路径置空即可）
    for secret_path in _secrets_from_env:
        _set_nested(data, secret_path, "")
    atomic_write(_config_path, json.dumps(data, indent=2, ensure_ascii=False))
    # 刷新指纹：自身写盘不构成"外部变更"，避免下一次 get_config() 触发
    # 无意义重载（v2 P7 热重载探测）
    _record_config_fingerprint()


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
    敏感项（llm.api_key / smtp.password / tavily 与学术数据源 api_key）
    统一掩码协议：以 "***" 开头（前端掩码）表示保留原值不变；
    传入完整的真实值则视为用户手动设置，解除环境变量标记并允许持久化。
    来自环境变量的敏感值不会因本次操作而写回磁盘（由 save_config() 保障）。

    参数：
        updates: 包含要更新字段的字典，支持嵌套结构

    返回：
        更新后的 AppConfig 对象
    """
    global _config, _config_path, _secrets_from_env
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")

    current = _config.model_dump()

    # 访问令牌：掩码（*** 开头）→ 保留原值；空字符串 → 清除令牌（禁用校验）；
    # 真实 Token → 更新
    if "security" in updates and isinstance(updates["security"], dict):
        security_updates = updates["security"]
        if "access_token" in security_updates:
            token = security_updates["access_token"]
            if token.startswith("***"):
                security_updates["access_token"] = current["security"]["access_token"]

    # 敏感项统一处理（SECRET_ENV_PATHS 注册表）：
    #   - *** 前缀（前端掩码）→ 保留原值；
    #   - 非空真实值 → 视为手动设置，解除环境变量标记允许落盘；
    #   - 空值 → llm.api_key 沿用历史语义保留原值（防前端误清空），
    #     其余按用户输入透传（可清空）。
    for secret_path in SECRET_ENV_PATHS.values():
        value = _get_nested(updates, secret_path)
        if not isinstance(value, str):
            continue  # 部分更新未携带该字段
        if value.startswith("***") or (
            value == "" and secret_path in _KEEP_ON_EMPTY_SECRET_PATHS
        ):
            _set_nested(updates, secret_path, _get_nested(current, secret_path))
        elif value:
            _secrets_from_env.discard(secret_path)

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

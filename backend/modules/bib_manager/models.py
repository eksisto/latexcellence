"""
参考文献管理 Pydantic 模型。

定义 API 请求体和 LLM 响应 schema。
"""

from pydantic import BaseModel


class FormatAnalysisRequest(BaseModel):
    """参考文献格式分析请求"""

    example: str


class ReferenceFormatResult(BaseModel):
    """LLM 格式分析输出"""

    format_name: str
    confidence: str = "high"
    entry_type: str
    document_type_marker: str | None = None
    required_fields: list[str] = []
    optional_fields: list[str] = []
    field_descriptions: dict[str, str] = {}
    format_description: str = ""
    raw_observed_fields: list[str] = []
    # LaTeX 样式字段（由 LLM 推荐 + style_registry 补全）
    latex_backend: str | None = None  # "bibtex" 或 "biblatex"
    latex_package: str | None = None  # 需要的 LaTeX 宏包
    latex_bibliographystyle: str | None = None  # .bst 样式名
    latex_preamble: str | None = None  # 完整 preamble 代码片段


class ExtractRequest(BaseModel):
    """元数据提取请求"""

    text: str | None = None
    image_base64: str | None = None
    format_info: dict | None = None


class ExtractedMetadata(BaseModel):
    """文献元数据（动态字段：key 由格式的 required_fields/optional_fields 决定）"""

    fields: dict[str, str] = {}


class GenerateBibtexRequest(BaseModel):
    """BibTeX 生成请求"""

    metadata: dict = {}
    format_info: dict | None = None
    existing_keys: list[str] = []
    doi: str | None = None  # 用于 DOI 查询生成 BibTeX


class BibEntry(BaseModel):
    """BibTeX 条目"""

    cite_key: str
    entry_type: str
    fields: dict[str, str] = {}
    raw_bibtex: str = ""


class SaveBibRequest(BaseModel):
    """.bib 文件写入请求"""

    file_path: str
    bibtex_code: str
    cite_key: str


class ReadBibRequest(BaseModel):
    """.bib 文件读取请求"""

    file_path: str


class GenerateLatexRequest(BaseModel):
    """生成 LaTeX 参考文献代码请求"""

    metadata: dict
    format_info: dict | None = None
    bib_file_path: str | None = None


class LatexCodeResult(BaseModel):
    """LaTeX 参考文献代码输出"""

    preamble: str = ""
    bibliographystyle: str = ""
    bibliography: str = ""
    cite_command: str = ""
    full_code: str = ""


class DoiLookupRequest(BaseModel):
    """DOI 查询请求"""

    doi: str


class DoiLookupResult(BaseModel):
    """DOI 查询结果（多数据源聚合后的论文元数据）"""

    title: str = ""
    authors: list[str] = []
    year: str = ""
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    publisher: str = ""
    abstract: str = ""
    url: str = ""
    isbn: str = ""
    issn: str = ""
    keywords: list[str] = []
    data_source: str = ""  # 数据来源标识（如 crossref / arxiv / datacite）

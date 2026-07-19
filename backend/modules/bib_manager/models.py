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
    metadata: dict
    format_info: dict | None = None
    existing_keys: list[str] = []


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

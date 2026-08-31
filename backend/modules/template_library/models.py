"""模板库 Pydantic v2 模型。

TemplateManifest 与阶段①内置模板的 manifest.json 字段精确一致：
id / name / name_en? / description / category（固定枚举）/ main_file /
engine?（xelatex|pdflatex）/ author / license / source / created_at。
kind（builtin/user）由存放位置判定，不入 manifest，仅出现在 API 视图模型中。
"""

from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

# 分类固定枚举（设计决策，不得扩展）
TemplateCategory = Literal[
    "paper",
    "thesis",
    "report",
    "slides",
    "resume",
    "book",
    "poster",
    "notes",
    "other",
]

# 分类合法值集合（表单/请求校验用）
TEMPLATE_CATEGORIES = frozenset(get_args(TemplateCategory))

# 模板来源：builtin 内置 / upload 上传 zip / template_gen 生成入库 / project_save 项目另存
TemplateSource = Literal["builtin", "upload", "template_gen", "project_save"]

# kind 由存放位置判定（不写入 manifest.json）
TemplateKind = Literal["builtin", "user"]


class TemplateManifest(BaseModel):
    """manifest.json 严格校验模型（与阶段① schema 一致）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    name_en: str = ""
    description: str = ""
    category: TemplateCategory
    main_file: str = Field(min_length=1)
    engine: Literal["xelatex", "pdflatex"] | None = None
    author: str = ""
    license: str = ""
    source: TemplateSource
    created_at: str = Field(min_length=1)

    def to_manifest_dict(self) -> dict:
        """输出与磁盘 manifest.json 一致的字段顺序（供写盘使用）。"""
        data = self.model_dump()
        return {key: data[key] for key in MANIFEST_FIELD_ORDER}


# manifest.json 字段书写顺序（与阶段①内置模板保持一致，便于 diff 审阅）
MANIFEST_FIELD_ORDER = (
    "id",
    "name",
    "name_en",
    "description",
    "category",
    "main_file",
    "engine",
    "author",
    "license",
    "source",
    "created_at",
)


class TemplateFileEntry(BaseModel):
    """模板内单文件条目（仅相对路径与大小，绝不暴露服务器绝对路径）。"""

    path: str
    size: int


class TemplateSummary(BaseModel):
    """模板列表/详情视图（kind 由存放位置判定）。"""

    kind: TemplateKind
    id: str
    name: str
    name_en: str = ""
    description: str = ""
    category: TemplateCategory
    main_file: str
    engine: Literal["xelatex", "pdflatex"] | None = None
    author: str = ""
    license: str = ""
    source: TemplateSource
    created_at: str
    file_count: int = 0


class TemplateDetail(TemplateSummary):
    """模板详情：摘要 + 文件树（相对路径）。"""

    files: list[TemplateFileEntry] = Field(default_factory=list)


# ── 请求模型 ─────────────────────────────────────────────────────────
class UpdateTemplateRequest(BaseModel):
    """改名 / 改分类 / 改描述（部分更新，None 表示不修改）。"""

    name: str | None = None
    category: TemplateCategory | None = None
    description: str | None = None


class CreateProjectFromTemplateRequest(BaseModel):
    """从模板创建项目。

    多用户模式：仅需 name（项目落入当前用户空间，配额预检）；
    单用户模式：需 target_dir（创建 <target_dir>/<项目名>/，非空冲突报错）。
    """

    name: str = Field(min_length=1)
    target_dir: str | None = None


class SaveProjectAsTemplateRequest(BaseModel):
    """当前项目另存为模板（workspace_id 定位当前打开的项目）。"""

    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: TemplateCategory | None = None
    description: str = ""


class SaveTemplateGenRequest(BaseModel):
    """template_gen 会话产物保存到我的模板库。"""

    session_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: TemplateCategory | None = None
    description: str = ""


def today_iso() -> str:
    """manifest created_at 用的 ISO 日期字符串（本地日期）。"""
    return date.today().isoformat()

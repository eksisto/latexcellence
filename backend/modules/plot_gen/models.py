"""科研绘图 Agent 的数据模型。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ColumnProfile(BaseModel):
    """单列数据画像。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    inferred_type: Literal["continuous", "categorical", "datetime", "id", "unknown"]
    dtype: str
    missing: int
    missing_ratio: float
    unique: int
    sample: list[str | int | float | None] = []
    quantiles: list[float] | None = None
    top_values: list[str] | None = None


class DataProfile(BaseModel):
    """数据文件摘要，用于 LLM 上下文和前端展示。"""

    model_config = ConfigDict(extra="forbid")

    file_name: str
    rows: int
    columns: int
    column_profiles: list[ColumnProfile] = []
    preview: list[dict[str, Any]] = []
    raw_summary: str = ""


class ChartPlan(BaseModel):
    """推荐图表方案。"""

    model_config = ConfigDict(extra="forbid")

    chart_type: str
    x: str | None = None
    y: str | None = None
    color: str | None = None
    facet_row: str | None = None
    facet_col: str | None = None
    aggregation: str = "none"
    error_bar: str | None = None
    transform: str | None = None
    stat_test: str | None = None
    rationale: str = ""
    risks: list[str] = []


class GeneratedScript(BaseModel):
    """LLM 生成的脚本描述。"""

    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] = "python"
    script_name: str = "scripts/plot.py"
    code: str


class ArtifactInfo(BaseModel):
    """沙箱产物信息。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    kind: str = "file"


class ExecutionResult(BaseModel):
    """Python 脚本执行结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    stdout: str = ""
    stderr: str = ""
    traceback: str = ""
    timed_out: bool = False
    artifacts: list[ArtifactInfo] = []
    summary: str = ""


class StatsTestResult(BaseModel):
    """统计检验结果。"""

    model_config = ConfigDict(extra="forbid")

    test: str
    statistic: float | None = None
    p_value: float | None = None
    df: int | float | None = None
    effect_size: float | None = None
    assumptions: list[str] = []
    warnings: list[str] = []
    summary: str = ""


class PlotSessionMeta(BaseModel):
    """会话元数据。"""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    data_file: str
    description: str = ""
    style: str = "nature"
    language: str = "zh"
    data_profile: DataProfile | None = None
    chart_plan: ChartPlan | None = None
    messages: list[dict[str, Any]] = []
    artifacts: list[ArtifactInfo] = []
    last_execution_success: bool | None = None
    last_tikz_success: bool | None = None
    created_at: str = ""
    updated_at: str = ""


class GeneratePlotRequest(BaseModel):
    """Agent 生成请求。"""

    session_id: str
    description: str = ""
    style: str = "nature"
    language: str = "zh"


class RefinePlotRequest(BaseModel):
    """自然语言微调请求。"""

    message: str = Field(min_length=1)
    language: str = "zh"

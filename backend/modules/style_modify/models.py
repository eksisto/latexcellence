"""style_modify Agent 的数据模型。

这里只保存会话、候选修改、编译摘要和操作记录等结构化数据。LaTeX 文件内容
不通过 Pydantic JSON 字段往返给 LLM，候选文件以磁盘文件形式保存在会话
overlay 中；SSE 只发送 diff/摘要，完整源码由 session 查询接口按需读取。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompileError(BaseModel):
    """编译日志中的一条结构化错误。"""

    model_config = ConfigDict(extra="forbid")

    type: str = ""
    file: str | None = None
    line: int | None = None
    detail: str = ""
    font: str | None = None


class CompileSummary(BaseModel):
    """shadow 编译结果摘要，不携带完整原始日志。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed", "not_run"] = "not_run"
    main_file: str | None = None
    log: str = ""
    errors: list[CompileError] = Field(default_factory=list)
    timed_out: bool = False


class StagedFile(BaseModel):
    """一个候选文件在 baseline 与 overlay 之间的差异。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    baseline_md5: str
    candidate_md5: str
    baseline_size: int
    candidate_size: int
    diff: str = ""
    changed: bool = True


class Proposal(BaseModel):
    """不可变 proposal revision。"""

    model_config = ConfigDict(extra="forbid")

    revision: int
    requirement: str
    refinements: list[str] = Field(default_factory=list)
    summary: str = ""
    files: list[StagedFile] = Field(default_factory=list)
    compile: CompileSummary = Field(default_factory=CompileSummary)
    created_at: str
    applicable: bool = True
    blocked_reasons: list[str] = Field(default_factory=list)


class OperationFile(BaseModel):
    """operation 中单文件的应用前后信息。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    baseline_md5: str
    applied_md5: str
    status: Literal["applied", "undone", "failed"] = "applied"


class OperationMeta(BaseModel):
    """一次已确认应用操作的安全撤销记录。"""

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    session_id: str
    workspace_id: str
    project_root: str
    project_fingerprint: str
    revision: int
    status: Literal["applying", "applied", "undone", "failed"] = "applying"
    created_at: str
    applied_at: str | None = None
    undone_at: str | None = None
    files: list[OperationFile] = Field(default_factory=list)


class SessionMeta(BaseModel):
    """style_modify Agent 会话元数据。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    workspace_id: str
    project_root: str
    project_fingerprint: str
    status: Literal[
        "created",
        "running",
        "ready",
        "applying",
        "applied",
        "deleted",
    ] = "created"
    requirement: str = ""
    language: str = "zh"
    created_at: str
    updated_at: str
    latest_revision: int = 0
    proposal: Proposal | None = None
    operation_id: str | None = None
    applied_revision: int | None = None
    refinements: list[str] = Field(default_factory=list)
    last_verified_md5s: dict[str, str] | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)

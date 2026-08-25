"""Git 差异高亮服务数据模型。"""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict


class GitRevision(BaseModel):
    """前端可选择的 Git revision。"""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    kind: str
    short_hash: str = ""
    subject: str = ""


class GitRefsResult(BaseModel):
    """Git revision 列表。"""

    model_config = ConfigDict(extra="forbid")

    # Git 仓库根目录绝对路径；多用户模式置 None（服务器路径不向用户暴露）
    git_root: str | None = None
    current_branch: str = ""
    is_dirty: bool = False
    revisions: list[GitRevision]


class DiffHighlightResult(BaseModel):
    """差异高亮 PDF 生成结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    job_id: str = ""
    pdf_url: str = ""
    main_file: str = ""
    base_ref: str = ""
    target_ref: str = ""
    diff_engine: str = "difflib"
    added_lines: int = 0
    deleted_lines: int = 0
    included_files: list[str] = []
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
    fixes: list[str] = []


@dataclass
class ProcessResult:
    """子进程执行结果。"""

    returncode: int
    stdout: str
    stderr: str


@dataclass
class ProjectContext:
    """当前打开项目和 Git 根目录上下文。"""

    root: Path
    git_root: Path
    project_prefix: PurePosixPath


@dataclass
class FlattenContext:
    """展开 LaTeX 依赖时共享的状态。"""

    ref: str
    project: ProjectContext
    visited: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


@dataclass
class FlattenedDocument:
    """展开后的主文档。"""

    content: str
    files: list[str]


@dataclass
class DiffDocument:
    """带差异标记的 LaTeX 文档。"""

    content: str
    added_lines: int
    deleted_lines: int

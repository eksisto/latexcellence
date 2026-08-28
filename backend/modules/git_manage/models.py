"""Git 版本管理服务数据模型。"""

from pydantic import BaseModel, ConfigDict, Field


class GitChangeItem(BaseModel):
    """单条工作区变更（path 为项目内相对路径）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str  # added / modified / deleted


class GitLastCommit(BaseModel):
    """最近一次提交摘要。"""

    model_config = ConfigDict(extra="forbid")

    short_hash: str
    subject: str
    author: str
    date: str  # ISO 8601（%aI）


class GitStatusResult(BaseModel):
    """版本控制状态总览。"""

    model_config = ConfigDict(extra="forbid")

    initialized: bool
    current_branch: str = ""
    is_dirty: bool = False
    changes: list[GitChangeItem] = []
    git_dir_size_bytes: int = 0
    git_dir_max_bytes: int = 0
    last_commit: GitLastCommit | None = None


class GitCommitRequest(BaseModel):
    """提交请求（message 为唯一自由文本，服务端再做空值/长度校验）。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500)


class GitCommitResult(BaseModel):
    """提交结果。"""

    model_config = ConfigDict(extra="forbid")

    short_hash: str
    subject: str
    committed_count: int
    git_dir_size_bytes: int


class GitLogEntry(BaseModel):
    """单条提交历史。"""

    model_config = ConfigDict(extra="forbid")

    hash: str
    short_hash: str
    subject: str
    author_name: str
    date_iso: str

"""Git 版本管理服务数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 变更类型三态（porcelain XY / diff 头推导后的统一口径）
ChangeKind = Literal["added", "modified", "deleted"]


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


# ── 源码级 diff 查看与选择性恢复 ──────────────────────────────────────


class GitDiffLine(BaseModel):
    """diff 展示行（type: context / add / del；text 不含行首标记与换行）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["context", "add", "del"]
    text: str


class GitDiffHunk(BaseModel):
    """单个 hunk：解析后的展示行 + 原文切片（raw 原样回传用于恢复）。"""

    model_config = ConfigDict(extra="forbid")

    header: str
    lines: list[GitDiffLine] = []
    raw: str
    additions: int = 0
    deletions: int = 0


class GitFileDiffResult(BaseModel):
    """单文件源码级 diff（工作区 vs HEAD，或单个提交 vs 其父提交）。

    kind 为空表示目标当前无变更（页面数据过期）；is_binary / too_large
    时不生成行级 hunk，仅可整文件恢复（restorable 仅 worktree 模式且
    文件存在可回退到 HEAD 的变更时为 true，含「恢复即消失」的
    staged-added；commit 模式只读恒为 false）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["", "added", "modified", "deleted"] = ""
    is_binary: bool = False
    too_large: bool = False
    truncated: bool = False
    additions: int = 0
    deletions: int = 0
    hunks: list[GitDiffHunk] = []
    restorable: bool = False
    commit_hash: str = ""


class GitCommitFileItem(BaseModel):
    """单个提交内的一条文件变更（path 为项目内相对路径）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: ChangeKind


class GitCommitFilesResult(BaseModel):
    """单个提交变更的文件清单（抽屉内两级导航的第一级）。"""

    model_config = ConfigDict(extra="forbid")

    hash: str
    files: list[GitCommitFileItem] = []
    truncated: bool = False


class GitRestoreRequest(BaseModel):
    """恢复请求：mode=file 整文件恢复；mode=hunks 按 raw 切片恢复。

    hunks 为 file-diff 响应中 hunk.raw 的原样回传（服务端在重新生成的
    diff 中逐条校验存在后才组装 patch，过期/伪造切片一律 fail-closed）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    mode: str = Field(pattern="^(file|hunks)$")
    hunks: list[str] = []


class GitRestoreResult(BaseModel):
    """恢复结果。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    mode: Literal["file", "hunks"]
    restored_hunks: int = 0

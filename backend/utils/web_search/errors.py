"""网页抓取阶段的类型化结果与失败契约。"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FetchFailure(BaseModel):
    """可供调度、日志和 UI 使用的结构化抓取失败。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    category: str = Field(min_length=1)
    retryable: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    message: str = Field(min_length=1)
    elapsed_ms: float = Field(ge=0)
    downloaded_bytes: int = Field(ge=0)
    retry_after_seconds: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    attempts: int = Field(default=1, ge=1)


class FetchArtifact(BaseModel):
    """原始抓取产物的可追溯元数据，不包含凭据或完整响应头。"""

    model_config = ConfigDict(extra="forbid")

    requested_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    fetched_at: datetime
    mime_type: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    raw_size: int = Field(ge=0)
    cache_key: str = Field(min_length=1)
    etag: str = ""
    last_modified: str = ""
    cache_hit: bool = False

    @field_validator("fetched_at")
    @classmethod
    def normalize_fetched_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        return value.astimezone(timezone.utc)

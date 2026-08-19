"""结构化网页文档与证据包的数据契约。"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import FetchFailure
from ..models import EngineStatus


class LinkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    url: str = Field(min_length=1)


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    kind: Literal[
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
        "quote",
        "math",
        "image",
        "caption",
        "footnote",
        "reference",
    ]
    text: str
    heading_path: list[str] = Field(default_factory=list)
    links: list[LinkRef] = Field(default_factory=list)
    ordinal: int = Field(ge=0)


class DocumentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    title: str
    level: int = Field(ge=1)
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    word_count: int = Field(ge=0)


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str
    authors: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    language: str = ""
    blocks: list[ContentBlock] = Field(default_factory=list)
    outline: list[DocumentSection] = Field(default_factory=list)
    quality: dict[str, float] = Field(default_factory=dict)
    extraction_strategy: str = Field(min_length=1)

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_block_references(self) -> "ExtractedDocument":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id values must be unique within a document")
        known = set(block_ids)
        for section in self.outline:
            unknown = set(section.block_ids) - known
            if unknown:
                raise ValueError(
                    f"section {section.section_id} references unknown blocks: "
                    f"{sorted(unknown)}"
                )
        return self


class EvidenceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    title: str
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)
    retrieval_score: float


class DocumentChunk(BaseModel):
    """Heading-aware retrieval unit built from one or more content blocks."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)
    token_estimate: int = Field(ge=1)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    engine_statuses: list[EngineStatus] = Field(default_factory=list)
    failures: list[FetchFailure] = Field(default_factory=list)
    omitted_count: int = Field(ge=0)
    partial: bool

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "EvidenceBundle":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within a bundle")
        return self

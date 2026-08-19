"""学术检索查询计划的数据契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScholarlyQueryPlan(BaseModel):
    """跨 Provider 学术查询规划器的统一输出。"""

    model_config = ConfigDict(extra="forbid")

    original_query: str = Field(min_length=1)
    topic_terms: list[str] = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year_from: int | None = Field(default=None, ge=1000, le=9999)
    year_to: int | None = Field(default=None, ge=1000, le=9999)
    work_types: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    goal: Literal[
        "known_work",
        "review",
        "foundational",
        "latest",
        "comparison",
        "dataset",
        "citation_tracking",
    ]
    preprints_accepted: bool = True
    open_access_required: bool = False

    @field_validator("topic_terms", "authors", "work_types")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                normalized.append(item)
        return normalized

    @field_validator("topic_terms")
    @classmethod
    def require_topic_terms(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("topic_terms must contain at least one non-empty value")
        return values

    @model_validator(mode="after")
    def validate_year_range(self) -> "ScholarlyQueryPlan":
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must not be after year_to")
        return self

    def provider_filters(self, provider: str) -> tuple[dict[str, object], list[str]]:
        """将计划映射到 Provider 通用字段，并显式返回未支持的条件。"""
        filters: dict[str, object] = {"query": " ".join(self.topic_terms)}
        unsupported: list[str] = []
        if self.authors:
            filters["authors"] = self.authors
        if self.year_from is not None:
            filters["year_from"] = self.year_from
        if self.year_to is not None:
            filters["year_to"] = self.year_to
        if self.work_types:
            filters["work_types"] = self.work_types
        if self.open_access_required:
            filters["open_access_required"] = True
            if provider not in {"openalex", "crossref", "unpaywall", "core"}:
                unsupported.append("open_access_required")
        if self.identifiers:
            filters["identifiers"] = self.identifiers
            if provider not in {
                "openalex",
                "crossref",
                "semantic_scholar",
                "pubmed",
                "arxiv",
            }:
                unsupported.append("identifiers")
        if self.goal == "dataset" and provider not in {"openalex", "datacite"}:
            unsupported.append("goal")
        return filters, unsupported

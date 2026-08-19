"""网络访问离线评测集与预测结果契约。"""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HasCaseId(Protocol):
    case_id: str


TCase = TypeVar("TCase", bound=HasCaseId)


def _unique_case_ids(values: list[TCase]) -> list[TCase]:
    ids = [value.case_id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("case_id values must be unique")
    return values


class SearchJudgment(EvalModel):
    url: str = Field(min_length=1)
    relevance: int = Field(ge=0, le=3)
    authoritative: bool = False


class SearchCase(EvalModel):
    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    category: Literal[
        "current",
        "stable_fact",
        "official_docs",
        "troubleshooting",
        "comparison",
        "long_tail",
    ]
    language: str = Field(min_length=1)
    judgments: list[SearchJudgment] = Field(min_length=1)


class SearchDataset(EvalModel):
    schema_version: Literal[1]
    suite: Literal["search"]
    cases: list[SearchCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def validate_case_ids(cls, values: list[SearchCase]) -> list[SearchCase]:
        return _unique_case_ids(values)


class RankedSearchResult(EvalModel):
    url: str = Field(min_length=1)
    canonical_url: str = ""
    title: str = ""


class SearchPrediction(EvalModel):
    case_id: str = Field(min_length=1)
    results: list[RankedSearchResult] = Field(default_factory=list)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    engine_failures: dict[str, str] = Field(default_factory=dict)
    measurement_gaps: list[str] = Field(default_factory=list)


class SearchPredictions(EvalModel):
    schema_version: Literal[1]
    suite: Literal["search"]
    system: str = Field(min_length=1)
    cases: list[SearchPrediction]

    @field_validator("cases")
    @classmethod
    def validate_case_ids(
        cls, values: list[SearchPrediction]
    ) -> list[SearchPrediction]:
        return _unique_case_ids(values)


class ScholarlyJudgment(EvalModel):
    title: str = Field(min_length=1)
    identifiers: dict[str, str]
    relevance: int = Field(ge=0, le=3)
    open_access: bool = False

    @model_validator(mode="after")
    def require_identifier(self) -> "ScholarlyJudgment":
        if not any(value.strip() for value in self.identifiers.values()):
            raise ValueError("at least one stable identifier is required")
        return self


class ScholarlyCase(EvalModel):
    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    judgments: list[ScholarlyJudgment] = Field(min_length=1)


class ScholarlyDataset(EvalModel):
    schema_version: Literal[1]
    suite: Literal["scholarly"]
    cases: list[ScholarlyCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def validate_case_ids(cls, values: list[ScholarlyCase]) -> list[ScholarlyCase]:
        return _unique_case_ids(values)


class RankedScholarlyResult(EvalModel):
    title: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)
    open_access: bool = False


class ScholarlyPrediction(EvalModel):
    case_id: str = Field(min_length=1)
    results: list[RankedScholarlyResult] = Field(default_factory=list)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    provider_failures: dict[str, str] = Field(default_factory=dict)
    measurement_gaps: list[str] = Field(default_factory=list)


class ScholarlyPredictions(EvalModel):
    schema_version: Literal[1]
    suite: Literal["scholarly"]
    system: str = Field(min_length=1)
    cases: list[ScholarlyPrediction]

    @field_validator("cases")
    @classmethod
    def validate_case_ids(
        cls, values: list[ScholarlyPrediction]
    ) -> list[ScholarlyPrediction]:
        return _unique_case_ids(values)


BlockKind = Literal[
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


class EvalBlock(EvalModel):
    block_id: str = Field(min_length=1)
    kind: BlockKind
    text: str
    heading_path: list[str] = Field(default_factory=list)


class ExtractionCase(EvalModel):
    case_id: str = Field(min_length=1)
    fixture_path: str = Field(min_length=1)
    page_type: str = Field(min_length=1)
    expected_status: str = Field(min_length=1)
    expected_strategy: str = Field(min_length=1)
    gold_blocks: list[EvalBlock] = Field(default_factory=list)
    boilerplate_text: list[str] = Field(default_factory=list)


class ExtractionDataset(EvalModel):
    schema_version: Literal[1]
    suite: Literal["extraction"]
    cases: list[ExtractionCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def validate_case_ids(cls, values: list[ExtractionCase]) -> list[ExtractionCase]:
        return _unique_case_ids(values)


class ExtractionPrediction(EvalModel):
    case_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    blocks: list[EvalBlock] = Field(default_factory=list)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    measurement_gaps: list[str] = Field(default_factory=list)


class ExtractionPredictions(EvalModel):
    schema_version: Literal[1]
    suite: Literal["extraction"]
    system: str = Field(min_length=1)
    cases: list[ExtractionPrediction]

    @field_validator("cases")
    @classmethod
    def validate_case_ids(
        cls, values: list[ExtractionPrediction]
    ) -> list[ExtractionPrediction]:
        return _unique_case_ids(values)


class GoldEvidence(EvalModel):
    evidence_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    text: str = Field(min_length=1)


class GroundingCase(EvalModel):
    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer_terms: list[str] = Field(min_length=1)
    evidence: list[GoldEvidence] = Field(min_length=1)

    @field_validator("expected_answer_terms")
    @classmethod
    def reject_blank_terms(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("expected answer terms must not be blank")
        return values


class GroundingDataset(EvalModel):
    schema_version: Literal[1]
    suite: Literal["grounding"]
    cases: list[GroundingCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def validate_case_ids(cls, values: list[GroundingCase]) -> list[GroundingCase]:
        return _unique_case_ids(values)


class EvidenceCitation(EvalModel):
    evidence_id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class GroundingPrediction(EvalModel):
    case_id: str = Field(min_length=1)
    answer: str
    citations: list[EvidenceCitation] = Field(default_factory=list)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    token_count: int | None = Field(default=None, ge=0)
    tool_rounds: int | None = Field(default=None, ge=0)
    measurement_gaps: list[str] = Field(default_factory=list)


class GroundingPredictions(EvalModel):
    schema_version: Literal[1]
    suite: Literal["grounding"]
    system: str = Field(min_length=1)
    cases: list[GroundingPrediction]

    @field_validator("cases")
    @classmethod
    def validate_case_ids(
        cls, values: list[GroundingPrediction]
    ) -> list[GroundingPrediction]:
        return _unique_case_ids(values)


Dataset = SearchDataset | ScholarlyDataset | ExtractionDataset | GroundingDataset
Predictions = (
    SearchPredictions
    | ScholarlyPredictions
    | ExtractionPredictions
    | GroundingPredictions
)

DATASET_MODELS = {
    "search": SearchDataset,
    "scholarly": ScholarlyDataset,
    "extraction": ExtractionDataset,
    "grounding": GroundingDataset,
}

PREDICTION_MODELS = {
    "search": SearchPredictions,
    "scholarly": ScholarlyPredictions,
    "extraction": ExtractionPredictions,
    "grounding": GroundingPredictions,
}

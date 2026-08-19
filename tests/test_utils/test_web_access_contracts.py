"""阶段 0 网页访问 v2 数据契约测试。"""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.utils.scholarly_research.planner import ScholarlyQueryPlan
from backend.utils.web_search.errors import FetchArtifact, FetchFailure
from backend.utils.web_search.extract.models import (
    ContentBlock,
    DocumentSection,
    EvidenceBundle,
    EvidenceChunk,
    ExtractedDocument,
)
from backend.utils.web_search.models import (
    EngineStatus,
    SearchCandidate,
    SearchResponse,
)
from backend.utils.web_search.planner import SearchQueryPlan


def test_search_query_plan_normalizes_lists_and_dates() -> None:
    plan = SearchQueryPlan(
        original_query="  FastAPI lifespan  ",
        intent="official_docs",
        languages=["en", "EN", "  zh-CN  "],
        variants=["FastAPI lifespan", "fastapi lifespan", " official docs "],
        required_terms=["lifespan", ""],
        preferred_domains=["fastapi.tiangolo.com"],
        date_from=date(2025, 1, 1),
        date_to=date(2026, 1, 1),
    )

    assert plan.languages == ["en", "zh-CN"]
    assert plan.variants == ["FastAPI lifespan", "official docs"]
    assert plan.required_terms == ["lifespan"]


def test_search_query_plan_rejects_empty_variants_after_cleanup() -> None:
    with pytest.raises(ValidationError, match="at least one non-empty"):
        SearchQueryPlan(
            original_query="query",
            intent="fact",
            languages=["en"],
            variants=["  "],
        )


def test_search_query_plan_rejects_extra_fields_and_reverse_dates() -> None:
    payload = {
        "original_query": "query",
        "intent": "current",
        "languages": ["en"],
        "variants": ["query"],
        "date_from": date(2026, 1, 2),
        "date_to": date(2026, 1, 1),
    }
    with pytest.raises(ValidationError, match="date_from"):
        SearchQueryPlan.model_validate(payload)

    payload["date_from"] = None
    payload["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SearchQueryPlan.model_validate(payload)


def test_search_candidate_and_fetch_artifact_normalize_utc() -> None:
    local_tz = timezone(timedelta(hours=8))
    candidate = SearchCandidate(
        url="https://example.test/article",
        canonical_url="https://example.test/article",
        title="Article",
        snippet="Summary",
        source_type="official_docs",
        final_score=1.0,
        published_at=datetime(2026, 8, 18, 12, tzinfo=local_tz),
    )
    artifact = FetchArtifact(
        requested_url="https://example.test/article",
        final_url="https://example.test/article",
        fetched_at=datetime(2026, 8, 18, 12, tzinfo=local_tz),
        mime_type="text/html",
        strategy="http",
        content_hash="sha256:test",
        raw_size=10,
        cache_key="test-key",
    )

    assert candidate.published_at == datetime(2026, 8, 18, 4, tzinfo=timezone.utc)
    assert artifact.fetched_at == datetime(2026, 8, 18, 4, tzinfo=timezone.utc)


def test_web_contracts_reject_naive_datetimes_and_negative_budgets() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        SearchCandidate(
            url="https://example.test",
            canonical_url="https://example.test/",
            title="",
            snippet="",
            source_type="unknown",
            final_score=0,
            published_at=datetime(2026, 8, 18),
        )

    with pytest.raises(ValidationError):
        FetchFailure(
            code="timeout",
            phase="http",
            category="network",
            retryable=True,
            message="Timed out",
            elapsed_ms=-1,
            downloaded_bytes=0,
        )


def test_extracted_document_validates_outline_block_references() -> None:
    block = ContentBlock(
        block_id="block-1",
        kind="paragraph",
        text="Evidence text",
        ordinal=0,
    )
    document = ExtractedDocument(
        document_id="doc-1",
        url="https://example.test/article",
        title="Article",
        blocks=[block],
        outline=[
            DocumentSection(
                section_id="section-1",
                title="Introduction",
                level=1,
                block_ids=["block-1"],
                word_count=2,
            )
        ],
        extraction_strategy="trafilatura",
    )
    assert document.outline[0].block_ids == ["block-1"]

    with pytest.raises(ValidationError, match="unknown blocks"):
        ExtractedDocument(
            document_id="doc-1",
            url="https://example.test/article",
            title="Article",
            blocks=[block],
            outline=[
                DocumentSection(
                    section_id="section-1",
                    title="Introduction",
                    level=1,
                    block_ids=["missing"],
                    word_count=2,
                )
            ],
            extraction_strategy="trafilatura",
        )


def test_evidence_bundle_requires_unique_evidence_ids() -> None:
    chunk = EvidenceChunk(
        evidence_id="evidence-1",
        document_id="doc-1",
        source_url="https://example.test/article",
        title="Article",
        block_ids=["block-1"],
        text="Evidence text",
        retrieval_score=0.9,
    )
    status = EngineStatus(
        engine="mock",
        status="ok",
        elapsed_ms=1,
        result_count=1,
        retryable=False,
    )
    with pytest.raises(ValidationError, match="evidence_id"):
        EvidenceBundle(
            bundle_id="bundle-1",
            query="query",
            evidence=[chunk, chunk.model_copy()],
            engine_statuses=[status],
            omitted_count=0,
            partial=False,
        )


def test_scholarly_query_plan_validates_year_range() -> None:
    plan = ScholarlyQueryPlan(
        original_query="transformer surveys",
        topic_terms=["transformer"],
        year_from=2024,
        year_to=2026,
        goal="review",
    )
    assert plan.preprints_accepted is True

    with pytest.raises(ValidationError, match="year_from"):
        ScholarlyQueryPlan(
            original_query="transformer surveys",
            topic_terms=["transformer"],
            year_from=2026,
            year_to=2024,
            goal="review",
        )

    with pytest.raises(ValidationError, match="topic_terms"):
        ScholarlyQueryPlan(
            original_query="transformer surveys",
            topic_terms=[" "],
            goal="review",
        )


def test_legacy_search_response_contract_remains_available() -> None:
    response = SearchResponse(query="legacy")
    assert response.query == "legacy"
    assert response.results == []

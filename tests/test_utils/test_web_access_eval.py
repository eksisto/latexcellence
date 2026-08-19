"""网络访问离线评测工具测试。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.web_access_eval.metrics import (
    evaluate_extraction,
    evaluate_grounding,
    evaluate_scholarly,
    evaluate_search,
)
from tools.web_access_eval.models import (
    EvalBlock,
    EvidenceCitation,
    ExtractionDataset,
    ExtractionPrediction,
    ExtractionPredictions,
    GroundingDataset,
    GroundingPrediction,
    GroundingPredictions,
    RankedScholarlyResult,
    RankedSearchResult,
    ScholarlyDataset,
    ScholarlyPrediction,
    ScholarlyPredictions,
    SearchDataset,
    SearchPrediction,
    SearchPredictions,
)
from tools.web_access_eval.run_eval import load_dataset, replay, validate_datasets
from tools.web_access_eval.structured import (
    collect_extraction as collect_structured_extraction,
)
from tools.web_access_eval.legacy import collect_extraction, collect_grounding
from tools.web_access_eval.legacy import collect_extraction

DATASETS = Path("tools/web_access_eval/datasets")


def test_seed_datasets_validate_offline() -> None:
    paths = validate_datasets(DATASETS)
    assert {path.stem for path in paths} == {
        "search",
        "scholarly",
        "extraction",
        "grounding",
    }


def test_extraction_fixture_path_cannot_escape_dataset_directory(
    tmp_path: Path,
) -> None:
    dataset = json.loads((DATASETS / "extraction.json").read_text(encoding="utf-8"))
    dataset["cases"][0]["fixture_path"] = "../search.json"
    path = tmp_path / "extraction.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes dataset directory"):
        validate_datasets(tmp_path)


def test_dataset_case_ids_are_unique() -> None:
    payload = json.loads((DATASETS / "search.json").read_text(encoding="utf-8"))
    payload["cases"].append(payload["cases"][0])
    with pytest.raises(ValidationError, match="case_id values must be unique"):
        SearchDataset.model_validate(payload)


def test_contract_rejects_future_schema_and_non_finite_latency() -> None:
    payload = json.loads((DATASETS / "search.json").read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    with pytest.raises(ValidationError):
        SearchDataset.model_validate(payload)

    with pytest.raises(ValidationError):
        SearchPrediction(case_id="case", latency_ms=float("nan"))


def test_search_metrics_dedupe_urls_and_ignore_missing_latency() -> None:
    dataset = load_dataset(DATASETS / "search.json")
    predictions = SearchPredictions(
        schema_version=1,
        suite="search",
        system="test",
        cases=[
            SearchPrediction(
                case_id="search-official-fastapi-lifespan",
                results=[
                    RankedSearchResult(
                        url="https://fastapi.tiangolo.com/advanced/events/?utm_source=test"
                    ),
                    RankedSearchResult(
                        url="https://fastapi.tiangolo.com/advanced/events/"
                    ),
                ],
                latency_ms=12,
            )
        ],
    )
    report = evaluate_search(dataset, predictions)
    row = report["cases"][0]
    assert row["duplicate_rate_at_10"] == 0.5
    assert row["ndcg_at_10"] <= 1
    assert report["aggregate"]["prediction_coverage"] == 0.5
    assert report["aggregate"]["latency_p50_ms"] == 12


def test_scholarly_metrics_normalize_arxiv_versions_and_dedupe() -> None:
    dataset = load_dataset(DATASETS / "scholarly.json")
    predictions = ScholarlyPredictions(
        schema_version=1,
        suite="scholarly",
        system="test",
        cases=[
            ScholarlyPrediction(
                case_id="scholarly-known-attention",
                results=[
                    RankedScholarlyResult(identifiers={"arxiv": "arXiv:1706.03762v2"}),
                    RankedScholarlyResult(identifiers={"arxiv": "1706.03762"}),
                ],
                latency_ms=20,
            )
        ],
    )
    report = evaluate_scholarly(dataset, predictions)
    assert report["cases"][0]["recall_at_20"] == 1
    assert report["cases"][0]["ndcg_at_10"] <= 1


def test_extraction_metrics_include_order_and_status() -> None:
    dataset = load_dataset(DATASETS / "extraction.json")
    predictions = ExtractionPredictions(
        schema_version=1,
        suite="extraction",
        system="test",
        cases=[
            ExtractionPrediction(
                case_id="extract-authored-technical-page",
                status="ok",
                strategy="html",
                blocks=[
                    EvalBlock(
                        block_id="checks-list",
                        kind="list",
                        text="Pin tool versions. Normalize timestamps. Compare artifact hashes.",
                        heading_path=["Reproducible builds", "Verification"],
                    ),
                    EvalBlock(
                        block_id="overview-heading",
                        kind="heading",
                        text="Reproducible builds",
                        heading_path=["Reproducible builds"],
                    ),
                ],
                latency_ms=5,
            )
        ],
    )
    report = evaluate_extraction(dataset, predictions)
    assert report["cases"][0]["status_accuracy"] == 1
    assert report["cases"][0]["block_order"] == 0.25


def test_grounding_metrics_require_matching_evidence_url_and_id() -> None:
    dataset = load_dataset(DATASETS / "grounding.json")
    predictions = GroundingPredictions(
        schema_version=1,
        suite="grounding",
        system="test",
        cases=[
            GroundingPrediction(
                case_id="grounding-reproducible-build",
                answer="Compare the SHA-256 hash.",
                citations=[
                    EvidenceCitation(
                        evidence_id="hash-command",
                        url="https://fixtures.latexcellence.local/other-article",
                    )
                ],
                latency_ms=3,
                token_count=20,
                tool_rounds=1,
            )
        ],
    )
    report = evaluate_grounding(dataset, predictions)
    assert report["cases"][0]["answer_term_recall"] == 1
    assert report["cases"][0]["evidence_recall"] == 0


def test_replay_rejects_suite_mismatch(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(
        json.dumps(
            {"schema_version": 1, "suite": "scholarly", "system": "x", "cases": []}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Suite mismatch"):
        replay(DATASETS / "search.json", prediction_path)


def test_replay_rejects_unknown_case_id(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite": "search",
                "system": "x",
                "cases": [{"case_id": "typo", "results": [], "latency_ms": 1}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown case IDs"):
        replay(DATASETS / "search.json", prediction_path)


def test_replay_writes_deterministic_report(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite": "search",
                "system": "fixture",
                "cases": [
                    {
                        "case_id": "search-official-fastapi-lifespan",
                        "results": [
                            {"url": "https://fastapi.tiangolo.com/advanced/events/"}
                        ],
                        "latency_ms": 10,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = replay(DATASETS / "search.json", prediction_path)
    assert report["suite"] == "search"
    assert report["aggregate"]["prediction_coverage"] == 0.5


def test_legacy_extraction_capture_preserves_observability_gap() -> None:
    dataset_path = DATASETS / "extraction.json"
    dataset = load_dataset(dataset_path)
    predictions = collect_extraction(dataset, dataset_path)
    prediction = predictions.cases[0]
    assert predictions.system == "legacy"
    assert prediction.strategy == "legacy_trafilatura"
    assert (
        "legacy_extractor_returns_plain_text_without_blocks"
        in prediction.measurement_gaps
    )
    assert prediction.blocks[0].text


def test_structured_extraction_capture_preserves_gold_structure() -> None:
    dataset_path = DATASETS / "extraction.json"
    dataset = load_dataset(dataset_path)
    predictions = collect_structured_extraction(dataset, dataset_path)
    report = evaluate_extraction(dataset, predictions)

    assert report["aggregate"]["block_recall"] == 1
    assert report["aggregate"]["kind_preservation"] == 1
    assert report["aggregate"]["heading_preservation"] == 1
    assert report["aggregate"]["boilerplate_ratio"] == 0


def test_grounding_metrics_omit_unknown_usage_values() -> None:
    dataset = load_dataset(DATASETS / "grounding.json")
    predictions = GroundingPredictions(
        schema_version=1,
        suite="grounding",
        system="legacy",
        cases=[
            GroundingPrediction(
                case_id="grounding-reproducible-build",
                answer="Compare the SHA-256 hash.",
                latency_ms=4,
                token_count=None,
                tool_rounds=None,
            )
        ],
    )
    report = evaluate_grounding(dataset, predictions)
    assert "token_count" not in report["cases"][0]
    assert "tool_rounds" not in report["cases"][0]


def test_grounding_capture_rejects_missing_latency(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps(
            {"grounding-reproducible-build": {"answer": "Compare the SHA-256 hash."}}
        ),
        encoding="utf-8",
    )
    dataset = load_dataset(DATASETS / "grounding.json")
    with pytest.raises(ValueError, match="lacks latency_ms"):
        collect_grounding(dataset, trace_path)

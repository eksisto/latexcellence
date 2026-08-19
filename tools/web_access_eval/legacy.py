"""Capture predictions from the current network-access implementations.

This module is intentionally an evaluation-only adapter. It is never imported by
the FastAPI runtime and requires ``--allow-network`` for search providers.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.web_access_eval.models import (  # noqa: E402
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
from tools.web_access_eval.run_eval import load_dataset  # noqa: E402


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def collect_search(
    dataset: SearchDataset, *, strategy: str = "legacy"
) -> SearchPredictions:
    from backend.utils.web_search import get_search_manager

    manager = get_search_manager()
    rows: list[SearchPrediction] = []
    for case in dataset.cases:
        started = time.perf_counter()
        try:
            response = await manager.search(case.query, max_results=20)
            engine_failures = {
                f"{status.engine}:{index}": status.error_code or status.status
                for index, status in enumerate(response.engine_statuses)
                if status.status not in {"ok", "empty"}
            }
            rows.append(
                SearchPrediction(
                    case_id=case.case_id,
                    results=[
                        RankedSearchResult(url=item.url, title=item.title)
                        for item in response.results
                    ],
                    latency_ms=(time.perf_counter() - started) * 1000,
                    engine_failures=engine_failures,
                    measurement_gaps=(
                        ["legacy_manager_does_not_expose_per_engine_failures"]
                        if strategy == "legacy"
                        else []
                    ),
                )
            )
        except Exception as exc:  # keep other cases measurable
            rows.append(
                SearchPrediction(
                    case_id=case.case_id,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    engine_failures={strategy: type(exc).__name__},
                )
            )
    return SearchPredictions(
        schema_version=1, suite="search", system=strategy, cases=rows
    )


async def collect_scholarly(dataset: ScholarlyDataset) -> ScholarlyPredictions:
    from backend.utils.scholarly_research import (
        close_research_http_client,
        get_research_manager,
        initialize_research_http_client,
        reset_research_manager,
    )

    await initialize_research_http_client()
    try:
        manager = get_research_manager()
        rows: list[ScholarlyPrediction] = []
        for case in dataset.cases:
            started = time.perf_counter()
            try:
                response = await manager.search_works(case.query, max_results=20)
                results = [
                    RankedScholarlyResult(
                        title=item.title,
                        identifiers={
                            key: value
                            for key, value in {
                                "doi": item.doi,
                                "arxiv": item.arxiv_id,
                                "pmid": item.pmid,
                                "openalex": item.openalex_id,
                            }.items()
                            if value
                        },
                        open_access=item.is_oa,
                    )
                    for item in response.results
                ]
                rows.append(
                    ScholarlyPrediction(
                        case_id=case.case_id,
                        results=results,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        measurement_gaps=[
                            "legacy_manager_does_not_expose_per_provider_failures"
                        ],
                    )
                )
            except Exception as exc:
                rows.append(
                    ScholarlyPrediction(
                        case_id=case.case_id,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        provider_failures={"legacy": type(exc).__name__},
                    )
                )
        return ScholarlyPredictions(
            schema_version=1, suite="scholarly", system="legacy", cases=rows
        )
    finally:
        await close_research_http_client()
        reset_research_manager()


def collect_extraction(
    dataset: ExtractionDataset, dataset_path: Path
) -> ExtractionPredictions:
    import trafilatura

    rows: list[ExtractionPrediction] = []
    root = dataset_path.parent.resolve()
    for case in dataset.cases:
        started = time.perf_counter()
        fixture = (root / case.fixture_path).resolve()
        if not fixture.is_relative_to(root) or not fixture.is_file():
            raise ValueError(
                f"extraction fixture is missing or escapes dataset directory: "
                f"{case.fixture_path}"
            )
        html = fixture.read_text(encoding="utf-8")
        text = trafilatura.extract(html, output_format="txt") or ""
        blocks = (
            [EvalBlock(block_id="legacy-content", kind="paragraph", text=text)]
            if text
            else []
        )
        rows.append(
            ExtractionPrediction(
                case_id=case.case_id,
                status="ok" if text else "empty",
                strategy="legacy_trafilatura",
                blocks=blocks,
                latency_ms=(time.perf_counter() - started) * 1000,
                measurement_gaps=["legacy_extractor_returns_plain_text_without_blocks"],
            )
        )
    return ExtractionPredictions(
        schema_version=1, suite="extraction", system="legacy", cases=rows
    )


def collect_grounding(
    dataset: GroundingDataset, trace_path: Path
) -> GroundingPredictions:
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("grounding trace must be a JSON object keyed by case_id")
    rows: list[GroundingPrediction] = []
    for case in dataset.cases:
        item = payload.get(case.case_id)
        if not isinstance(item, dict):
            continue
        if "latency_ms" not in item:
            raise ValueError(f"grounding trace lacks latency_ms for {case.case_id}")
        citations = [
            EvidenceCitation.model_validate(value)
            for value in item.get("citations", [])
        ]
        rows.append(
            GroundingPrediction(
                case_id=case.case_id,
                answer=str(item.get("answer", "")),
                citations=citations,
                latency_ms=float(item["latency_ms"]),
                token_count=item.get("token_count"),
                tool_rounds=item.get("tool_rounds"),
                measurement_gaps=list(item.get("measurement_gaps", [])),
            )
        )
    return GroundingPredictions(
        schema_version=1, suite="grounding", system="legacy", cases=rows
    )


async def _async_main(args: argparse.Namespace) -> int:
    dataset_path = args.dataset
    dataset = load_dataset(dataset_path)
    if args.suite != dataset.suite:
        raise ValueError(f"dataset suite is {dataset.suite!r}, not {args.suite!r}")
    if args.suite in {"search", "scholarly"} and not args.allow_network:
        raise ValueError("live legacy capture requires --allow-network")
    if args.suite in {"search", "scholarly"}:
        from backend.core.config import load_config

        load_config(str(args.config))
    if args.suite == "search":
        from backend.core.config import get_config
        from backend.utils.web_search import reset_search_manager

        get_config().features.function_calling.web_search.strategy = (
            args.search_strategy
        )
        reset_search_manager()
        predictions = await collect_search(dataset, strategy=args.search_strategy)
    elif args.suite == "scholarly":
        predictions = await collect_scholarly(dataset)
    elif args.suite == "extraction":
        predictions = collect_extraction(dataset, dataset_path)
    else:
        if args.trace is None:
            raise ValueError("grounding capture requires --trace")
        predictions = collect_grounding(dataset, args.trace)
    _write(args.output, predictions.model_dump(mode="json"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=["search", "scholarly", "extraction", "grounding"],
        required=True,
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument(
        "--search-strategy", choices=["legacy", "aggregate"], default="legacy"
    )
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_async_main(args))
    except (OSError, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

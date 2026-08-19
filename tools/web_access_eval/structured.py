"""Offline prediction collection for the structured HTML extractor."""

import hashlib
import time
from pathlib import Path

from backend.utils.web_search.extract.registry import ExtractorRegistry

from .models import (
    EvalBlock,
    ExtractionDataset,
    ExtractionPrediction,
    ExtractionPredictions,
)


def collect_extraction(
    dataset: ExtractionDataset, dataset_path: Path
) -> ExtractionPredictions:
    registry = ExtractorRegistry()
    cases: list[ExtractionPrediction] = []
    for case in dataset.cases:
        started = time.perf_counter()
        fixture = dataset_path.parent / case.fixture_path
        body = fixture.read_bytes()
        content_hash = hashlib.sha256(body).hexdigest()
        document = registry.extract(
            body,
            "text/html",
            f"https://fixture.invalid/{fixture.name}",
            content_hash,
        )
        cases.append(
            ExtractionPrediction(
                case_id=case.case_id,
                status="ok" if document.blocks else "empty_document",
                strategy=case.expected_strategy,
                blocks=[
                    EvalBlock(
                        block_id=block.block_id,
                        kind=block.kind,
                        text=block.text,
                        heading_path=block.heading_path,
                    )
                    for block in document.blocks
                ],
                latency_ms=(time.perf_counter() - started) * 1000,
                measurement_gaps=[],
            )
        )
    return ExtractionPredictions(
        schema_version=1,
        suite="extraction",
        system="structured-html-v1",
        cases=cases,
    )

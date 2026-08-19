"""Tests for the stage 6 capacity and rollback verification helpers."""

from pathlib import Path

import pytest

from tools.web_access_eval.stage6 import check_rollback_config, run_capacity

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_example_config_preserves_rollback_path() -> None:
    report = check_rollback_config(PROJECT_ROOT / "config.example.json")

    assert report["passed"] is True
    assert all(report["checks"].values())


@pytest.mark.asyncio
async def test_capacity_smoke_reports_bounded_workload() -> None:
    report = await run_capacity(
        PROJECT_ROOT
        / "tools"
        / "web_access_eval"
        / "datasets"
        / "pages"
        / "technical_article.html",
        iterations=4,
        concurrency=2,
    )

    assert report["successes"] == 4
    assert report["latency_p95_ms"] >= 0
    assert report["peak_memory_mb"] >= 0

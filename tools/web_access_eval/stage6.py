"""Stage 6 local capacity smoke test and rollback configuration check."""

import argparse
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import AppConfig  # noqa: E402
from backend.utils.web_search.extract.html import extract_html  # noqa: E402


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


async def run_capacity(
    fixture: Path, *, iterations: int, concurrency: int
) -> dict[str, Any]:
    """Measure local structured HTML extraction under bounded concurrency."""
    body = fixture.read_text(encoding="utf-8")
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def extract_once(index: int) -> None:
        async with semaphore:
            started = time.perf_counter()
            document = await asyncio.to_thread(
                extract_html,
                body,
                f"https://fixture.invalid/article/{index}",
                f"fixture-{index}",
            )
            if not document.blocks:
                raise RuntimeError("capacity fixture produced no content blocks")
            latencies.append((time.perf_counter() - started) * 1000)

    tracemalloc.start()
    started = time.perf_counter()
    await asyncio.gather(*(extract_once(index) for index in range(iterations)))
    elapsed_ms = (time.perf_counter() - started) * 1000
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "workload": "structured_html_extraction",
        "iterations": iterations,
        "concurrency": concurrency,
        "successes": len(latencies),
        "elapsed_ms": round(elapsed_ms, 3),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "peak_memory_mb": round(peak_bytes / (1024 * 1024), 3),
    }


def check_rollback_config(config_path: Path) -> dict[str, Any]:
    """Validate that the example config retains every documented rollback switch."""
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = AppConfig.model_validate(payload)
    web = config.features.function_calling.web_search
    scholarly = config.features.function_calling.scholarly_research
    checks = {
        "search_legacy": web.strategy == "legacy",
        "page_reading_legacy": web.page_reading.strategy == "legacy",
        "browser_disabled": not web.page_reading.browser.enabled,
        "scholarly_ranking_legacy": scholarly.ranking_mode == "legacy",
        "scholarly_full_text_disabled": not scholarly.full_text_enabled,
        "evidence_bundle_switch_present": isinstance(
            config.features.writing_chat.evidence_bundle_enabled, bool
        ),
    }
    return {"checks": checks, "passed": all(checks.values())}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capacity = subparsers.add_parser("capacity", help="运行本地结构化抽取容量冒烟测试")
    capacity.add_argument(
        "--fixture",
        type=Path,
        default=TOOL_ROOT / "datasets" / "pages" / "technical_article.html",
    )
    capacity.add_argument("--iterations", type=int, default=100)
    capacity.add_argument("--concurrency", type=int, default=8)
    capacity.add_argument("--max-p95-ms", type=float)
    capacity.add_argument("--max-peak-memory-mb", type=float)
    rollback = subparsers.add_parser("rollback-check", help="校验默认配置中的回滚开关")
    rollback.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config.example.json"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "rollback-check":
        report = check_rollback_config(args.config)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 0 if report["passed"] else 1
    if args.iterations < 1 or args.concurrency < 1:
        sys.stderr.write("error: iterations and concurrency must be positive\n")
        return 2
    report = asyncio.run(
        run_capacity(
            args.fixture,
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
    )
    passed = True
    if args.max_p95_ms is not None:
        passed = passed and report["latency_p95_ms"] <= args.max_p95_ms
    if args.max_peak_memory_mb is not None:
        passed = passed and report["peak_memory_mb"] <= args.max_peak_memory_mb
    report["passed"] = passed
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

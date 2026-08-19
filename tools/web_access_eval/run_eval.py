"""校验或离线重放 Writing Chat 网络访问评测结果。

用法：
    python tools/web_access_eval/run_eval.py validate
    python tools/web_access_eval/run_eval.py replay --dataset datasets/search.json \
        --predictions results/legacy/search.json --output results/legacy/search-report.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.web_access_eval.metrics import EVALUATORS  # noqa: E402
from tools.web_access_eval.structured import (
    collect_extraction as collect_structured_extraction,
)  # noqa: E402
from tools.web_access_eval.models import (  # noqa: E402
    DATASET_MODELS,
    PREDICTION_MODELS,
    Dataset,
    Predictions,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Top-level JSON value must be an object: {path}")
    return value


def load_dataset(path: Path) -> Dataset:
    payload = _read_json(path)
    suite = payload.get("suite")
    model = DATASET_MODELS.get(suite)
    if model is None:
        raise ValueError(f"Unknown dataset suite in {path}: {suite!r}")
    return model.model_validate(payload)


def load_predictions(path: Path) -> Predictions:
    payload = _read_json(path)
    suite = payload.get("suite")
    model = PREDICTION_MODELS.get(suite)
    if model is None:
        raise ValueError(f"Unknown prediction suite in {path}: {suite!r}")
    return model.model_validate(payload)


def replay(dataset_path: Path, predictions_path: Path) -> dict[str, Any]:
    dataset = load_dataset(dataset_path)
    predictions = load_predictions(predictions_path)
    if dataset.suite != predictions.suite:
        raise ValueError(
            f"Suite mismatch: dataset={dataset.suite}, predictions={predictions.suite}"
        )
    dataset_ids = {case.case_id for case in dataset.cases}
    unknown_ids = sorted(
        case.case_id for case in predictions.cases if case.case_id not in dataset_ids
    )
    if unknown_ids:
        raise ValueError(
            f"Predictions contain unknown case IDs: {', '.join(unknown_ids)}"
        )
    evaluator = EVALUATORS[dataset.suite]
    return evaluator(dataset, predictions)


def validate_datasets(datasets_dir: Path) -> list[Path]:
    paths = sorted(datasets_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"No dataset JSON files found in {datasets_dir}")
    for path in paths:
        dataset = load_dataset(path)
        if dataset.suite == "extraction":
            dataset_root = path.parent.resolve()
            for case in dataset.cases:
                fixture = (dataset_root / case.fixture_path).resolve()
                if not fixture.is_relative_to(dataset_root) or not fixture.is_file():
                    raise ValueError(
                        f"Extraction fixture is missing or escapes dataset directory: "
                        f"{case.fixture_path}"
                    )
    return paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="校验全部数据集")
    validate_parser.add_argument(
        "--datasets-dir", type=Path, default=TOOL_ROOT / "datasets"
    )
    replay_parser = subparsers.add_parser("replay", help="离线重放预测结果")
    replay_parser.add_argument("--dataset", type=Path, required=True)
    replay_parser.add_argument("--predictions", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path)
    collect_parser = subparsers.add_parser(
        "collect-structured-extraction",
        help="用本地 fixture 采集结构化 HTML 抽取预测",
    )
    collect_parser.add_argument(
        "--dataset", type=Path, default=TOOL_ROOT / "datasets" / "extraction.json"
    )
    collect_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            paths = validate_datasets(args.datasets_dir)
            sys.stdout.write(f"Validated {len(paths)} dataset file(s).\n")
            return 0
        if args.command == "collect-structured-extraction":
            dataset = load_dataset(args.dataset)
            if dataset.suite != "extraction":
                raise ValueError("structured collector requires the extraction suite")
            predictions = collect_structured_extraction(dataset, args.dataset)
            rendered = predictions.model_dump_json(indent=2) + "\n"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            return 0
        report = replay(args.dataset, args.predictions)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

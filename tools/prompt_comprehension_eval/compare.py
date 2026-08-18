#!/usr/bin/env python3
"""对比两次提示词评测运行结果，检测回归。

用法：
    # 对比最近两次运行
    python tools/prompt_comprehension_eval/compare.py

    # 对比指定两个结果
    python tools/prompt_comprehension_eval/compare.py \\
        logs/prompt_comprehension_eval/runs/2026-08-14T10-00-00/results.json \\
        logs/prompt_comprehension_eval/runs/2026-08-18T14-30-00/results.json

    # 只显示回归项
    python tools/prompt_comprehension_eval/compare.py --regressions-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.prompt_comprehension_eval.run_eval import (
    compare_reports,
    print_comparison,
)

RUNS_DIR = ROOT / "logs" / "prompt_comprehension_eval" / "runs"


def _find_latest_runs(n: int = 2) -> list[Path]:
    """查找 runs/ 下最近 n 个 results.json。"""
    if not RUNS_DIR.is_dir():
        return []
    dirs = sorted(
        (d for d in RUNS_DIR.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    paths = []
    for d in dirs:
        results_file = d / "results.json"
        if results_file.is_file():
            paths.append(results_file)
            if len(paths) >= n:
                break
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "baseline_path",
        nargs="?",
        default=None,
        help="基线 results.json 路径（省略则自动查找最近两次）",
    )
    parser.add_argument(
        "current_path",
        nargs="?",
        default=None,
        help="当前 results.json 路径",
    )
    parser.add_argument(
        "--regressions-only",
        action="store_true",
        help="只显示回归项",
    )
    args = parser.parse_args(argv)

    if args.baseline_path and args.current_path:
        baseline_file = Path(args.baseline_path)
        current_file = Path(args.current_path)
    else:
        runs = _find_latest_runs(2)
        if len(runs) < 2:
            print("错误: 找不到足够多的运行记录（需要至少 2 次）")
            print(f"搜索目录: {RUNS_DIR}")
            return 1
        baseline_file, current_file = runs[1], runs[0]

    if not baseline_file.is_file():
        print(f"错误: 基线文件不存在: {baseline_file}")
        return 1
    if not current_file.is_file():
        print(f"错误: 当前文件不存在: {current_file}")
        return 1

    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    current = json.loads(current_file.read_text(encoding="utf-8"))

    comparison = compare_reports(baseline, current)
    print_comparison(comparison, regressions_only=args.regressions_only)

    return 1 if comparison["verdict"] == "REGRESSION" else 0


if __name__ == "__main__":
    raise SystemExit(main())

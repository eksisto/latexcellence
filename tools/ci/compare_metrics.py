"""评测基线比对：把 logs/ci/metrics/ 下的指标与 tools/ci/baselines/ 基线比对，回归时非零退出。

比对规则（tools/ci/metric_rules.py）：
    - lower 指标：current > baseline + tol 算回归；higher 指标对称
    - tol 默认按 suite 类别取 DEFAULT_TOLERANCES，基线文件的
      "tolerances" 字段可按指标覆盖
    - prompt_comprehension 额外做逐用例状态比对（排名上升即回归，
      与该工具自带 compare.py 的规则一致）
    - 基线有而本次缺失的指标 → 失败（防止采集口径变化静默掩盖回归）；
      本次新增的指标只提示，不判定
    - 无基线的 suite 默认放行并提示如何播种基线（--require-baseline 可收紧）

用法：
    比对：
        python tools/ci/compare_metrics.py --metrics-dir logs/ci/metrics \
            --baselines-dir tools/ci/baselines --output logs/ci/regression_report.json
    把本次结果更新为基线（保留已有 tolerances，人工审核 diff 后提交）：
        python tools/ci/compare_metrics.py --metrics-dir logs/ci/metrics \
            --baselines-dir tools/ci/baselines --update-baseline [suite ...]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ci.metric_rules import (  # noqa: E402
    CASE_STATUS_RANKS,
    DIRECTIONS,
    default_tolerance,
    suite_class,
)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent.parent,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def compare_one(current: dict, baseline: dict) -> dict:
    """单 suite 比对，返回 {status, regressions, improvements, notes}。"""
    suite = current["suite"]
    tol_map = baseline.get("tolerances", {})
    regressions: list[dict] = []
    improvements: list[dict] = []
    notes: list[str] = []
    base_metrics = baseline.get("metrics", {})
    cur_metrics = current.get("metrics", {})

    for name, base_val in base_metrics.items():
        rule = DIRECTIONS.get(name)
        if rule is None:
            notes.append(f"指标 {name} 未在 metric_rules.DIRECTIONS 登记方向，跳过判定")
            continue
        if name not in cur_metrics:
            regressions.append(
                {
                    "metric": name,
                    "baseline": base_val,
                    "current": None,
                    "tolerance": tol_map.get(name, default_tolerance(suite, rule[1])),
                    "reason": "本次结果缺失该指标（采集口径变化？）",
                }
            )
            continue
        cur_val = cur_metrics[name]
        direction, kind = rule
        tol = tol_map.get(name, default_tolerance(suite, kind))
        if direction == "lower" and cur_val > base_val + tol:
            regressions.append(
                {
                    "metric": name,
                    "baseline": base_val,
                    "current": cur_val,
                    "tolerance": tol,
                    "reason": "越大越差的指标超出容差",
                }
            )
        elif direction == "higher" and cur_val < base_val - tol:
            regressions.append(
                {
                    "metric": name,
                    "baseline": base_val,
                    "current": cur_val,
                    "tolerance": tol,
                    "reason": "越大越好的指标跌破容差",
                }
            )
        elif cur_val != base_val:
            improvements.append(
                {"metric": name, "baseline": base_val, "current": cur_val}
            )

    new_metrics = [n for n in cur_metrics if n not in base_metrics]
    if new_metrics:
        notes.append(
            f"新增指标（基线无此值，跳过比对）：{', '.join(sorted(new_metrics))}"
        )

    # prompt_comprehension：逐用例状态比对
    case_regressions: list[dict] = []
    base_cases = baseline.get("cases") or {}
    cur_cases = current.get("cases") or {}
    for name, base_status in base_cases.items():
        cur_status = cur_cases.get(name)
        if cur_status is None:
            continue
        base_rank = CASE_STATUS_RANKS.get(base_status)
        cur_rank = CASE_STATUS_RANKS.get(cur_status)
        if base_rank is None or cur_rank is None:
            notes.append(f"用例 {name} 状态异常：{base_status} → {cur_status}")
        elif cur_rank > base_rank:
            case_regressions.append(
                {"case": name, "baseline": base_status, "current": cur_status}
            )

    status = "regression" if (regressions or case_regressions) else "pass"
    return {
        "status": status,
        "baseline_updated_at": baseline.get("updated_at"),
        "regressions": regressions,
        "case_regressions": case_regressions,
        "improvements": improvements,
        "notes": notes,
    }


def update_baseline(current: dict, baseline_path: Path) -> None:
    """把本次指标写为基线：保留已有 tolerances，记录更新时间与 commit。"""
    old = _load(baseline_path) or {}
    payload = {
        "suite": current["suite"],
        "class": suite_class(current["suite"]),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": _git_commit(),
        "metrics": current["metrics"],
        "tolerances": old.get("tolerances", {}),
    }
    if current.get("cases"):
        payload["cases"] = current["cases"]
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  基线已更新: {baseline_path}（请人工审核 diff 后提交入库）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metrics-dir", required=True, type=Path)
    parser.add_argument("--baselines-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="回归报告 JSON 输出路径")
    parser.add_argument(
        "--update-baseline",
        nargs="*",
        metavar="SUITE",
        help="把本次结果写为基线（可选指定 suite；指定后不做回归判定，恒返回 0）",
    )
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help="无基线的 suite 视为失败（基线播种完成后建议开启）",
    )
    args = parser.parse_args()

    metrics_files = sorted(args.metrics_dir.glob("*.json"))
    if not metrics_files:
        print(f"错误：{args.metrics_dir} 下没有 metrics JSON", file=sys.stderr)
        return 1

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suites": {},
    }
    failed = False
    has_regression = False
    pass_count = no_baseline_count = 0

    for mf in metrics_files:
        current = json.loads(mf.read_text(encoding="utf-8"))
        suite = current.get("suite") or mf.stem
        print(f"== {suite} ==")
        baseline_path = args.baselines_dir / f"{suite}.json"

        if args.update_baseline is not None and (
            not args.update_baseline or suite in args.update_baseline
        ):
            update_baseline(current, baseline_path)
            report["suites"][suite] = {"status": "baseline_updated"}
            continue

        baseline = _load(baseline_path)
        if baseline is None:
            no_baseline_count += 1
            hint = (
                f"[{suite}] 暂无基线，本次指标仅记录不判定。"
                "确认结果合理后可播种基线：\n"
                f"  python tools/ci/compare_metrics.py --metrics-dir {args.metrics_dir}"
                f" --baselines-dir {args.baselines_dir} --update-baseline {suite}\n"
                "  然后把 tools/ci/baselines/ 的变更提交入库。"
            )
            print(f"  {hint}")
            report["suites"][suite] = {"status": "no_baseline"}
            if args.require_baseline:
                failed = True
            continue

        result = compare_one(current, baseline)
        report["suites"][suite] = result
        for r in result["regressions"]:
            failed = True
            has_regression = True
            print(
                f"  ✗ 回归 {r['metric']}: {r['baseline']} → {r['current']}"
                f"（容差 ±{r['tolerance']}，{r['reason']}）"
            )
        for r in result["case_regressions"]:
            failed = True
            has_regression = True
            print(f"  ✗ 用例回归 {r['case']}: {r['baseline']} → {r['current']}")
        for imp in result["improvements"]:
            print(f"  变化 {imp['metric']}: {imp['baseline']} → {imp['current']}")
        for note in result["notes"]:
            print(f"  [提示] {note}")
        if result["status"] == "pass":
            pass_count += 1
            print("  ✓ 通过")

    if args.update_baseline is None:
        regression_count = sum(
            1 for s in report["suites"].values() if s["status"] == "regression"
        )
        print(
            f"\n汇总：{pass_count} 套通过，{regression_count} 套回归，"
            f"{no_baseline_count} 套无基线"
        )
        if has_regression:
            print(
                "结论：存在质量回归，请查看上方明细与对应评测报告（report.html / results.json）。"
            )
        elif failed and no_baseline_count:
            print(
                "结论：存在无基线的 suite（--require-baseline 开启时视为失败），请先播种基线。"
            )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"回归报告: {args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""汇总对比多轮结果，并支持双 tag 对照组回归判定 + 通用性门禁。

用法：
    python tools/plotgen_opt/compare.py                     # 全部已知 tag 汇总表
    python tools/plotgen_opt/compare.py <tagA> <tagB> \
        [--targets k1,k2] [--tolerance 5] [--visual-tolerance 1] [--no-gate]

双 tag 模式退出码：
    0  通过（无门禁失败、无显著回退）
    1  Generality Gate 发现新增硬编码特征
    2  对照组回归：有场景显著回退
"""

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
TOOL_ROOT = TOOLS_ROOT / "plotgen_opt"
RESULTS = TOOL_ROOT / "results"
sys.path.insert(0, str(TOOLS_ROOT))

from generality_gate import (  # noqa: E402
    check_hardcoding,
    load_visual_reviews,
    regression_verdict,
)

TAGS = [
    "baseline",
    "opt1",
    "opt2",
    "opt3",
    "opt4",
    "opt5",
    "opt6",
    "opt7",
    "opt8",
    "opt9",
    "opt10",
    "opt11",
    "opt11b",
    "base4",
    "opt12",
    "opt12b",
    "opt13",
]


def load(tag: str) -> dict[str, Any]:
    p = RESULTS / tag / "all_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def score_of(metrics: dict[str, Any]) -> float:
    return float(metrics.get("score", 0))


def _parse_args(
    argv: list[str],
) -> tuple[list[str], set[str], float, float, bool]:
    positional = [a for a in argv if not a.startswith("--")]
    targets: set[str] = set()
    tolerance = 5.0
    visual_tolerance = 1.0
    run_gate = True
    for i, arg in enumerate(argv):
        if arg == "--targets" and i + 1 < len(argv):
            targets = {k for k in argv[i + 1].split(",") if k}
        elif arg == "--tolerance" and i + 1 < len(argv):
            tolerance = float(argv[i + 1])
        elif arg == "--visual-tolerance" and i + 1 < len(argv):
            visual_tolerance = float(argv[i + 1])
        elif arg == "--no-gate":
            run_gate = False
    return positional, targets, tolerance, visual_tolerance, run_gate


def _print_all_table(data: dict[str, dict[str, Any]]) -> None:
    tags = list(data)
    all_scenarios = sorted({k for m in data.values() for k in m})
    header = f"{'场景':<18} " + " ".join(f"{t:>12}" for t in tags)
    print(header)
    print("-" * len(header))
    for sc in all_scenarios:
        row = [sc]
        for t in tags:
            m = data.get(t, {}).get(sc, {})
            if not m:
                row.append(f"{'-':>12}")
                continue
            row.append(
                f"{m.get('score','?'):>3}分 "
                f"{m.get('rounds','?'):>2}轮 "
                f"{m.get('total_s',0):>6.1f}s"
            )
        print(" ".join(row))

    print("\n=== 汇总（均值） ===")
    for t in tags:
        m = data.get(t, {})
        if not m:
            continue
        scores = [v["score"] for v in m.values()]
        rounds = [v.get("rounds") or 0 for v in m.values()]
        times = [v["total_s"] for v in m.values()]
        exec_ok = sum(1 for v in m.values() if v["execution_success"] is True)
        tikz_ok = sum(1 for v in m.values() if v["tikz_success"] is True)
        print(
            f"{t:<10} avg_score={sum(scores)/len(scores):5.1f} "
            f"avg_rounds={sum(rounds)/len(rounds):5.1f} "
            f"sum_time={sum(times):6.1f}s "
            f"exec_ok={exec_ok}/{len(m)} tikz_ok={tikz_ok}/{len(m)} "
            f"total_warn={sum(v['warnings'] for v in m.values())} "
            f"total_err={sum(v['errors'] for v in m.values())}"
        )


def _print_pair_table(tag_a: str, tag_b: str, metrics_a: dict, metrics_b: dict) -> None:
    header = f"{'场景':<18} | {tag_a:<18} | {tag_b:<18}"
    print(header)
    print("-" * len(header))
    for sc in sorted(set(metrics_a) | set(metrics_b)):
        row = f"{sc:<18} | "
        for m in (metrics_a, metrics_b):
            if sc in m:
                row += (
                    f"score={m[sc].get('score', 0):<3} "
                    f"rounds={m[sc].get('rounds', '?'):<3} "
                    f"time={m[sc].get('total_s', 0):>6.1f}s | "
                )
            else:
                row += " " * 18 + " | "
        print(row)


def _run_pair_gate(
    tag_a: str,
    tag_b: str,
    metrics_a: dict,
    metrics_b: dict,
    *,
    targets: set[str],
    tolerance: float,
    visual_tolerance: float,
    run_gate: bool,
) -> int:
    verdict = regression_verdict(
        metrics_a,
        metrics_b,
        score_of=score_of,
        visual_a=load_visual_reviews(tag_a, TOOL_ROOT),
        visual_b=load_visual_reviews(tag_b, TOOL_ROOT),
        tolerance=tolerance,
        visual_tolerance=visual_tolerance,
        target_keys=targets,
    )
    print("\n=== 对照组回归 ===")
    for row in verdict["rows"]:
        tag = "target" if row["kind"] == "target" else "control"
        visual = f" visual={row['visual_delta']:+.1f}" if "visual_delta" in row else ""
        print(
            f"  [{tag}] {row['scenario']}: {row['score_a']:g} → "
            f"{row['score_b']:g} ({row['delta']:+.1f}){visual}"
        )
    for note in verdict["notes"]:
        print(f"  提示：{note}")
    exit_code = 0
    if verdict["failed"]:
        print("\n[BLOCKED] 对照组回归失败，本次优化作废：")
        for issue in verdict["issues"]:
            print(f"  - {issue}")
        exit_code = 2

    if run_gate:
        gate = check_hardcoding()
        if gate["failures"]:
            print("\n[BLOCKED] Generality Gate 失败：提示词 diff 含硬编码特征：")
            for hit in gate["failures"]:
                print(
                    f"  - [{hit['pattern']}] {hit['file']}:{hit['line']}: "
                    f"{hit['text'].strip()}"
                )
            exit_code = exit_code or 1
        elif any(h["kind"] == "suspicious" for h in gate["warnings"]):
            print("\n[WARN] Generality Gate 发现疑似环境事实（请人工确认）：")
            for hit in gate["warnings"]:
                if hit["kind"] == "suspicious":
                    print(f"  - {hit['file']}:{hit['line']}: {hit['text'].strip()}")
        else:
            print("\nGenerality Gate：PASS（未发现新增硬编码特征）")
    if exit_code == 0:
        print("\nPASS：无显著回归，可进入下一轮。")
    return exit_code


def main() -> int:
    positional, targets, tolerance, visual_tolerance, run_gate = _parse_args(
        sys.argv[1:]
    )
    if len(positional) == 2:
        tag_a, tag_b = positional[0], positional[1]
        metrics_a, metrics_b = load(tag_a), load(tag_b)
        if not metrics_a or not metrics_b:
            print("missing all_summary.json for one of the tags")
            return 2
        _print_pair_table(tag_a, tag_b, metrics_a, metrics_b)
        return _run_pair_gate(
            tag_a,
            tag_b,
            metrics_a,
            metrics_b,
            targets=targets,
            tolerance=tolerance,
            visual_tolerance=visual_tolerance,
            run_gate=run_gate,
        )

    tags = [t for t in TAGS if (RESULTS / t / "all_summary.json").exists()]
    data = {t: load(t) for t in tags}
    _print_all_table(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())

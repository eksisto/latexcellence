# -*- coding: utf-8 -*-
"""对比两个 tag 的 template_gen 评测结果（启发式分 + 视觉分 + 通用性门禁）。

用法：
    python tools/templategen_opt/compare.py <tagA> <tagB> \
        [--targets k1,k2] [--tolerance 5] [--visual-tolerance 1] [--no-gate]

退出码：
    0  通过（无门禁失败、无显著回退）
    1  Generality Gate 发现新增硬编码特征
    2  对照组回归：有场景显著回退
"""

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
TOOL_ROOT = TOOLS_ROOT / "templategen_opt"
sys.path.insert(0, str(TOOLS_ROOT))

from generality_gate import (  # noqa: E402
    check_hardcoding,
    load_visual_reviews,
    regression_verdict,
)


def load_metrics(tag: str) -> dict[str, Any]:
    path = TOOL_ROOT / "results" / tag / "_all.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def score_of(metrics: dict[str, Any]) -> float:
    return float(metrics.get("quality", {}).get("score", 0))


def _parse_args(
    argv: list[str],
) -> tuple[list[str], set[str], float, float, bool]:
    positional: list[str] = []
    targets: set[str] = set()
    tolerance = 5.0
    visual_tolerance = 1.0
    run_gate = True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--targets" and i + 1 < len(argv):
            targets = {k for k in argv[i + 1].split(",") if k}
            i += 2
            continue
        elif arg == "--tolerance" and i + 1 < len(argv):
            tolerance = float(argv[i + 1])
            i += 2
            continue
        elif arg == "--visual-tolerance" and i + 1 < len(argv):
            visual_tolerance = float(argv[i + 1])
            i += 2
            continue
        elif arg == "--no-gate":
            run_gate = False
            i += 1
            continue
        else:
            positional.append(arg)
            i += 1
    return positional, targets, tolerance, visual_tolerance, run_gate


def _print_table(tag_a: str, tag_b: str, metrics_a: dict, metrics_b: dict) -> None:
    header = f"{'scenario':<14} | {tag_a:<18} | {tag_b:<18}"
    print(header)
    print("-" * len(header))
    all_keys = sorted(set(metrics_a) | set(metrics_b))
    for key in all_keys:
        row = f"{key:<14} | "
        for m in (metrics_a, metrics_b):
            if key in m:
                row += (
                    f"score={m[key].get('quality', {}).get('score', 0):<3} "
                    f"req={m[key].get('req_ratio', 0):.0%} | "
                )
            else:
                row += " " * 18 + " | "
        print(row)


def main() -> None:
    positional, targets, tolerance, visual_tolerance, run_gate = _parse_args(
        sys.argv[1:]
    )
    if len(positional) != 2:
        print(__doc__)
        sys.exit(0)
    tag_a, tag_b = positional[0], positional[1]
    metrics_a, metrics_b = load_metrics(tag_a), load_metrics(tag_b)
    if not metrics_a or not metrics_b:
        print("missing _all.json for one of the tags")
        sys.exit(2)

    _print_table(tag_a, tag_b, metrics_a, metrics_b)

    for t, m in ((tag_a, metrics_a), (tag_b, metrics_b)):
        avg_q = sum(v.get("quality", {}).get("score", 0) for v in m.values()) / max(
            len(m), 1
        )
        vavg = load_visual_reviews(t, TOOL_ROOT)
        avg_v = sum(vavg.values()) / len(vavg) if vavg else None
        print(
            f"{t}: avg heuristic={avg_q:.1f}/100, "
            f"avg visual overall={avg_v if avg_v is None else round(avg_v, 1)}/10"
        )

    exit_code = 0

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
    if verdict["failed"]:
        print("\n[BLOCKED] 对照组回归失败，本次优化作废：")
        for issue in verdict["issues"]:
            print(f"  - {issue}")
        exit_code = exit_code or 2

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
        elif gate["warnings"]:
            suspicious = [h for h in gate["warnings"] if h["kind"] == "suspicious"]
            if suspicious:
                print("\n[WARN] Generality Gate 发现疑似环境事实（请人工确认）：")
                for hit in suspicious:
                    print(f"  - {hit['file']}:{hit['line']}: {hit['text'].strip()}")
        else:
            print("\nGenerality Gate：PASS（未发现新增硬编码特征）")

    if exit_code == 0:
        print("\nPASS：无显著回归，可进入下一轮。")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

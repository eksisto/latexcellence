# -*- coding: utf-8 -*-
"""对比 style_modify Agent 多轮评测结果 + 双 tag 对照组回归判定。

用法：
    python tools/stylemodify_opt/compare.py [tag1 tag2 ...]
    python tools/stylemodify_opt/compare.py <tagA> <tagB> \
        [--targets k1,k2] [--tolerance 5] [--no-gate]

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
TOOL_ROOT = TOOLS_ROOT / "stylemodify_opt"
RESULTS = TOOL_ROOT / "results"
sys.path.insert(0, str(TOOLS_ROOT))

from generality_gate import check_hardcoding, regression_verdict  # noqa: E402


def load(tag: str) -> dict[str, Any]:
    p = RESULTS / tag / "all_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def score_of(metrics: dict[str, Any]) -> float:
    return float(metrics.get("quality", {}).get("score", 0))


def _parse_args(
    argv: list[str],
) -> tuple[list[str], set[str], float, bool]:
    positional: list[str] = []
    targets: set[str] = set()
    tolerance = 5.0
    run_gate = True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--targets" and i + 1 < len(argv):
            targets = {k for k in argv[i + 1].split(",") if k}
            i += 2
            continue
        if arg == "--tolerance" and i + 1 < len(argv):
            tolerance = float(argv[i + 1])
            i += 2
            continue
        if arg == "--no-gate":
            run_gate = False
            i += 1
            continue
        positional.append(arg)
        i += 1
    return positional, targets, tolerance, run_gate


def _print_table(tags: list[str], data: dict[str, dict[str, Any]]) -> None:
    all_scenarios = sorted({k for m in data.values() for k in m})
    header = f"{'场景':<18} " + " ".join(f"{t:>14}" for t in tags)
    print(header)
    print("-" * len(header))
    for sc in all_scenarios:
        row = [sc]
        for t in tags:
            m = data.get(t, {}).get(sc, {})
            if not m:
                row.append(f"{'-':>14}")
                continue
            q = m.get("quality", {})
            row.append(
                f"{q.get('score', '?'):>3}分 "
                f"{m.get('rounds', '?'):>2}轮 "
                f"{m.get('total_s', 0):>5.1f}s"
            )
        print(" ".join(row))

    print("\n=== 汇总（均值） ===")
    for t in tags:
        m = data.get(t, {})
        if not m:
            continue
        scores = [v["quality"]["score"] for v in m.values()]
        rounds = [v.get("rounds") or 0 for v in m.values()]
        times = [v["total_s"] for v in m.values()]
        req_ok = sum(1 for v in m.values() if v["req_ratio"] >= 1.0)
        preserve_ok = sum(1 for v in m.values() if v["preservation_ratio"] >= 1.0)
        compile_ok = sum(1 for v in m.values() if v["compile_success"] is True)
        apply_ok = sum(
            1 for v in m.values() if v.get("apply_test", {}).get("ok") is True
        )
        print(
            f"{t:<10} avg_score={sum(scores)/len(scores):5.1f} "
            f"avg_rounds={sum(rounds)/len(rounds):5.1f} "
            f"sum_time={sum(times):6.1f}s "
            f"req_ok={req_ok}/{len(m)} preserve_ok={preserve_ok}/{len(m)} "
            f"compile_ok={compile_ok}/{len(m)} apply_ok={apply_ok}/{len(m)} "
            f"total_warn={sum(v['warnings'] for v in m.values())} "
            f"total_err={sum(v['errors'] for v in m.values())}"
        )


def _run_pair_gate(
    tag_a: str,
    tag_b: str,
    metrics_a: dict,
    metrics_b: dict,
    *,
    targets: set[str],
    tolerance: float,
    run_gate: bool,
) -> int:
    verdict = regression_verdict(
        metrics_a,
        metrics_b,
        score_of=score_of,
        tolerance=tolerance,
        target_keys=targets,
    )
    print("\n=== 对照组回归 ===")
    for row in verdict["rows"]:
        tag = "target" if row["kind"] == "target" else "control"
        print(
            f"  [{tag}] {row['scenario']}: {row['score_a']:g} → "
            f"{row['score_b']:g} ({row['delta']:+.1f})"
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
    positional, targets, tolerance, run_gate = _parse_args(sys.argv[1:])
    if len(positional) == 2:
        tag_a, tag_b = positional[0], positional[1]
        metrics_a, metrics_b = load(tag_a), load(tag_b)
        if not metrics_a or not metrics_b:
            print("missing all_summary.json for one of the tags")
            return 2
        _print_table([tag_a, tag_b], {tag_a: metrics_a, tag_b: metrics_b})
        return _run_pair_gate(
            tag_a,
            tag_b,
            metrics_a,
            metrics_b,
            targets=targets,
            tolerance=tolerance,
            run_gate=run_gate,
        )

    if positional:
        tags = [t for t in positional if load(t)]
    else:
        tags = sorted(
            p.name
            for p in RESULTS.iterdir()
            if p.is_dir() and (p / "all_summary.json").exists()
        )
    if not tags:
        print("no results found")
        return 0
    _print_table(tags, {t: load(t) for t in tags})
    return 0


if __name__ == "__main__":
    sys.exit(main())

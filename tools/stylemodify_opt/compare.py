# -*- coding: utf-8 -*-
"""对比 style_modify Agent 多轮评测结果。

用法：
    python tools/stylemodify_opt/compare.py [tag1 tag2 ...]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "results"


def load(tag: str) -> dict:
    p = ROOT / tag / "all_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main() -> None:
    if len(sys.argv) > 1:
        tags = [t for t in sys.argv[1:] if load(t)]
    else:
        tags = sorted(
            p.name
            for p in ROOT.iterdir()
            if p.is_dir() and (p / "all_summary.json").exists()
        )
    if not tags:
        print("no results found")
        return

    data = {t: load(t) for t in tags}
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


if __name__ == "__main__":
    main()

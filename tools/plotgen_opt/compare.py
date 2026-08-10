"""汇总对比多轮结果（baseline/opt1..opt11b）并输出表格数据。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "results"
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
]


def load(tag: str) -> dict:
    p = ROOT / tag / "all_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main() -> None:
    tags = [t for t in TAGS if (ROOT / t / "all_summary.json").exists()]
    data = {t: load(t) for t in tags}
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


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""对比两个 tag 的 template_gen 评测结果（启发式分 + 视觉分）。

用法：
    python tools/templategen_opt/compare.py <tagA> <tagB>
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_metrics(tag: str) -> dict:
    path = ROOT / "templategen_opt" / "results" / tag / "_all.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return
    tags = sys.argv[1:3]
    metrics = [load_metrics(t) for t in tags]
    if not all(metrics):
        print("missing _all.json for one of the tags")
        return

    header = f"{'scenario':<14}"
    for t in tags:
        header += f" | {t:<18}"
    print(header)
    print("-" * len(header))
    all_keys = sorted(set(metrics[0]) | set(metrics[1]))
    for key in all_keys:
        row = f"{key:<14}"
        for m in metrics:
            if key in m:
                row += f" | score={m[key].get('quality', {}).get('score', 0):<3} req={m[key].get('req_ratio', 0):.0%}"
            else:
                row += " | " + " " * 18
        print(row)

    print("-" * len(header))
    for t, m in zip(tags, metrics):
        avg_q = sum(v.get("quality", {}).get("score", 0) for v in m.values()) / max(
            len(m), 1
        )
        vfile = ROOT / "templategen_opt" / "results" / t / "visual_reviews.json"
        vavg = None
        if vfile.exists():
            vr = json.loads(vfile.read_text(encoding="utf-8"))
            vavg = sum(r.get("overall", 0) for r in vr.values()) / max(len(vr), 1)
        print(
            f"{t}: avg heuristic={avg_q:.1f}/100, "
            f"avg visual overall={vavg if vavg is None else round(vavg, 1)}/10"
        )


if __name__ == "__main__":
    main()

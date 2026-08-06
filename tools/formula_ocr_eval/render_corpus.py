"""渲染公式语料为评估输入图片（维护者工具）。

用法：
    python tools/formula_ocr_eval/render_corpus.py
    python tools/formula_ocr_eval/render_corpus.py --force
    python tools/formula_ocr_eval/render_corpus.py --only formula_01

输出默认写入 logs/formula_ocr_eval/input/（已被 .gitignore 忽略）。
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# 把 tools/ 加入 sys.path（formula_ocr_eval 是 tools 下的包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from formula_ocr_eval.core import iter_formulas, load_manifest, render_latex_png

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "logs" / "formula_ocr_eval" / "input"


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染公式语料为评估输入图片")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--limit", type=int, default=None, help="只渲染前 N 条")
    parser.add_argument("--only", default=None, help="只渲染指定 id（如 formula_01）")
    parser.add_argument("--force", action="store_true", help="已存在的图片也重新渲染")
    args = parser.parse_args()

    manifest = load_manifest()
    failures: list[str] = []
    for entry, tex in iter_formulas(manifest, args.limit):
        if args.only and entry["id"] != args.only:
            continue
        png = args.output_dir / f"{entry['id']}.png"
        if png.exists() and not args.force:
            print(f"[skip] {entry['id']} 已存在: {png}")
            continue
        try:
            render_latex_png(
                tex,
                entry["inline"],
                png,
                packages=manifest["render"].get("packages"),
                dpi=args.dpi,
                engine=manifest["render"].get("engine", "pdflatex"),
            )
            print(f"[ok] {entry['id']} -> {png}")
        except Exception as e:
            print(f"[fail] {entry['id']}: {e}")
            failures.append(entry["id"])

    if failures:
        print(f"渲染失败 {len(failures)} 条: {failures}")
        return 1
    print("全部完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

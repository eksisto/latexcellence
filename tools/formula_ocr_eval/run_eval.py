"""公式 OCR 人工评估（维护者工具，非 pytest 自动测试）。

流程：
    1. 确保语料输入图片已渲染（缺失时自动调用渲染）
    2. 逐张调用 FormulaOCRService.recognize()（进程内复用后端真实管线，含 LLM 调用）
    3. 将 OCR 返回的 LaTeX 再渲染为 PNG
    4. 生成 HTML 报告（原图 vs 识别结果图 + 原始 LaTeX），供人工观察

用法：
    python tools/formula_ocr_eval/run_eval.py
    python tools/formula_ocr_eval/run_eval.py --limit 5
    python tools/formula_ocr_eval/run_eval.py --only formula_01
    python tools/formula_ocr_eval/run_eval.py --skip-render   # 复用已有输入图片
"""

import argparse
import asyncio
import html
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# 把 tools/ 加入 sys.path（formula_ocr_eval 是 tools 下的包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import get_config, load_config
from backend.modules.formula_ocr.service import FormulaOCRService

from formula_ocr_eval.core import (
    iter_formulas,
    load_manifest,
    render_latex_png,
    strip_ocr_latex,
)

DEFAULT_EVAL_DIR = PROJECT_ROOT / "logs" / "formula_ocr_eval"

_STATUS_LABELS = {
    "ok": ("识别成功", "ok"),
    "empty": ("空结果", "warn"),
    "error": ("失败", "err"),
    "render_error": ("结果渲染失败", "err"),
}


def ensure_config() -> None:
    """确保 config 已加载（脚本不在 FastAPI lifespan 内运行）。"""
    try:
        get_config()
    except RuntimeError:
        config_path = PROJECT_ROOT / "config.json"
        if not config_path.exists():
            raise RuntimeError("未找到 config.json，请先配置 LLM API Key")
        load_config(str(config_path))


async def recognize_one(service: FormulaOCRService, png_path: Path) -> dict:
    data = png_path.read_bytes()
    return await service.recognize(data, "image/png")


def render_report(report_path: Path, results: list[dict]) -> None:
    """生成 HTML 人工比对报告（图片用相对路径引用）。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    inline_mismatch = sum(
        1
        for item in results
        if item["status"] == "ok"
        and item.get("expected_inline") is not None
        and item.get("result_inline") != item.get("expected_inline")
    )

    by_tier: dict[int, list[dict]] = {}
    for item in results:
        by_tier.setdefault(item["tier"], []).append(item)

    sections = []
    for tier in sorted(by_tier):
        items = by_tier[tier]
        tier_name = items[0]["tier_name"]
        cards = "\n".join(_card_html(item) for item in items)
        sections.append(
            f"<section><h2>{html.escape(tier_name)}（{len(items)} 条）</h2>"
            f'<div class="grid">{cards}</div></section>'
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>公式 OCR 人工评估报告</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; background: #f5f6f8; color: #222; }}
  h1 {{ font-size: 22px; }}
  .summary {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  section {{ margin-bottom: 28px; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px; width: 480px; }}
  .card h3 {{ margin: 0 0 8px; font-size: 15px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #fff; }}
  .badge.ok {{ background: #2e9e5b; }}
  .badge.warn {{ background: #d9822b; }}
  .badge.err {{ background: #c0392b; }}
  .pair {{ display: flex; gap: 8px; margin: 8px 0; }}
  .pair figure {{ margin: 0; flex: 1; text-align: center; }}
  .pair img {{ max-width: 100%; border: 1px solid #e0e0e0; background: #fff; }}
  .pair figcaption {{ font-size: 12px; color: #666; margin-top: 4px; }}
  pre {{ background: #f8f9fa; border: 1px solid #eee; border-radius: 4px; padding: 8px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
  .meta {{ font-size: 12px; color: #666; margin: 4px 0; }}
  .empty {{ color: #999; padding: 12px 0; font-size: 13px; }}
</style>
</head>
<body>
<h1>公式 OCR 人工评估报告</h1>
<div class="summary">
  生成时间：{now}<br>
  共 {len(results)} 条：识别成功 {counts.get("ok", 0)}，空结果 {counts.get("empty", 0)}，失败 {counts.get("error", 0)}，结果渲染失败 {counts.get("render_error", 0)}<br>
  inline/display 判定与语料不一致：{inline_mismatch} 条<br>
  <span style="color:#888">说明：左侧为语料原公式渲染图，右侧为 OCR 识别结果渲染图；人工对比两图即可判断识别质量。</span>
</div>
{''.join(sections)}
</body>
</html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(doc, encoding="utf-8")


def _card_html(item: dict) -> str:
    status = item["status"]
    label, cls = _STATUS_LABELS.get(status, ("未知", "err"))
    title = item["title"] or item["id"]
    conf = item.get("confidence")
    conf_text = f"{conf:.2f}" if conf is not None else "-"
    expected = item.get("expected_inline")
    expected_text = (
        f"预期: {'行内' if expected else '展示'}" if expected is not None else ""
    )
    mismatch = (
        item["status"] == "ok"
        and expected is not None
        and item.get("result_inline") != expected
    )
    mismatch_html = (
        '<span style="color:#c0392b"> · inline 判定不一致</span>' if mismatch else ""
    )
    meta = (
        f'<div class="meta">'
        f'{html.escape(item["id"])} · 置信度 {conf_text} · '
        f'行内: {"是" if item.get("result_inline") else "否"} · {expected_text}'
        f"{mismatch_html}</div>"
    )
    latex_text = html.escape(item.get("latex", ""))
    if item.get("error"):
        error_html = f"<span style='color:#c0392b'>{html.escape(item['error'])}</span>"
        if status == "render_error" and latex_text:
            latex_text = f"{error_html}<br>{latex_text}"
        else:
            latex_text = error_html
    if item.get("ocr_image"):
        ocr_img = f'<img src="ocr/{html.escape(item["ocr_image"])}" alt="OCR 结果">'
    elif status == "error":
        ocr_img = '<div class="empty">识别失败，无结果图</div>'
    elif status == "render_error":
        ocr_img = '<div class="empty">结果公式渲染失败，无结果图</div>'
    else:
        ocr_img = '<div class="empty">识别结果为空，无结果图</div>'
    return f"""<div class="card">
<h3>{html.escape(title)} <span class="badge {cls}">{label}</span></h3>
{meta}
<div class="pair">
  <figure><img src="input/{html.escape(item['id'])}.png" alt="原公式"><figcaption>原公式</figcaption></figure>
  <figure>{ocr_img}<figcaption>OCR 结果</figcaption></figure>
</div>
<pre>{latex_text}</pre>
</div>"""


def write_results(output_dir: Path, results: list[dict]) -> None:
    (output_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def main_async(args: argparse.Namespace) -> int:
    ensure_config()
    cfg = get_config()
    if not cfg.llm.api_key:
        print("警告：config.json 未配置 llm.api_key，OCR 将返回空结果。")

    manifest = load_manifest()
    input_dir = args.input_dir
    ocr_dir = args.output_dir / "ocr"
    input_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)

    service = FormulaOCRService()
    results: list[dict] = []
    for entry, tex in iter_formulas(manifest, args.limit):
        if args.only and entry["id"] != args.only:
            continue
        input_png = input_dir / f"{entry['id']}.png"
        if not input_png.exists() and not args.skip_render:
            render_latex_png(
                tex,
                entry["inline"],
                input_png,
                packages=manifest["render"].get("packages"),
                dpi=args.dpi,
            )
        if not input_png.exists():
            print(f"[skip] {entry['id']}: 输入图片不存在，请先运行 render_corpus.py")
            continue

        item = {
            "id": entry["id"],
            "tier": entry["tier"],
            "tier_name": entry["tier_name"],
            "title": entry["title"],
            "expected_latex": tex,
            "expected_inline": entry["inline"],
            "status": "error",
        }
        print(f"[ocr] {entry['id']} ...", flush=True)
        try:
            result = await recognize_one(service, input_png)
            item.update(
                {
                    "status": "ok",
                    "latex": result.get("latex", ""),
                    "confidence": result.get("confidence", 0.0),
                    "result_inline": result.get("inline", False),
                    "result_inline_source": result.get("inline_source", "model"),
                    "low_confidence": result.get("low_confidence", False),
                }
            )
            ocr_latex = strip_ocr_latex(item["latex"])
            if ocr_latex:
                ocr_png = ocr_dir / f"{entry['id']}.png"
                try:
                    render_latex_png(
                        ocr_latex,
                        item["result_inline"],
                        ocr_png,
                        packages=manifest["render"].get("packages"),
                        dpi=args.dpi,
                        engine=manifest["render"].get("engine", "pdflatex"),
                    )
                    item["ocr_image"] = ocr_png.name
                except Exception as e:
                    item["status"] = "render_error"
                    item["error"] = f"结果公式渲染失败：{e}"
            else:
                item["status"] = "empty"
            if item.get("error"):
                print(f"  -> {item['status']}: {item['error']}")
            else:
                print(f"  -> {item['status']}")
        except Exception as e:
            item["status"] = "error"
            item["error"] = str(e)
            print(f"  -> error: {e}")
        results.append(item)

    write_results(args.output_dir, results)
    report_path = args.output_dir / "report.html"
    render_report(report_path, results)
    print(f"\n报告已生成: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="公式 OCR 人工评估")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_EVAL_DIR / "input")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_EVAL_DIR)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条")
    parser.add_argument("--only", default=None, help="只评估指定 id（如 formula_01）")
    parser.add_argument(
        "--skip-render", action="store_true", help="复用已有输入图片，不自动渲染"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except RuntimeError as e:
        print(f"错误：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

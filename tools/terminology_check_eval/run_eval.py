"""术语一致性人工评估（维护者工具，非 pytest 自动测试）。

流程：
    1. 在 tests/fixtures/terminology_check 语料目录上打开工作区
    2. 逐条调用 TerminologyCheckService.check_terminology()（进程内复用后端
       真实管线；每条 = N 次术语提取 + 1 次语义聚类，含 LLM 调用）
    3. 把每段修正文本中的 marker 还原为 LaTeX
    4. 生成 HTML 报告（原文 vs 修正后 + 修改明细 + 期望术语簇命中标记），
       供人工观察

用法：
    python tools/terminology_check_eval/run_eval.py
    python tools/terminology_check_eval/run_eval.py --limit 5
    python tools/terminology_check_eval/run_eval.py --only term_01
    python tools/terminology_check_eval/run_eval.py --validate   # 只校验语料，不调 LLM
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
# 把 tools/ 加入 sys.path（terminology_check_eval 是 tools 下的包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import get_config, load_config
from backend.modules.terminology_check.service import TerminologyCheckService

from terminology_check_eval.core import (
    analyze_hits,
    iter_items,
    load_manifest,
    normalize_fragment,
    open_eval_workspace,
    restore_markers,
    validate_corpus,
)

DEFAULT_EVAL_DIR = PROJECT_ROOT / "logs" / "terminology_check_eval"
DEFAULT_WORKSPACE_ID = "terminology_check_eval"

_STATUS_LABELS = {
    "issues": ("发现不一致", "ok"),
    "clean": ("未发现不一致", "warn"),
    "error": ("失败", "err"),
}

_CLUSTER_STATUS_ICON = {"hit": "✓", "partial": "◐", "missed": "✗", "n/a": "–"}
_CLUSTER_STATUS_COLOR = {
    "hit": "#2e9e5b",
    "partial": "#d9822b",
    "missed": "#c0392b",
    "n/a": "#888888",
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


async def check_one(
    service: TerminologyCheckService, file_path: str, workspace_id: str
) -> dict:
    """调用一次术语检查，返回完整结果（含全部段落）。"""
    return await service.check_terminology(file_path, workspace_id=workspace_id)


def build_item(entry: dict, tex: str, result: dict, error: str | None = None) -> dict:
    """把 service 返回的多段落结果整理为报告条目。"""
    item = {
        "id": entry["id"],
        "tier": entry["tier"],
        "tier_name": entry["tier_name"],
        "title": entry["title"],
        "has_inconsistency": entry["has_inconsistency"],
        "expected_clusters": entry.get("expected_clusters", []),
        "expected_latex": tex,
        "paragraphs": [],
        "changes": [],
        "unlisted_changes": False,
        "status": "error",
        "error": error,
        "matched": [],
        "missed": [],
        "false_positive_changes": [],
        "cluster_stats": [],
    }
    if error is not None:
        return item

    paragraphs: list[dict] = []
    changes: list[dict] = []
    for para in result.get("results", []):
        para_changes = list(para.get("changes", []) or [])
        for change in para_changes:
            flattened = dict(change)
            flattened["paragraph_index"] = para["paragraph_index"]
            changes.append(flattened)
        original_tex = para.get("text", para.get("original", ""))
        corrected_marker = para.get("corrected", para.get("plain_text", ""))
        try:
            corrected_latex = restore_markers(original_tex, corrected_marker)
        except Exception:
            corrected_latex = original_tex
        paragraphs.append(
            {
                "paragraph_index": para["paragraph_index"],
                "original": original_tex,
                "corrected_latex": corrected_latex,
                "changes": para_changes,
                "terms": list(para.get("terms", []) or []),
                "plain_text": para.get("plain_text", ""),
            }
        )

    text_changed = any(p["corrected_latex"] != p["original"] for p in paragraphs)
    item.update(
        {
            "paragraphs": paragraphs,
            "changes": changes,
            "unlisted_changes": bool(changes or text_changed) and not changes,
        }
    )
    item["status"] = "issues" if (changes or text_changed) else "clean"
    item.update(analyze_hits(item["expected_clusters"], changes))
    return item


def render_report(report_path: Path, results: list[dict]) -> None:
    """生成 HTML 人工评估报告（不依赖外部图片）。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    expected_inconsistent = sum(1 for item in results if item["has_inconsistency"])
    detected = sum(
        1
        for item in results
        if item["status"] == "issues" and item["has_inconsistency"]
    )
    fp_items = sum(
        1
        for item in results
        if item["status"] == "issues" and not item["has_inconsistency"]
    )
    fp_changes = sum(len(item["false_positive_changes"]) for item in results)
    clean_expected = len(results) - expected_inconsistent
    total_changes = sum(len(item["changes"]) for item in results)

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
<title>术语一致性人工评估报告</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; background: #f5f6f8; color: #222; }}
  h1 {{ font-size: 22px; }}
  .summary {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  section {{ margin-bottom: 28px; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px; width: 700px; }}
  .card h3 {{ margin: 0 0 8px; font-size: 15px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #fff; }}
  .badge.ok {{ background: #2e9e5b; }}
  .badge.warn {{ background: #d9822b; }}
  .badge.err {{ background: #c0392b; }}
  .badge.clean {{ background: #5b7fa6; }}
  .meta {{ font-size: 12px; color: #666; margin: 4px 0; }}
  .issues, .changes, .paragraphs {{ margin: 8px 0; font-size: 13px; }}
  .issues ul {{ margin: 4px 0; padding-left: 18px; }}
  .changes table {{ border-collapse: collapse; width: 100%; margin-top: 4px; }}
  .changes th, .changes td {{ border: 1px solid #e0e0e0; padding: 4px 6px; font-size: 12px; text-align: left; vertical-align: top; }}
  .changes th {{ background: #f4f5f7; }}
  del {{ color: #c0392b; }}
  ins {{ color: #2e9e5b; text-decoration: none; }}
  pre {{ background: #f8f9fa; border: 1px solid #eee; border-radius: 4px; padding: 8px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
  .hint {{ font-size: 12px; color: #d9822b; margin: 4px 0; }}
  .terms {{ font-size: 12px; color: #5b7fa6; margin: 4px 0; }}
  .empty {{ color: #999; padding: 8px 0; font-size: 13px; }}
  .paragraphs b {{ display: block; margin-top: 6px; }}
</style>
</head>
<body>
<h1>术语一致性人工评估报告</h1>
<div class="summary">
  生成时间：{now}<br>
  共 {len(results)} 条：发现不一致 {counts.get("issues", 0)}，未发现不一致 {counts.get("clean", 0)}，失败 {counts.get("error", 0)}；检查出的修改条目共 {total_changes} 个<br>
  期望含不一致语料 {expected_inconsistent} 条：发现不一致 {detected} 条，漏检 {expected_inconsistent - detected} 条；
  干净语料 {clean_expected} 条：误报 {fp_items} 条（误报修改条目 {fp_changes} 个）<br>
  <span style="color:#888">说明：每张卡片上方为期望术语簇（推荐项与各变体的 ✓ 命中 / ✗ 漏检标记）与检查出的修改明细（[误报] 为语料中未标注的修改）；下方逐段展示原文与修正后 LaTeX 对照。命中标记是宽松子串匹配，仅供人工观察辅助，最终结论以人工判断为准。</span>
</div>
{''.join(sections)}
</body>
</html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(doc, encoding="utf-8")


def _variant_span(variant: str, cluster_stat: dict, recommended: str) -> str:
    """单变体的展示：推荐项 / ✓ 命中 / ✗ 漏检。"""
    esc = html.escape

    if recommended and normalize_fragment(variant) == normalize_fragment(recommended):
        return f'<span style="color:#2e9e5b"><b>{esc(variant)}</b>（推荐）</span>'
    if variant in cluster_stat.get("matched_variants", []):
        return f'<span style="color:#2e9e5b">✓ {esc(variant)}</span>'
    return f'<span style="color:#c0392b">✗ {esc(variant)}</span>'


def _card_html(item: dict) -> str:
    status = item["status"]
    label, cls = _STATUS_LABELS.get(status, ("未知", "err"))
    title = item["title"] or item["id"]
    expected_badge = "含不一致" if item["has_inconsistency"] else "干净"
    expected_cls = "ok" if item["has_inconsistency"] else "clean"

    total_expected = len(item["matched"]) + len(item["missed"])
    meta = (
        f'<div class="meta">{html.escape(item["id"])} · '
        f"期望变体替换 {total_expected} 命中 {len(item['matched'])} "
        f"漏检 {len(item['missed'])}"
    )
    if item["false_positive_changes"]:
        meta += f" · 误报 {len(item['false_positive_changes'])} 条"
    meta += "</div>"

    hint = ""
    if status == "clean" and item["has_inconsistency"]:
        hint = (
            '<div class="hint">未发现期望术语不一致：可能漏检，'
            "也可能是 LLM 调用失败被服务层静默回退（无 API Key、提取或聚类失败时"
            "返回空 changes）</div>"
        )

    expected_html = ""
    if item["expected_clusters"]:
        rows = []
        for ci, cluster in enumerate(item["expected_clusters"]):
            stat = item["cluster_stats"][ci] if ci < len(item["cluster_stats"]) else {}
            icon = _CLUSTER_STATUS_ICON.get(stat.get("status"), "?")
            color = _CLUSTER_STATUS_COLOR.get(stat.get("status"), "#888888")
            variants_html = " / ".join(
                _variant_span(v, stat, cluster.get("recommended", ""))
                for v in cluster.get("variants", [])
            )
            rows.append(
                f'<li><span style="color:{color}">{icon}</span> '
                f"推荐 <ins>{html.escape(cluster.get('recommended', ''))}</ins> ⇐ "
                f"{variants_html}</li>"
            )
        expected_html = (
            '<div class="issues"><b>期望术语簇</b><ul>' + "".join(rows) + "</ul></div>"
        )

    changes_html = ""
    if item["changes"]:
        rows = []
        for idx, change in enumerate(item["changes"]):
            fp = idx in item["false_positive_changes"]
            fp_html = ' <span style="color:#c0392b">[误报]</span>' if fp else ""
            rows.append(
                "<tr>"
                f"<td>{idx + 1}{fp_html}</td>"
                f"<td>{html.escape(str(change.get('paragraph_index', '')))}</td>"
                f"<td>{html.escape(change.get('type', ''))}</td>"
                f"<td><del>{html.escape(change.get('original', ''))}</del></td>"
                f"<td><ins>{html.escape(change.get('corrected', ''))}</ins></td>"
                f"<td>{html.escape(change.get('explanation', ''))}</td>"
                "</tr>"
            )
        changes_html = (
            '<div class="changes"><b>检测到的修改</b>'
            "<table><thead><tr><th>#</th><th>段落</th><th>类型</th><th>原文</th>"
            "<th>修正</th><th>说明</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    elif status == "issues":
        changes_html = (
            '<div class="changes"><b>检测到的修改</b>'
            '<p class="empty">文本有变化但未列出修改明细（unlisted_changes）</p></div>'
        )

    error_html = (
        f'<span style="color:#c0392b">{html.escape(item["error"])}</span>'
        if item.get("error")
        else ""
    )

    paragraphs_html = ""
    for p in item["paragraphs"]:
        idx = p["paragraph_index"]
        paragraphs_html += (
            f"<b>段落 P{idx} 原文</b>" f"<pre>{html.escape(p['original'])}</pre>"
        )
        if p["corrected_latex"] != p["original"]:
            paragraphs_html += (
                f"<b>段落 P{idx} 修正后</b>"
                f"<pre>{html.escape(p['corrected_latex'])}</pre>"
            )
        if p.get("terms"):
            paragraphs_html += (
                '<div class="terms">段落术语：'
                f'{html.escape("、".join(p["terms"]))}</div>'
            )
    paragraphs_block = (
        f'<div class="paragraphs"><b>段落对照</b>{paragraphs_html}</div>'
        if paragraphs_html
        else ""
    )

    return f"""<div class="card">
<h3>{html.escape(title)} <span class="badge {cls}">{label}</span> <span class="badge {expected_cls}">{expected_badge}</span></h3>
{meta}
{hint}
{expected_html}
{changes_html}
{error_html}
{paragraphs_block}
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
        print(
            "警告：config.json 未配置 llm.api_key，术语检查将走规则降级"
            "（仅提取术语，不产生修改建议）。"
        )

    manifest = load_manifest()
    corpus_issues = validate_corpus(manifest)
    for issue in corpus_issues:
        print(f"[corpus] {issue}")
    if corpus_issues:
        print(f"语料校验发现 {len(corpus_issues)} 个问题，请先修复后再运行评估。")
        return 1
    if args.validate:
        print("语料校验通过。")
        return 0

    open_eval_workspace(args.workspace_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    service = TerminologyCheckService()
    results: list[dict] = []
    for entry, tex in iter_items(manifest, args.limit):
        if args.only and entry["id"] != args.only:
            continue
        print(f"[check] {entry['id']} ...", flush=True)
        result: dict = {}
        error: str | None = None
        try:
            result = await check_one(service, entry["file"], args.workspace_id)
        except Exception as e:
            error = str(e)
        item = build_item(entry, tex, result, error)
        detail = f": {error}" if error else ""
        print(f"  -> {item['status']}（{len(item['changes'])} 条修改）{detail}")
        results.append(item)

    write_results(args.output_dir, results)
    report_path = args.output_dir / "report.html"
    render_report(report_path, results)
    print(f"\n报告已生成: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="术语一致性人工评估")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_EVAL_DIR)
    parser.add_argument(
        "--workspace-id", default=DEFAULT_WORKSPACE_ID, help="评估工作区 ID"
    )
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条")
    parser.add_argument("--only", default=None, help="只评估指定 id（如 term_01）")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="只校验语料（多段落 / 变体可见性 / 簇契约），不调用 LLM",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except RuntimeError as e:
        print(f"错误：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""逐段翻译人工评估（维护者工具，非 pytest 自动测试）。

流程：
    1. 在 tests/fixtures/paragraph_translate 语料目录上打开工作区
    2. 逐条调用 ParagraphTranslateService.translate()（进程内复用后端真实管线，
       含 LLM 调用与 marker 保护；目标语言取自语料条目）
    3. 还原 marker（回退路径的 translated 为 marker 空间文本，成功路径幂等）
    4. 生成 HTML 报告（参考译文 / 原文 / 译文对照 + 修改明细 + 期望短语
       命中标记），供人工观察

CLI 骨架（语料加载 / 工作区 / 主循环 / 结果落盘 / --validate）复用
tools/eval_common.py。

用法：
    python tools/paragraph_translate_eval/run_eval.py
    python tools/paragraph_translate_eval/run_eval.py --limit 5
    python tools/paragraph_translate_eval/run_eval.py --only trans_01
    python tools/paragraph_translate_eval/run_eval.py --validate   # 只校验语料，不调 LLM
"""

import html
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# 把 tools/ 加入 sys.path（paragraph_translate_eval 是 tools 下的包，eval_common 也在 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.modules.paragraph_translate.service import ParagraphTranslateService

from eval_common import REPORT_CSS, run_eval_cli
from paragraph_translate_eval.core import (
    FIXTURES_DIR,
    analyze_hits,
    restore_markers,
    validate_corpus,
)

DEFAULT_EVAL_DIR = PROJECT_ROOT / "logs" / "paragraph_translate_eval"
DEFAULT_WORKSPACE_ID = "paragraph_translate_eval"

_STATUS_LABELS = {
    "translated": ("已翻译", "ok"),
    "unchanged": ("译文与原文相同", "warn"),
    "error": ("失败", "err"),
}


async def check_one(
    service: ParagraphTranslateService, entry: dict, workspace_id: str
) -> dict:
    """调用一次逐段翻译，取第一个段落的翻译结果（语料每条恰好一个段落）。"""
    result = await service.translate(
        entry["file"],
        workspace_id=workspace_id,
        target_language=entry["target_language"],
    )
    return result["results"][0]


def build_item(entry: dict, tex: str, para: dict, error: str | None = None) -> dict:
    """把 service 返回的单段落结果整理为报告条目。"""
    item = {
        "id": entry["id"],
        "tier": entry["tier"],
        "tier_name": entry["tier_name"],
        "title": entry["title"],
        "source_language": entry["source_language"],
        "target_language": entry["target_language"],
        "has_translation": entry["has_translation"],
        "expected_phrases": entry.get("expected_phrases", []),
        "expected_translation": entry.get("expected_translation", ""),
        "expected_latex": tex,
        "translated_latex": tex,
        "changes": [],
        "unlisted_changes": False,
        "status": "error",
        "error": error,
        "matched": [],
        "missed": [],
        "unmatched_changes": [],
    }
    if error is not None:
        return item

    original_latex = para.get("original", tex)
    translated = para.get("translated", para.get("plain_text", ""))
    try:
        # 成功路径的 translated 已是还原后的干净 LaTeX（restore 幂等）；
        # 回退路径为 marker 空间文本，统一还原供报告展示
        translated_latex = restore_markers(original_latex, translated)
    except Exception:
        translated_latex = original_latex
    changes = list(para.get("changes", []) or [])
    item.update(
        {
            "translated_latex": translated_latex,
            "changes": changes,
            "unlisted_changes": False,
        }
    )
    item["status"] = "unchanged" if translated_latex == original_latex else "translated"
    item.update(analyze_hits(item["expected_phrases"], changes, translated_latex))
    return item


def render_report(report_path: Path, results: list[dict]) -> None:
    """生成 HTML 人工评估报告（不依赖外部图片）。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    expected_translate = sum(1 for item in results if item["has_translation"])
    produced = sum(
        1
        for item in results
        if item["status"] == "translated" and item["has_translation"]
    )
    redundant = sum(
        1
        for item in results
        if item["status"] == "translated" and not item["has_translation"]
    )
    same_language = len(results) - expected_translate
    total_phrases = sum(len(item["matched"]) + len(item["missed"]) for item in results)
    matched_phrases = sum(len(item["matched"]) for item in results)
    unmatched_changes = sum(len(item["unmatched_changes"]) for item in results)

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
<title>逐段翻译人工评估报告</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<h1>逐段翻译人工评估报告</h1>
<div class="summary">
  生成时间：{now}<br>
  共 {len(results)} 条：已翻译 {counts.get("translated", 0)}，译文与原文相同 {counts.get("unchanged", 0)}，失败 {counts.get("error", 0)}<br>
  期望含翻译语料 {expected_translate} 条：产生译文 {produced} 条，未翻译 {expected_translate - produced} 条；
  已同语种语料 {same_language} 条：多余改动 {redundant} 条<br>
  期望短语共 {total_phrases} 个：命中 {matched_phrases} 个，漏检 {total_phrases - matched_phrases} 个；
  未被期望短语覆盖的修改条目 {unmatched_changes} 个（参考信息，非误报）<br>
  <span style="color:#888">说明：每张卡片上方为期望短语（✓ 命中 / ✗ 漏检）与修改明细（[未覆盖] 为未被期望短语标注的 change）；下方依次为参考译文、原文与模型译文对照。短语命中是宽松匹配，仅供人工观察辅助，最终结论以人工判断为准。</span>
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
    expected_badge = "含翻译" if item["has_translation"] else "已同语种"
    expected_cls = "ok" if item["has_translation"] else "clean"

    total_expected = len(item["matched"]) + len(item["missed"])
    meta = (
        f'<div class="meta">{html.escape(item["id"])} · '
        f"期望短语 {total_expected} 命中 {len(item['matched'])} 漏检 {len(item['missed'])}"
    )
    if item["unmatched_changes"]:
        meta += f" · 未覆盖修改 {len(item['unmatched_changes'])} 条"
    meta += "</div>"

    hint = ""
    if status == "unchanged" and item["has_translation"]:
        hint = (
            '<div class="hint">译文与原文相同：可能未翻译，'
            "也可能是 LLM 调用失败被服务层静默回退为原文</div>"
        )
    if status == "translated" and not item["has_translation"]:
        hint = (
            '<div class="hint">原文已在目标语言，但模型仍修改了文本'
            "（多余改动）</div>"
        )

    phrases_html = ""
    if item["expected_phrases"]:
        rows = []
        for i, phrase in enumerate(item["expected_phrases"]):
            hit = i in item["matched"]
            mark = "✓" if hit else "✗"
            color = "#2e9e5b" if hit else "#c0392b"
            rows.append(
                f'<li><span style="color:{color}">{mark}</span> '
                f"<del>{html.escape(phrase.get('original', ''))}</del> → "
                f"<ins>{html.escape(phrase.get('translated', ''))}</ins></li>"
            )
        phrases_html = (
            '<div class="issues"><b>期望短语</b><ul>' + "".join(rows) + "</ul></div>"
        )

    changes_html = ""
    if item["changes"]:
        rows = []
        for idx, change in enumerate(item["changes"]):
            uncovered = idx in item["unmatched_changes"]
            tag = ' <span style="color:#888">[未覆盖]</span>' if uncovered else ""
            rows.append(
                "<tr>"
                f"<td>{idx + 1}{tag}</td>"
                f"<td>{html.escape(change.get('type', ''))}</td>"
                f"<td><del>{html.escape(change.get('original', ''))}</del></td>"
                f"<td><ins>{html.escape(change.get('translated', ''))}</ins></td>"
                f"<td>{html.escape(change.get('explanation', ''))}</td>"
                "</tr>"
            )
        changes_html = (
            '<div class="changes"><b>检测到的翻译修改</b>'
            "<table><thead><tr><th>#</th><th>类型</th><th>原文</th>"
            "<th>译文</th><th>说明</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )

    error_html = (
        f'<span style="color:#c0392b">{html.escape(item["error"])}</span>'
        if item.get("error")
        else ""
    )

    reference_html = ""
    if item["expected_translation"]:
        reference_html = (
            "<b>参考译文</b><pre>"
            + html.escape(item["expected_translation"])
            + "</pre>"
        )

    translated_html = ""
    if item["translated_latex"] != item["expected_latex"]:
        translated_html = (
            "<b>模型译文</b><pre>" + html.escape(item["translated_latex"]) + "</pre>"
        )
    elif status == "unchanged":
        translated_html = '<div class="empty">译文与原文相同，未单独展示</div>'

    return f"""<div class="card">
<h3>{html.escape(title)} <span class="badge {cls}">{label}</span> <span class="badge {expected_cls}">{expected_badge}</span> <span style="font-size:12px;color:#666">{html.escape(item['source_language'])} → {html.escape(item['target_language'])}</span></h3>
{meta}
{hint}
{phrases_html}
{changes_html}
{error_html}
{reference_html}
<b>原文</b><pre>{html.escape(item["expected_latex"])}</pre>
{translated_html}
</div>"""


def main() -> int:
    return run_eval_cli(
        fixtures_dir=FIXTURES_DIR,
        default_eval_dir=DEFAULT_EVAL_DIR,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        description="逐段翻译人工评估",
        no_key_warning=(
            "警告：config.json 未配置 llm.api_key，翻译将返回原文"
            "（全部记为译文与原文相同）。"
        ),
        check_label="translate",
        service_factory=ParagraphTranslateService,
        check_one=check_one,
        build_item=build_item,
        render_report=render_report,
        validate_corpus=validate_corpus,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""错字查找人工评估（维护者工具，非 pytest 自动测试）。

流程：
    1. 在 tests/fixtures/typo_check 语料目录上打开工作区
    2. 逐条调用 TypoCheckService.check_typos()（进程内复用后端真实管线，含 LLM 调用）
    3. 把修正文本中的 marker 还原为 LaTeX
    4. 生成 HTML 报告（原文 vs 修正后 + 修改明细 + 期望错字命中标记），供人工观察

CLI 骨架（语料加载 / 工作区 / 主循环 / 结果落盘）复用 tools/eval_common.py。

用法：
    python tools/typo_check_eval/run_eval.py
    python tools/typo_check_eval/run_eval.py --limit 5
    python tools/typo_check_eval/run_eval.py --only typo_01
"""

import html
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# 把 tools/ 加入 sys.path（typo_check_eval 是 tools 下的包，eval_common 也在 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.modules.typo_check.service import TypoCheckService

from eval_common import REPORT_CSS, run_eval_cli
from typo_check_eval.core import FIXTURES_DIR, analyze_hits, restore_markers

DEFAULT_EVAL_DIR = PROJECT_ROOT / "logs" / "typo_check_eval"
DEFAULT_WORKSPACE_ID = "typo_check_eval"

_STATUS_LABELS = {
    "issues": ("发现问题", "ok"),
    "clean": ("未发现问题", "warn"),
    "error": ("失败", "err"),
}


async def check_one(service: TypoCheckService, entry: dict, workspace_id: str) -> dict:
    """调用一次逐段检查，取第一个段落的检查结果（语料每条恰好一个段落）。"""
    result = await service.check_typos(entry["file"], workspace_id=workspace_id)
    return result["results"][0]


def build_item(entry: dict, tex: str, para: dict, error: str | None = None) -> dict:
    """把 service 返回的单段落结果整理为报告条目。"""
    item = {
        "id": entry["id"],
        "tier": entry["tier"],
        "tier_name": entry["tier_name"],
        "title": entry["title"],
        "has_typo": entry["has_typo"],
        "expected_issues": entry.get("expected_issues", []),
        "expected_latex": tex,
        "plain_text": para.get("plain_text", ""),
        "corrected_latex": tex,
        "changes": [],
        "unlisted_changes": False,
        "status": "error",
        "error": error,
        "matched": [],
        "missed": [],
        "false_positive_changes": [],
    }
    if error is not None:
        return item

    changes = list(para.get("changes", []) or [])
    corrected_marker = para.get("corrected", para.get("plain_text", ""))
    try:
        corrected_latex = restore_markers(tex, corrected_marker)
    except Exception:
        corrected_latex = tex
    item.update(
        {
            "plain_text": para.get("plain_text", ""),
            "corrected_latex": corrected_latex,
            "changes": changes,
            "unlisted_changes": bool(para.get("unlisted_changes", False)),
        }
    )
    text_changed = corrected_latex != tex
    item["status"] = "issues" if (changes or text_changed) else "clean"
    item.update(analyze_hits(item["expected_issues"], changes))
    return item


def render_report(report_path: Path, results: list[dict]) -> None:
    """生成 HTML 人工评估报告（不依赖外部图片）。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    expected_typo = sum(1 for item in results if item["has_typo"])
    detected = sum(
        1 for item in results if item["status"] == "issues" and item["has_typo"]
    )
    fp_items = sum(
        1 for item in results if item["status"] == "issues" and not item["has_typo"]
    )
    fp_changes = sum(len(item["false_positive_changes"]) for item in results)
    clean_expected = len(results) - expected_typo

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
<title>错字查找人工评估报告</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<h1>错字查找人工评估报告</h1>
<div class="summary">
  生成时间：{now}<br>
  共 {len(results)} 条：发现问题 {counts.get("issues", 0)}，未发现问题 {counts.get("clean", 0)}，失败 {counts.get("error", 0)}<br>
  期望含错字语料 {expected_typo} 条：发现错字 {detected} 条，漏检 {expected_typo - detected} 条；
  干净语料 {clean_expected} 条：误报 {fp_items} 条（误报修改条目 {fp_changes} 个）<br>
  <span style="color:#888">说明：上方为检查出的修改明细与期望错字命中标记（✓ 命中 / ✗ 漏检 / [误报] 为语料中未标注的修改）；下方为原文与修正后 LaTeX 对照。命中标记是宽松子串匹配，仅供人工观察辅助，最终结论以人工判断为准。</span>
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
    expected_badge = "含错字" if item["has_typo"] else "干净"
    expected_cls = "ok" if item["has_typo"] else "clean"

    total_expected = len(item["expected_issues"])
    meta = (
        f'<div class="meta">{html.escape(item["id"])} · '
        f"期望 {total_expected} 命中 {len(item['matched'])} 漏检 {len(item['missed'])}"
    )
    if item["false_positive_changes"]:
        meta += f" · 误报 {len(item['false_positive_changes'])} 条"
    meta += "</div>"

    hint = ""
    if status == "clean" and item["has_typo"]:
        hint = (
            '<div class="hint">未发现期望错字：可能漏检，'
            "也可能是 LLM 调用失败被服务层静默回退为原文</div>"
        )

    expected_html = ""
    if total_expected:
        rows = []
        for i, issue in enumerate(item["expected_issues"]):
            hit = i in item["matched"]
            mark = "✓" if hit else "✗"
            color = "#2e9e5b" if hit else "#c0392b"
            rows.append(
                f'<li><span style="color:{color}">{mark}</span> '
                f"<del>{html.escape(issue.get('original', ''))}</del> → "
                f"<ins>{html.escape(issue.get('corrected', ''))}</ins></li>"
            )
        expected_html = (
            '<div class="issues"><b>期望错字</b><ul>' + "".join(rows) + "</ul></div>"
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
                f"<td>{html.escape(change.get('type', ''))}</td>"
                f"<td><del>{html.escape(change.get('original', ''))}</del></td>"
                f"<td><ins>{html.escape(change.get('corrected', ''))}</ins></td>"
                f"<td>{html.escape(change.get('explanation', ''))}</td>"
                "</tr>"
            )
        changes_html = (
            '<div class="changes"><b>检测到的修改</b>'
            "<table><thead><tr><th>#</th><th>类型</th><th>原文</th>"
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
    pre_html = ""
    if item["corrected_latex"] != item["expected_latex"]:
        pre_html = (
            "<b>修正后</b><pre>" + html.escape(item["corrected_latex"]) + "</pre>"
        )
    return f"""<div class="card">
<h3>{html.escape(title)} <span class="badge {cls}">{label}</span> <span class="badge {expected_cls}">{expected_badge}</span></h3>
{meta}
{hint}
{expected_html}
{changes_html}
{error_html}
<b>原文</b><pre>{html.escape(item["expected_latex"])}</pre>
{pre_html}
</div>"""


def main() -> int:
    return run_eval_cli(
        fixtures_dir=FIXTURES_DIR,
        default_eval_dir=DEFAULT_EVAL_DIR,
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        description="错字查找人工评估",
        no_key_warning=(
            "警告：config.json 未配置 llm.api_key，检查将返回原文（全部记为未发现问题）。"
        ),
        check_label="check",
        service_factory=TypoCheckService,
        check_one=check_one,
        build_item=build_item,
        render_report=render_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())

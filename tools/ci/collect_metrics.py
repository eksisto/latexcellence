"""统一评测指标采集：把各套评测的原始产物归一化为 metrics.json，供 CI 回归比对。

支持的 suite（--suite）与输入格式（--results）：
    语料式评测 results.json（条目含 matched / missed / false_positive_changes）：
        typo_check / expression_opt / terminology_check / paragraph_translate
    OCR 评测 results.json（expected_latex 即语料 .tex，无标注条目）：
        formula_ocr / table_ocr（补算 normalized_match_rate 归一化匹配率）
    prompt_comprehension 的 results.json（summary + cases）：
        prompt_comprehension
    web_access_eval 离线重放报告（run_eval.py replay 的输出）：
        web_search / web_scholarly / web_extraction / web_extraction_structured
        / web_grounding

输出 schema：
    {
      "suite": str,
      "collected_at": ISO 时间,
      "metrics": {指标名: 数值},      # 参与回归判定（方向见 metric_rules.DIRECTIONS）
      "info": {补充信息},             # 不参与判定（延迟 / 条目数 / 模型名等）
      "cases": {用例名: 状态},        # 仅 prompt_comprehension，逐用例状态比对
    }

用法：
    python tools/ci/collect_metrics.py --suite typo_check \
        --results logs/ci/typo_check/results.json --output logs/ci/metrics/typo_check.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ci.metric_rules import DIRECTIONS  # noqa: E402

SUPPORTED_SUITES = (
    "typo_check",
    "expression_opt",
    "terminology_check",
    "paragraph_translate",
    "formula_ocr",
    "table_ocr",
    "prompt_comprehension",
    "web_search",
    "web_scholarly",
    "web_extraction",
    "web_extraction_structured",
    "web_grounding",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _round(value: float) -> float:
    return round(value, 6)


# ── 语料式（typo / expression / terminology / translate 共用小工具）────────


def _has_flag(item: dict, flag: str) -> bool:
    return bool(item.get(flag))


def _is_detected(item: dict) -> bool:
    return item.get("status") == "issues"


def collect_typo_like(items: list[dict], flag: str) -> tuple[dict, dict]:
    """错字查找 / 表达优化共用：期望条目标记 + analyze_hits 索引。"""
    expected = sum(1 for it in items if _has_flag(it, flag))
    detected = sum(1 for it in items if _has_flag(it, flag) and _is_detected(it))
    metrics = {
        "errors": sum(1 for it in items if it.get("status") == "error"),
        "missed_items": expected - detected,
        "missed_expected": sum(
            len(it.get("missed", [])) for it in items if _has_flag(it, flag)
        ),
        "false_positive_items": sum(
            1 for it in items if _is_detected(it) and not _has_flag(it, flag)
        ),
        "false_positive_changes": sum(
            len(it.get("false_positive_changes", [])) for it in items
        ),
        "detection_rate": _round(detected / expected) if expected else 1.0,
    }
    info = {"items_total": len(items), "expected_items": expected}
    return metrics, info


def collect_terminology(items: list[dict]) -> tuple[dict, dict]:
    """术语一致性：条目级检测统计 + 簇级命中统计（cluster_stats）。"""
    statuses = [
        cs.get("status") for it in items for cs in it.get("cluster_stats", []) or []
    ]
    judged = [s for s in statuses if s != "n/a"]
    expected = sum(1 for it in items if _has_flag(it, "has_inconsistency"))
    detected = sum(
        1 for it in items if _has_flag(it, "has_inconsistency") and _is_detected(it)
    )
    metrics = {
        "errors": sum(1 for it in items if it.get("status") == "error"),
        "false_positive_items": sum(
            1
            for it in items
            if _is_detected(it) and not _has_flag(it, "has_inconsistency")
        ),
        "false_positive_changes": sum(
            len(it.get("false_positive_changes", [])) for it in items
        ),
        "detection_rate": _round(detected / expected) if expected else 1.0,
        "cluster_hit_rate": (
            _round(sum(1 for s in judged if s == "hit") / len(judged))
            if judged
            else 1.0
        ),
        "clusters_partial": sum(1 for s in judged if s == "partial"),
        "clusters_missed": sum(1 for s in judged if s == "missed"),
    }
    info = {
        "items_total": len(items),
        "expected_clusters": len(statuses),
        "judged_clusters": len(judged),
    }
    return metrics, info


def collect_translate(items: list[dict]) -> tuple[dict, dict]:
    """逐段翻译：期望短语命中 + 是否按需翻译。"""
    expected = sum(1 for it in items if _has_flag(it, "has_translation"))
    translated = sum(
        1
        for it in items
        if _has_flag(it, "has_translation") and it.get("status") == "translated"
    )
    metrics = {
        "errors": sum(1 for it in items if it.get("status") == "error"),
        "translation_rate": _round(translated / expected) if expected else 1.0,
        "phrase_matched": sum(len(it.get("matched", [])) for it in items),
        "phrase_missed": sum(len(it.get("missed", [])) for it in items),
        "unmatched_changes": sum(len(it.get("unmatched_changes", [])) for it in items),
        "false_positive_items": sum(
            1
            for it in items
            if it.get("status") == "translated" and not _has_flag(it, "has_translation")
        ),
    }
    info = {"items_total": len(items), "expected_translation_items": expected}
    return metrics, info


# ── OCR ────────────────────────────────────────────────────────────────────


def _normalize_ocr(strip_ocr_latex, latex: str) -> str:
    """清洗 + 空白折叠后比对（LaTeX 大小写敏感，不转小写）。"""
    return " ".join(strip_ocr_latex(latex or "").split())


def collect_ocr(items: list[dict], suite: str) -> tuple[dict, dict]:
    """公式 / 表格 OCR：语料 .tex 即期望答案，补算归一化匹配率。

    匹配率是近似指标（等价改写不算匹配），只用于回归观察，最终结论以
    人工报告为准。
    """
    if suite == "formula_ocr":
        from formula_ocr_eval.core import strip_ocr_latex
    else:
        from table_ocr_eval.core import strip_ocr_latex

    ok_items = [it for it in items if it.get("status") == "ok"]
    matched = sum(
        1
        for it in ok_items
        if _normalize_ocr(strip_ocr_latex, it.get("expected_latex", ""))
        == _normalize_ocr(strip_ocr_latex, it.get("latex", ""))
    )
    metrics = {
        "errors": sum(
            1 for it in items if it.get("status") in ("error", "render_error")
        ),
        "empty_results": sum(1 for it in items if it.get("status") == "empty"),
        "low_confidence": sum(1 for it in items if it.get("low_confidence")),
        "normalized_match_rate": (_round(matched / len(ok_items)) if ok_items else 0.0),
    }
    if suite == "formula_ocr":
        metrics["inline_mismatch"] = sum(
            1
            for it in ok_items
            if it.get("result_inline") is not None
            and it.get("result_inline") != it.get("expected_inline")
        )
    info = {"items_total": len(items), "ok_items": len(ok_items)}
    return metrics, info


# ── prompt_comprehension ───────────────────────────────────────────────────


def collect_prompt_comprehension(data: dict) -> tuple[dict, dict, dict]:
    summary = data.get("summary", {})
    metrics = {
        "pass_rate": _round(summary.get("pass_rate", 0.0)),
        "pass": summary.get("PASS", 0),
        "partial": summary.get("PARTIAL", 0),
        "fail": summary.get("FAIL", 0),
        "errors": summary.get("ERROR", 0),
    }
    info = {
        "total": summary.get("total", 0),
        "model": data.get("metadata", {}).get("model", ""),
        "prompt_hash": data.get("metadata", {}).get("prompt_hash", ""),
        "total_tokens": data.get("metadata", {}).get("total_tokens", 0),
    }
    cases = {c["name"]: c.get("status", "ERROR") for c in data.get("cases", [])}
    return metrics, info, cases


# ── web_access 重放报告 ────────────────────────────────────────────────────


def collect_web_report(data: dict) -> tuple[dict, dict]:
    aggregate = data.get("aggregate", {})
    metrics = {k: _round(v) for k, v in aggregate.items() if k in DIRECTIONS}
    info = {k: v for k, v in aggregate.items() if k not in DIRECTIONS}
    info["system"] = data.get("system", "")
    return metrics, info


# ── CLI ────────────────────────────────────────────────────────────────────

_COLLECTORS = {
    "typo_check": ("items", lambda items: collect_typo_like(items, "has_typo")),
    "expression_opt": (
        "items",
        lambda items: collect_typo_like(items, "has_improvement"),
    ),
    "terminology_check": ("items", collect_terminology),
    "paragraph_translate": ("items", collect_translate),
    "formula_ocr": ("items", lambda items: collect_ocr(items, "formula_ocr")),
    "table_ocr": ("items", lambda items: collect_ocr(items, "table_ocr")),
    "prompt_comprehension": ("document", collect_prompt_comprehension),
}


def collect(suite: str, data: dict) -> dict:
    """按 suite 归一化原始产物为统一 metrics 结构。"""
    out: dict = {
        "suite": suite,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": {},
        "info": {},
    }
    if suite.startswith("web_"):
        out["metrics"], out["info"] = collect_web_report(data)
        return out

    spec = _COLLECTORS.get(suite)
    if spec is None:
        raise ValueError(
            f"不支持的 suite：{suite}（可选：{', '.join(SUPPORTED_SUITES)}）"
        )
    kind, collector = spec
    if kind == "items":
        metrics, info = collector(
            data if isinstance(data, list) else data.get("items", data)
        )
        out["metrics"], out["info"] = metrics, info
    else:
        metrics, info, cases = collector(data)
        out["metrics"], out["info"], out["cases"] = metrics, info, cases
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", required=True, choices=SUPPORTED_SUITES)
    parser.add_argument("--results", required=True, type=Path, help="原始产物路径")
    parser.add_argument(
        "--output", required=True, type=Path, help="metrics.json 输出路径"
    )
    args = parser.parse_args()

    try:
        data = _load(args.results)
        result = collect(args.suite, data)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"指标已写入: {args.output}")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""回归比对共享规则：指标方向登记表、suite 分类与默认容差。

tools/ci/collect_metrics.py 产出的 metrics.json 与 tools/ci/baselines/ 下的
基线文件共用本模块的规则表做回归判定。新增指标时必须在这里登记方向
（higher=越大越好 / lower=越小越好）与类型（rate=0..1 比率 / count=计数），
未登记的指标只记录、不参与回归判定。
"""

from __future__ import annotations

# 指标方向登记表：指标名 -> (方向, 类型)
DIRECTIONS: dict[str, tuple[str, str]] = {
    # ── 语料式评测（typo / expression / terminology / translate）─────────
    "detection_rate": ("higher", "rate"),
    "missed_items": ("lower", "count"),
    "missed_expected": ("lower", "count"),
    "false_positive_items": ("lower", "count"),
    "false_positive_changes": ("lower", "count"),
    "cluster_hit_rate": ("higher", "rate"),
    "clusters_partial": ("lower", "count"),
    "clusters_missed": ("lower", "count"),
    "translation_rate": ("higher", "rate"),
    "phrase_matched": ("higher", "count"),
    "phrase_missed": ("lower", "count"),
    "unmatched_changes": ("lower", "count"),
    # ── OCR 评测（formula / table）───────────────────────────────────────
    "empty_results": ("lower", "count"),
    "low_confidence": ("lower", "count"),
    "normalized_match_rate": ("higher", "rate"),
    "inline_mismatch": ("lower", "count"),
    # ── prompt_comprehension ─────────────────────────────────────────────
    "pass_rate": ("higher", "rate"),
    "pass": ("higher", "count"),
    "partial": ("lower", "count"),
    "fail": ("lower", "count"),
    # ── web_access 重放报告（各 suite aggregate）─────────────────────────
    "ndcg_at_10": ("higher", "rate"),
    "mrr_at_10": ("higher", "rate"),
    "recall_at_20": ("higher", "rate"),
    "authority_hit": ("higher", "rate"),
    "domain_diversity_at_10": ("higher", "count"),
    "duplicate_rate_at_10": ("lower", "rate"),
    "engine_failure_count": ("lower", "count"),
    "prediction_coverage": ("higher", "rate"),
    "identifier_completeness": ("higher", "rate"),
    "oa_hit": ("higher", "rate"),
    "provider_failure_count": ("lower", "count"),
    "char_precision": ("higher", "rate"),
    "char_recall": ("higher", "rate"),
    "block_recall": ("higher", "rate"),
    "block_order": ("higher", "rate"),
    "status_accuracy": ("higher", "rate"),
    "strategy_accuracy": ("higher", "rate"),
    "kind_preservation": ("higher", "rate"),
    "heading_preservation": ("higher", "rate"),
    "boilerplate_ratio": ("lower", "rate"),
    "answer_term_recall": ("higher", "rate"),
    "evidence_recall": ("higher", "rate"),
    "citation_precision": ("higher", "rate"),
    # ── 通用 ─────────────────────────────────────────────────────────────
    "errors": ("lower", "count"),
}

# prompt_comprehension 逐用例状态回归排名（与该工具 compare.py 的规则一致）：
# 数字越大越差，排名上升即回归。
CASE_STATUS_RANKS: dict[str, int] = {"PASS": 0, "PARTIAL": 1, "FAIL": 2, "ERROR": 3}

# suite -> 类别。类别决定默认容差：deterministic 完全确定（离线重放），
# llm 生成结果有随机性，ocr 介于两者之间（识别输出随机、判定确定）。
SUITE_CLASSES: dict[str, str] = {
    "typo_check": "llm",
    "expression_opt": "llm",
    "terminology_check": "llm",
    "paragraph_translate": "llm",
    "prompt_comprehension": "llm",
    "formula_ocr": "ocr",
    "table_ocr": "ocr",
    "web_search": "deterministic",
    "web_scholarly": "deterministic",
    "web_extraction": "deterministic",
    "web_extraction_structured": "deterministic",
    "web_grounding": "deterministic",
}

# 类别 -> 默认容差 {rate, count}：比对时允许的无回归波动幅度
# （lower 指标：current > baseline + tol 才算回归；higher 指标对称）。
DEFAULT_TOLERANCES: dict[str, dict[str, float]] = {
    "deterministic": {"rate": 0.0, "count": 0.0},
    "llm": {"rate": 0.05, "count": 2.0},
    "ocr": {"rate": 0.1, "count": 2.0},
}


def suite_class(suite: str) -> str:
    """suite 所属类别（未登记按 llm 处理，容差偏保守）。"""
    return SUITE_CLASSES.get(suite, "llm")


def default_tolerance(suite: str, kind: str) -> float:
    """suite 某类型指标的默认容差。"""
    return DEFAULT_TOLERANCES[suite_class(suite)][kind]

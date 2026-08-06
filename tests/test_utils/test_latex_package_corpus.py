"""
宏包规则表语料测试。

语料位于 tests/fixtures/latex_packages/，按来源分组：
    - formula_ocr/   公式 OCR 输出（domain=math）
    - table_ocr/     表格 OCR 输出（domain=table）
    - pandoc/        Pandoc 转换结果 / 整篇文档（domain=general）
    - adversarial/   对抗样本（注释、逐字、URL、[H] 浮动、未闭合环境等）

每个片段带预期宏包列表（manifest.json），逐项比对形成查准率/查全率基线；
另计算"LLM 兜底触发率"（预期非空但规则表返回空的比例）。
"""

import json
from pathlib import Path

import pytest

from backend.utils.latex_package_analyzer import detect_required_packages

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "latex_packages"


def _load_manifest() -> dict[str, dict]:
    manifest_path = FIXTURES_DIR / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _iter_fixtures():
    manifest = _load_manifest()
    for rel_path, meta in manifest.items():
        fixture_path = FIXTURES_DIR / rel_path
        if not fixture_path.exists():
            raise FileNotFoundError(f"Corpus fixture missing: {fixture_path}")
        yield rel_path, fixture_path.read_text(encoding="utf-8"), meta


def _corpus():
    return list(_iter_fixtures())


@pytest.mark.parametrize(
    "rel_path,text,meta",
    [pytest.param(r, t, m, id=r) for r, t, m in _corpus()],
)
def test_fixture_matches_expected(rel_path, text, meta):
    """每个语料片段：规则表输出与预期宏包列表完全一致。"""
    domain = meta.get("domain", "general")
    expected = sorted(meta["expected"])
    detected = detect_required_packages(text, domain=domain)
    assert detected == expected, (
        f"{rel_path}: expected {expected}, got {detected}"
        f" (domain={domain}, note={meta.get('note', '')})"
    )


def test_corpus_baseline_metrics(caplog):
    """输出语料基线指标：查准率 / 查全率 / LLM 兜底触发率。"""
    import logging

    true_positives = 0
    false_positives = 0
    false_negatives = 0
    expected_nonempty = 0
    llm_trigger_candidates = 0

    for rel_path, text, meta in _iter_fixtures():
        domain = meta.get("domain", "general")
        expected = sorted(meta["expected"])
        detected = detect_required_packages(text, domain=domain)

        tp = len(set(detected) & set(expected))
        true_positives += tp
        false_positives += len(set(detected) - set(expected))
        false_negatives += len(set(expected) - set(detected))

        if expected:
            expected_nonempty += 1
            if not detected:
                llm_trigger_candidates += 1

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 1.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 1.0
    )
    llm_trigger_rate = (
        llm_trigger_candidates / expected_nonempty if expected_nonempty else 0.0
    )

    caplog.set_level(logging.INFO)
    logging.getLogger("latex_package_corpus").info(
        "Corpus baseline: precision=%.3f recall=%.3f "
        "llm_fallback_trigger_rate=%.3f (fixtures=%d)",
        precision,
        recall,
        llm_trigger_rate,
        len(_corpus()),
    )

    assert precision == 1.0
    assert recall == 1.0
    assert llm_trigger_rate == 0.0

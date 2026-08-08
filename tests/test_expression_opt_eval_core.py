"""expression_opt_eval 核心逻辑与语料一致性测试。

不依赖 LLM 调用与文件系统工作区：
    - analyze_hits / build_item 纯函数行为
    - 语料可达性：has_improvement=true 的期望优化原文必须出现在剥离后的纯文本中
      （marker 保护下，藏在命令参数/公式里的内容对 LLM 不可见，不应作为期望）
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.utils.latex_parser import LatexParser
from expression_opt_eval.core import (
    analyze_hits,
    iter_items,
    load_manifest,
    normalize_fragment,
)
from expression_opt_eval.run_eval import build_item


def _improvement(original: str, optimized: str = "x") -> dict:
    return {"original": original, "optimized": optimized}


def _change(original: str, optimized: str = "x") -> dict:
    return {
        "type": "rewrite",
        "original": original,
        "optimized": optimized,
        "reason": "",
    }


def _entry(**overrides: dict) -> dict:
    base = {
        "id": "expr_01",
        "tier": 1,
        "tier_name": "简单冗余",
        "title": "",
        "has_improvement": True,
        "expected_improvements": [_improvement("gets better results")],
    }
    base.update(overrides)
    return base


class TestAnalyzeHits:
    def test_empty_changes_marks_all_missed(self):
        result = analyze_hits([_improvement("a large number of")], [])
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["false_positive_changes"] == []

    def test_empty_expected_marks_all_false_positive(self):
        result = analyze_hits([], [_change("a large number of")])
        assert result["matched"] == []
        assert result["missed"] == []
        assert result["false_positive_changes"] == [0]

    def test_substring_match_both_directions(self):
        # change 比期望窄（期望 "with ease"，change 只引用 "ease"）
        result = analyze_hits([_improvement("with ease")], [_change("ease")])
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == []
        # change 比期望宽且包含完整期望片段
        result = analyze_hits(
            [_improvement("gets better results")],
            [_change("gets better results than the baseline")],
        )
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == []

    def test_wider_change_matches_via_shared_token(self):
        # 回归：期望 "with ease"，change 引用更宽片段 "applied to datasets with ease"
        # （包含期望完整子串但前后缀更宽），应命中且不算误报
        result = analyze_hits(
            [_improvement("with ease")],
            [_change("applied to large-scale datasets with ease")],
        )
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["false_positive_changes"] == []

    def test_short_shared_token_not_enough(self):
        # 只共享 2 字母词元 is 不构成命中
        result = analyze_hits(
            [_improvement("It is obvious that")],
            [_change("is the baseline")],
        )
        assert result["matched"] == []
        assert result["false_positive_changes"] == [0]

    def test_mixed_hit_and_false_positive(self):
        result = analyze_hits(
            [_improvement("a large number of")],
            [_change("a large number of"), _change("collected")],
        )
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["false_positive_changes"] == [1]

    def test_case_and_whitespace_insensitive(self):
        result = analyze_hits(
            [_improvement("In order to evaluate")],
            [_change("IN   ORDER to evaluate the model")],
        )
        assert result["matched"] == [0]


class TestBuildItem:
    def test_issues_status_with_changes(self):
        tex = "The model gets better results on the benchmark."
        para = {
            "plain_text": tex,
            "optimized": tex.replace("gets better results", "outperforms"),
            "changes": [_change("gets better results", "outperforms")],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert item["matched"] == [0]
        assert item["optimized_latex"] == "The model outperforms on the benchmark."

    def test_clean_status_no_changes(self):
        tex = "The framework is fast, accurate, and easy to implement."
        para = {
            "plain_text": tex,
            "optimized": tex,
            "changes": [],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "clean"
        assert item["missed"] == [0]

    def test_error_status_keeps_original(self):
        tex = "The model gets better results on the benchmark."
        item = build_item(_entry(), tex, {}, "workspace failed")
        assert item["status"] == "error"
        assert item["error"] == "workspace failed"
        assert item["optimized_latex"] == tex
        assert item["changes"] == []

    def test_unlisted_changes_flagged(self):
        tex = "The model gets better results on the benchmark."
        para = {
            "plain_text": tex,
            "optimized": tex.replace("gets better results", "outperforms"),
            "changes": [],
            "unlisted_changes": True,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert item["unlisted_changes"] is True

    def test_restores_markers_in_optimized(self):
        tex = r"The \textbf{proposed} method gets better results."
        plain, _ = LatexParser().strip_to_plain_text(tex)
        optimized = plain.replace("gets better results", "outperforms")
        para = {
            "plain_text": plain,
            "optimized": optimized,
            "changes": [_change("gets better results", "outperforms")],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert r"\textbf{proposed}" in item["optimized_latex"]
        assert "outperforms" in item["optimized_latex"]


class TestCorpusReachability:
    def test_ids_unique_and_files_exist(self):
        manifest = load_manifest()
        ids = [entry["id"] for entry in manifest["items"]]
        assert len(ids) == len(set(ids))
        for entry in manifest["items"]:
            assert (
                PROJECT_ROOT / "tests" / "fixtures" / "expression_opt" / entry["file"]
            ).exists()

    def test_single_paragraph_per_file(self):
        for entry, tex in iter_items(load_manifest()):
            assert "\n\n" not in tex.strip(), f"{entry['id']} 含空行，不是单段落"
            assert tex.strip(), f"{entry['id']} 为空文件"

    def test_expected_improvements_reachable_in_plain_text(self):
        # 回归：marker 保护下，藏在命令参数/公式里的内容对 LLM 不可见，
        # 不应作为 has_improvement=true 的期望优化原文
        parser = LatexParser()
        for entry, tex in iter_items(load_manifest()):
            plain, _ = parser.strip_to_plain_text(tex)
            plain_norm = normalize_fragment(plain)
            for improve in entry.get("expected_improvements", []):
                assert normalize_fragment(improve["original"]) in plain_norm, (
                    f"{entry['id']} 期望优化原文 {improve['original']!r} "
                    "在剥离后的纯文本中不可达（可能藏在命令参数或公式内）"
                )

    def test_has_improvement_consistent_with_expected_improvements(self):
        for entry in load_manifest()["items"]:
            assert bool(entry["has_improvement"]) == bool(
                entry.get("expected_improvements")
            ), f"{entry['id']} 的 has_improvement 与 expected_improvements 不一致"

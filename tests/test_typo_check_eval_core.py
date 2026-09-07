"""typo_check_eval 核心逻辑与语料一致性测试。

不依赖 LLM 调用与文件系统工作区：
    - analyze_hits / build_item 纯函数行为
    - 语料可达性：has_typo=true 的期望错字必须出现在剥离后的纯文本中
      （marker 保护下，藏在命令参数里的错字对 LLM 不可见，不应作为期望）
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from typo_check_eval.core import (
    analyze_hits,
    iter_items,
    load_manifest,
    normalize_fragment,
)
from typo_check_eval.run_eval import build_item

from backend.utils.latex_parser import LatexParser


def _issue(original: str, corrected: str = "x") -> dict:
    return {"original": original, "corrected": corrected}


def _change(original: str, corrected: str = "x") -> dict:
    return {
        "type": "typo",
        "original": original,
        "corrected": corrected,
        "explanation": "",
    }


def _entry(**overrides: dict) -> dict:
    base = {
        "id": "typo_01",
        "tier": 1,
        "tier_name": "简单错字",
        "title": "",
        "has_typo": True,
        "expected_issues": [_issue("recieve")],
    }
    base.update(overrides)
    return base


class TestAnalyzeHits:
    def test_empty_changes_marks_all_missed(self):
        result = analyze_hits([_issue("recieve")], [])
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["false_positive_changes"] == []

    def test_empty_expected_marks_all_false_positive(self):
        result = analyze_hits([], [_change("recieve")])
        assert result["matched"] == []
        assert result["missed"] == []
        assert result["false_positive_changes"] == [0]

    def test_substring_match_both_directions(self):
        # change 比期望窄（期望 "affect on"，change 只引用 "affect"）
        result = analyze_hits([_issue("affect on")], [_change("affect")])
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == []
        # change 比期望宽且包含完整期望片段
        result = analyze_hits([_issue("a apple")], [_change("a apple sample")])
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == []

    def test_wider_change_matches_via_shared_token(self):
        # 回归：期望 "affect on"，change 引用更宽片段 "has little affect"
        # （包含错字词元 affect 但不含完整期望子串），应命中且不算误报
        result = analyze_hits([_issue("affect on")], [_change("has little affect")])
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["false_positive_changes"] == []

    def test_short_shared_token_not_enough(self):
        # 只共享 2 字母词元 the 不构成命中
        result = analyze_hits([_issue("The the")], [_change("the experiment")])
        assert result["matched"] == []
        assert result["false_positive_changes"] == [0]

    def test_mixed_hit_and_false_positive(self):
        result = analyze_hits(
            [_issue("recieve")],
            [_change("recieve"), _change("signal")],
        )
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["false_positive_changes"] == [1]

    def test_case_and_whitespace_insensitive(self):
        result = analyze_hits(
            [_issue("new stationary")],
            [_change("NEW   stationary")],
        )
        assert result["matched"] == [0]


class TestBuildItem:
    def test_issues_status_with_changes(self):
        tex = "The model recieve the signal."
        para = {
            "plain_text": tex,
            "corrected": tex.replace("recieve", "receive"),
            "changes": [_change("recieve", "receive")],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert item["matched"] == [0]
        assert item["corrected_latex"] == "The model receive the signal."

    def test_clean_status_no_changes(self):
        tex = "The text is clean."
        para = {
            "plain_text": tex,
            "corrected": tex,
            "changes": [],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "clean"
        assert item["missed"] == [0]

    def test_error_status_keeps_original(self):
        tex = "The model recieve the signal."
        item = build_item(_entry(), tex, {}, "workspace failed")
        assert item["status"] == "error"
        assert item["error"] == "workspace failed"
        assert item["corrected_latex"] == tex
        assert item["changes"] == []

    def test_unlisted_changes_flagged(self):
        tex = "The model recieve the signal."
        para = {
            "plain_text": tex,
            "corrected": tex.replace("recieve", "receive"),
            "changes": [],
            "unlisted_changes": True,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert item["unlisted_changes"] is True

    def test_restores_markers_in_corrected(self):
        tex = r"The \textbf{proposed} method is definately reliable."
        plain, _ = LatexParser().strip_to_plain_text(tex)
        corrected = plain.replace("definately", "definitely")
        para = {
            "plain_text": plain,
            "corrected": corrected,
            "changes": [_change("definately", "definitely")],
            "unlisted_changes": False,
        }
        item = build_item(_entry(), tex, para)
        assert item["status"] == "issues"
        assert r"\textbf{proposed}" in item["corrected_latex"]
        assert "definitely" in item["corrected_latex"]


class TestCorpusReachability:
    def test_ids_unique_and_files_exist(self):
        manifest = load_manifest()
        ids = [entry["id"] for entry in manifest["items"]]
        assert len(ids) == len(set(ids))
        for entry in manifest["items"]:
            assert (
                PROJECT_ROOT / "tests" / "fixtures" / "typo_check" / entry["file"]
            ).exists()

    def test_single_paragraph_per_file(self):
        for entry, tex in iter_items(load_manifest()):
            assert "\n\n" not in tex.strip(), f"{entry['id']} 含空行，不是单段落"
            assert tex.strip(), f"{entry['id']} 为空文件"

    def test_expected_issues_reachable_in_plain_text(self):
        # 回归：marker 保护下，藏在命令参数里的错字对 LLM 不可见，
        # 不应作为 has_typo=true 的期望错字（如旧 typo_14 的 \textbf{recieve}）
        parser = LatexParser()
        for entry, tex in iter_items(load_manifest()):
            plain, _ = parser.strip_to_plain_text(tex)
            plain_norm = normalize_fragment(plain)
            for issue in entry.get("expected_issues", []):
                assert normalize_fragment(issue["original"]) in plain_norm, (
                    f"{entry['id']} 期望错字 {issue['original']!r} "
                    "在剥离后的纯文本中不可达（可能藏在命令参数内）"
                )

    def test_has_typo_consistent_with_expected_issues(self):
        for entry in load_manifest()["items"]:
            assert bool(entry["has_typo"]) == bool(
                entry.get("expected_issues")
            ), f"{entry['id']} 的 has_typo 与 expected_issues 不一致"

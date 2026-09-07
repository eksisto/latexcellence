"""paragraph_translate_eval 核心逻辑与语料一致性测试。

不依赖 LLM 调用与文件系统工作区：
    - analyze_hits / build_item 纯函数行为
    - 语料可达性：期望短语原文必须出现在剥离后的纯文本中
      （marker 保护下，藏在命令参数/公式里的文本对 LLM 不可见，不应作为期望）
    - 语料契约：单段落、短语译文含于参考译文、has_translation 与 expected_phrases 一致
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paragraph_translate_eval.core import (
    analyze_hits,
    iter_items,
    load_manifest,
    normalize_fragment,
    validate_corpus,
)
from paragraph_translate_eval.run_eval import build_item

from backend.utils.latex_parser import LatexParser


def _phrase(original: str, translated: str) -> dict:
    return {"original": original, "translated": translated}


def _change(original: str, translated: str) -> dict:
    return {
        "type": "translation",
        "original": original,
        "translated": translated,
        "explanation": "",
    }


def _entry(**overrides: dict) -> dict:
    base = {
        "id": "trans_01",
        "tier": 1,
        "tier_name": "简单直译",
        "title": "",
        "source_language": "English",
        "target_language": "Chinese",
        "has_translation": True,
        "expected_translation": "图神经网络已经成为建模关系型数据的流行工具。",
        "expected_phrases": [_phrase("Graph neural networks", "图神经网络")],
    }
    base.update(overrides)
    return base


class TestAnalyzeHits:
    def test_empty_changes_marks_all_missed(self):
        result = analyze_hits([_phrase("Graph neural networks", "图神经网络")], [])
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["unmatched_changes"] == []

    def test_change_matching_both_sides(self):
        result = analyze_hits(
            [_phrase("Graph neural networks", "图神经网络")],
            [_change("Graph neural networks", "图神经网络")],
        )
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["unmatched_changes"] == []

    def test_original_side_only_is_not_a_hit(self):
        # 只匹配原文、译文不匹配，不算命中
        result = analyze_hits(
            [_phrase("Graph neural networks", "图神经网络")],
            [_change("Graph neural networks", "神经图模型")],
            translated_text="",
        )
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["unmatched_changes"] == [0]

    def test_translated_text_fallback(self):
        # LLM 未输出 changes 时，译文整体包含期望译文也算命中
        result = analyze_hits(
            [_phrase("Graph neural networks", "图神经网络")],
            [],
            translated_text="图神经网络已经成为建模关系型数据的流行工具。",
        )
        assert result["matched"] == [0]
        assert result["missed"] == []

    def test_translated_text_missing_keeps_missed(self):
        result = analyze_hits(
            [_phrase("Graph neural networks", "图神经网络")],
            [],
            translated_text="图数据建模工具越来越流行。",
        )
        assert result["matched"] == []
        assert result["missed"] == [0]

    def test_unmatched_changes_are_informational(self):
        result = analyze_hits(
            [_phrase("Graph neural networks", "图神经网络")],
            [_change("node classification", "节点分类")],
            translated_text="",
        )
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["unmatched_changes"] == [0]

    def test_empty_expected_all_changes_unmatched(self):
        result = analyze_hits([], [_change("node", "节点")])
        assert result["matched"] == []
        assert result["missed"] == []
        assert result["unmatched_changes"] == [0]


class TestBuildItem:
    def _para(self, original: str, translated: str, changes: list[dict]) -> dict:
        return {
            "paragraph_index": 0,
            "original": original,
            "translated": translated,
            "changes": changes,
            "text": original,
            "plain_text": original,
            "file_path": "trans_x.tex",
        }

    def test_translated_status_with_changes(self):
        tex = "Graph neural networks are popular."
        para = self._para(
            tex, "图神经网络很流行。", [_change("Graph neural networks", "图神经网络")]
        )
        item = build_item(_entry(), tex, para)
        assert item["status"] == "translated"
        assert item["translated_latex"] == "图神经网络很流行。"
        assert item["matched"] == [0]

    def test_unchanged_status(self):
        tex = "The text is identical."
        para = self._para(tex, tex, [])
        item = build_item(_entry(), tex, para)
        assert item["status"] == "unchanged"
        assert item["missed"] == [0]

    def test_error_status_keeps_original(self):
        tex = "Graph neural networks are popular."
        item = build_item(_entry(), tex, {}, "workspace failed")
        assert item["status"] == "error"
        assert item["error"] == "workspace failed"
        assert item["translated_latex"] == tex
        assert item["changes"] == []

    def test_restores_markers_in_fallback_translated(self):
        # 回退路径的 translated 是 marker 空间文本，应还原为可读 LaTeX
        tex = r"The cost is $O(n^2)$ and the model is \textbf{light}."
        plain, _ = LatexParser().strip_to_plain_text(tex)
        para = self._para(tex, plain, [])
        item = build_item(_entry(), tex, para)
        assert item["status"] == "unchanged"
        assert item["translated_latex"] == tex

    def test_success_path_restore_is_idempotent(self):
        # 成功路径的 translated 已是还原后的干净 LaTeX，restore 不改变内容
        tex = r"The cost is $O(n^2)$."
        translated = "成本是 $O(n^2)$。"
        para = self._para(tex, translated, [])
        item = build_item(_entry(), tex, para)
        assert item["status"] == "translated"
        assert item["translated_latex"] == translated

    def test_already_in_target_language_with_changes_flagged(self):
        tex = "In this section, we summarize our contributions."
        para = self._para(
            tex, "在本节中，我们总结了贡献。", [_change("In this section", "在本节中")]
        )
        entry = _entry(
            has_translation=False,
            source_language="English",
            target_language="English",
            expected_phrases=[],
        )
        item = build_item(entry, tex, para)
        assert item["status"] == "translated"
        assert item["unmatched_changes"] == [0]


class TestCorpusReachability:
    def test_ids_unique_and_files_exist(self):
        manifest = load_manifest()
        ids = [entry["id"] for entry in manifest["items"]]
        assert len(ids) == len(set(ids))
        for entry in manifest["items"]:
            assert (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "paragraph_translate"
                / entry["file"]
            ).exists()

    def test_single_paragraph_per_file(self):
        for entry, tex in iter_items(load_manifest()):
            assert "\n\n" not in tex.strip(), f"{entry['id']} 含空行，不是单段落"
            assert tex.strip(), f"{entry['id']} 为空文件"

    def test_expected_phrases_reachable_in_plain_text(self):
        parser = LatexParser()
        for entry, tex in iter_items(load_manifest()):
            plain, _ = parser.strip_to_plain_text(tex)
            plain_norm = normalize_fragment(plain)
            for phrase in entry.get("expected_phrases", []):
                original_norm = normalize_fragment(phrase["original"])
                if original_norm.isascii():
                    found = (
                        re.search(
                            r"(?<![A-Za-z])"
                            + re.escape(original_norm)
                            + r"(?![A-Za-z])",
                            plain_norm,
                        )
                        is not None
                    )
                else:
                    found = original_norm in plain_norm
                assert found, (
                    f"{entry['id']} 期望短语原文 {phrase['original']!r} "
                    "在剥离后的纯文本中不可达（可能藏在命令参数/公式内）"
                )

    def test_phrase_translations_contained_in_reference(self):
        for entry in load_manifest()["items"]:
            reference = normalize_fragment(entry["expected_translation"])
            for phrase in entry.get("expected_phrases", []):
                assert normalize_fragment(phrase["translated"]) in reference, (
                    f"{entry['id']} 期望短语译文不在参考译文中: "
                    f"{phrase['translated']!r}"
                )

    def test_has_translation_consistent_with_phrases(self):
        for entry in load_manifest()["items"]:
            assert bool(entry["has_translation"]) == bool(
                entry.get("expected_phrases")
            ), f"{entry['id']} 的 has_translation 与 expected_phrases 不一致"

    def test_language_names_valid(self):
        for entry in load_manifest()["items"]:
            assert entry["source_language"] in {"English", "Chinese"}
            assert entry["target_language"] in {"English", "Chinese"}
            if entry["has_translation"]:
                assert entry["source_language"] != entry["target_language"]

    def test_validate_corpus_passes(self):
        assert validate_corpus() == []

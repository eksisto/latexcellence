"""terminology_check_eval 核心逻辑与语料一致性测试。

不依赖 LLM 调用与文件系统工作区：
    - analyze_hits / build_item 纯函数行为
    - 语料可达性：期望术语簇的变体必须出现在剥离后的纯文本中
      （marker 保护下，藏在命令参数/公式里的术语对 LLM 不可见，不应作为期望）
    - 语料契约：多段落文档、recommended 必须是 variants 之一、
      has_inconsistency 与 expected_clusters 一致
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.utils.latex_parser import LatexParser
from terminology_check_eval.core import (
    analyze_hits,
    iter_items,
    load_manifest,
    normalize_fragment,
    validate_corpus,
)
from terminology_check_eval.run_eval import build_item


def _cluster(recommended: str, variants: list[str] | None = None) -> dict:
    variants = variants or [recommended]
    return {"recommended": recommended, "variants": variants}


def _change(original: str, corrected: str, paragraph_index: int = 0) -> dict:
    return {
        "type": "inconsistency",
        "concept": corrected,
        "original": original,
        "corrected": corrected,
        "explanation": "",
        "paragraph_index": paragraph_index,
    }


def _entry(**overrides: dict) -> dict:
    base = {
        "id": "term_01",
        "tier": 1,
        "tier_name": "大小写与格式混用",
        "title": "",
        "has_inconsistency": True,
        "expected_clusters": [
            _cluster("deep learning", ["Deep Learning", "deep learning"])
        ],
    }
    base.update(overrides)
    return base


class TestAnalyzeHits:
    def test_empty_changes_marks_all_missed(self):
        result = analyze_hits([_cluster("cnn", ["CNN", "cnn"])], [])
        assert result["matched"] == []
        assert result["missed"] == [0]
        assert result["false_positive_changes"] == []
        assert result["cluster_stats"][0]["status"] == "missed"

    def test_empty_expected_marks_all_false_positive(self):
        result = analyze_hits([], [_change("CNN", "cnn")])
        assert result["matched"] == []
        assert result["missed"] == []
        assert result["false_positive_changes"] == [0]

    def test_case_only_variant_is_expected_change(self):
        # 回归：大小写变体归一化后与推荐项相同，仍应作为期望变体，
        # 且双向统一（统一到哪个大小写）都算命中，方向由人工判断
        cluster = _cluster("deep learning", ["Deep Learning", "deep learning"])
        result = analyze_hits([cluster], [_change("deep learning", "Deep Learning")])
        assert result["matched"] == [0]
        assert result["missed"] == []
        assert result["false_positive_changes"] == []
        assert result["cluster_stats"][0]["status"] == "hit"
        assert result["cluster_stats"][0]["matched_variants"] == ["Deep Learning"]

    def test_abbreviation_direction_matters(self):
        # 缩写与全称的推荐方向不一致时不计命中
        cluster = _cluster(
            "convolutional neural network", ["CNN", "convolutional neural network"]
        )
        result = analyze_hits(
            [cluster], [_change("convolutional neural network", "CNN")]
        )
        assert result["matched"] == []
        assert result["false_positive_changes"] == [0]

    def test_abbreviation_hit(self):
        cluster = _cluster(
            "convolutional neural network", ["CNN", "convolutional neural network"]
        )
        result = analyze_hits(
            [cluster], [_change("CNN", "convolutional neural network")]
        )
        assert result["matched"] == [0]
        assert result["cluster_stats"][0]["status"] == "hit"

    def test_partial_cluster(self):
        cluster = _cluster(
            "machine learning", ["Machine Learning", "machine learning", "ML"]
        )
        result = analyze_hits([cluster], [_change("ML", "machine learning")])
        # 期望变体展开顺序：["Machine Learning"(0), "ML"(1)]，ML 命中索引 1
        assert result["matched"] == [1]
        assert result["missed"] == [0]
        assert result["cluster_stats"][0]["status"] == "partial"

    def test_mixed_hit_and_false_positive(self):
        cluster = _cluster("深度学习", ["深度学习", "deep learning"])
        result = analyze_hits(
            [cluster],
            [_change("deep learning", "深度学习"), _change("神经网络", "深度学习")],
        )
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == [1]

    def test_wider_change_matches_via_substring(self):
        cluster = _cluster("deep learning", ["deep learning", "DL"])
        result = analyze_hits([cluster], [_change("DL models", "deep learning models")])
        assert result["matched"] == [0]
        assert result["false_positive_changes"] == []


class TestBuildItem:
    def _para(
        self,
        text: str,
        changes: list[dict] | None = None,
        terms: list[str] | None = None,
    ) -> dict:
        plain = text
        corrected = text
        for c in changes or []:
            corrected = corrected.replace(c["original"], c["corrected"])
        return {
            "paragraph_index": 0,
            "text": text,
            "corrected": plain,
            "plain_text": plain,
            "changes": changes or [],
            "terms": terms or [],
        }

    def test_issues_status_with_changes(self):
        tex_1 = "CNN performs well."
        tex_2 = "The CNN is fast."
        result = {
            "results": [
                {
                    "paragraph_index": 0,
                    "text": tex_1,
                    "corrected": tex_1.replace("CNN", "convolutional neural network"),
                    "plain_text": tex_1,
                    "changes": [_change("CNN", "convolutional neural network", 0)],
                    "terms": ["CNN"],
                    "file_path": "term_x.tex",
                },
                {
                    "paragraph_index": 1,
                    "text": tex_2,
                    "corrected": tex_2,
                    "plain_text": tex_2,
                    "changes": [],
                    "terms": [],
                    "file_path": "term_x.tex",
                },
            ]
        }
        item = build_item(_entry(), tex_1 + "\n\n" + tex_2, result)
        assert item["status"] == "issues"
        assert item["changes"][0]["paragraph_index"] == 0
        assert len(item["paragraphs"]) == 2
        assert item["paragraphs"][1]["corrected_latex"] == tex_2

    def test_clean_status_no_changes(self):
        tex = "The encoder is consistent."
        result = {
            "results": [
                {
                    "paragraph_index": 0,
                    "text": tex,
                    "corrected": tex,
                    "plain_text": tex,
                    "changes": [],
                    "terms": ["encoder"],
                    "file_path": "term_x.tex",
                }
            ]
        }
        item = build_item(
            _entry(has_inconsistency=False, expected_clusters=[]), tex, result
        )
        assert item["status"] == "clean"
        assert item["missed"] == []
        assert item["false_positive_changes"] == []

    def test_error_status_keeps_original(self):
        tex = "CNN performs well."
        item = build_item(_entry(), tex, {}, "workspace failed")
        assert item["status"] == "error"
        assert item["error"] == "workspace failed"
        assert item["changes"] == []
        assert item["paragraphs"] == []

    def test_restores_markers_in_corrected(self):
        tex = r"The CNN and \textbf{the} GNN are compared."
        plain, _ = LatexParser().strip_to_plain_text(tex)
        corrected = plain.replace("CNN", "convolutional neural network")
        result = {
            "results": [
                {
                    "paragraph_index": 0,
                    "text": tex,
                    "corrected": corrected,
                    "plain_text": plain,
                    "changes": [_change("CNN", "convolutional neural network", 0)],
                    "terms": ["CNN", "GNN"],
                    "file_path": "term_x.tex",
                }
            ]
        }
        item = build_item(_entry(), tex, result)
        assert item["status"] == "issues"
        corrected_latex = item["paragraphs"][0]["corrected_latex"]
        assert r"\textbf{the}" in corrected_latex
        assert "convolutional neural network" in corrected_latex


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
                / "terminology_check"
                / entry["file"]
            ).exists()

    def test_multi_paragraph_per_file(self):
        # 术语一致性依赖跨段落聚类，每条语料必须至少 2 个段落
        for entry, tex in iter_items(load_manifest()):
            assert tex.strip(), f"{entry['id']} 为空文件"
            assert (
                len(tex.strip().split("\n\n")) >= 2
            ), f"{entry['id']} 段落数 < 2，无法评估跨段落一致性"

    def test_expected_variants_reachable_in_plain_text(self):
        # 回归：marker 保护下，藏在命令参数/公式里的术语对 LLM 不可见，
        # 不应作为期望变体
        parser = LatexParser()
        for entry, tex in iter_items(load_manifest()):
            plain, _ = parser.strip_to_plain_text(tex)
            plain_norm = normalize_fragment(plain)
            for cluster in entry.get("expected_clusters", []):
                for variant in cluster.get("variants", []):
                    variant_norm = normalize_fragment(variant)
                    if variant_norm.isascii():
                        import re

                        found = (
                            re.search(
                                r"(?<![A-Za-z])"
                                + re.escape(variant_norm)
                                + r"(?![A-Za-z])",
                                plain_norm,
                            )
                            is not None
                        )
                    else:
                        found = variant_norm in plain_norm
                    assert found, (
                        f"{entry['id']} 期望变体 {variant!r} "
                        "在剥离后的纯文本中不可达（可能藏在命令参数/公式内）"
                    )

    def test_recommended_is_a_variant(self):
        for entry in load_manifest()["items"]:
            for cluster in entry.get("expected_clusters", []):
                assert (
                    cluster["recommended"] in cluster["variants"]
                ), f"{entry['id']} 的 recommended 不在 variants 中"

    def test_has_inconsistency_consistent_with_clusters(self):
        for entry in load_manifest()["items"]:
            assert bool(entry["has_inconsistency"]) == bool(
                entry.get("expected_clusters")
            ), f"{entry['id']} 的 has_inconsistency 与 expected_clusters 不一致"

    def test_validate_corpus_passes(self):
        assert validate_corpus() == []

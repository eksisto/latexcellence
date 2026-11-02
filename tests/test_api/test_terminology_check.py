"""
terminology_check 模块测试。

覆盖内容：
    - Pydantic 模型校验（TermChange / TermParagraphResult / TermCluster 等）
    - _deduplicate_terms 去重逻辑
    - _build_results 基于 LLM 聚类的结果构建
    - term_extractor LLM mock（提取 + 聚类）
    - preview_paragraphs
    - apply_changes marker 还原
    - 空段落处理
    - 无 LLM 降级路径
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.terminology_check.extractor import TermExtractor
from backend.modules.terminology_check.models import (
    ExtractedTerm,
    TermChange,
    TermCluster,
    TermClusterResult,
    TermExtractionResult,
    TermParagraphResult,
)
from backend.modules.terminology_check.service import (
    TerminologyCheckService,
    _deduplicate_terms,
)


@pytest.fixture
def temp_project():
    """创建包含多段落的临时 LaTeX 项目。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        main_content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n\n"
            r"Machine Learning (ML) is a powerful technique. "
            r"Deep learning is a subset of machine learning." + "\n\n"
            r"The ML approach has been widely adopted. "
            r"The Convolutional Neural Network (CNN) works well." + "\n\n"
            r"machine learning is a key concept. "
            "In conclusion, \u673a\u5668\u5b66\u4e60 has many applications." + "\n\n"
            r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        yield root


@pytest.fixture
def service():
    return TerminologyCheckService()


class TestModels:
    """Pydantic 模型校验测试。"""

    def test_term_change_valid(self):
        data = {
            "type": "inconsistency",
            "concept": "Machine Learning",
            "original": "machine learning",
            "corrected": "Machine Learning",
            "explanation": "consistent casing",
        }
        change = TermChange.model_validate(data)
        assert change.type == "inconsistency"
        assert change.original == "machine learning"
        assert change.corrected == "Machine Learning"

    def test_term_change_defaults(self):
        data = {
            "original": "a",
            "corrected": "b",
        }
        change = TermChange.model_validate(data)
        assert change.type == "inconsistency"
        assert change.concept == ""
        assert change.explanation == ""

    def test_term_paragraph_result_valid(self):
        data = {
            "paragraph_index": 0,
            "original": "some text",
            "corrected": "some text",
            "terms": ["ML", "CNN"],
            "changes": [],
        }
        result = TermParagraphResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.terms == ["ML", "CNN"]

    def test_extracted_term_valid(self):
        data = {"term": "Machine Learning", "term_type": "concept", "context": "is a"}
        term = ExtractedTerm.model_validate(data)
        assert term.term == "Machine Learning"
        assert term.term_type == "concept"

    def test_term_extraction_result_empty(self):
        data = {"terms": []}
        result = TermExtractionResult.model_validate(data)
        assert result.terms == []

    def test_term_cluster_valid(self):
        data = {
            "recommended": "Deep Learning",
            "variants": ["Deep Learning", "deep learning", "DL"],
        }
        cluster = TermCluster.model_validate(data)
        assert cluster.recommended == "Deep Learning"
        assert len(cluster.variants) == 3

    def test_term_cluster_result_empty(self):
        data = {"clusters": []}
        result = TermClusterResult.model_validate(data)
        assert result.clusters == []

    def test_term_cluster_result_with_clusters(self):
        data = {
            "clusters": [
                {
                    "recommended": "深度学习",
                    "variants": ["Deep Learning", "deep learning", "深度学习", "DL"],
                },
                {
                    "recommended": "Machine Learning",
                    "variants": ["Machine Learning", "machine learning"],
                },
            ]
        }
        result = TermClusterResult.model_validate(data)
        assert len(result.clusters) == 2
        assert result.clusters[0].recommended == "深度学习"
        assert "深度学习" in result.clusters[0].variants

    def test_missing_required_field_fails(self):
        with pytest.raises(ValidationError):
            TermChange.model_validate({"type": "x"})

    def test_term_cluster_missing_variants_fails(self):
        with pytest.raises(ValidationError):
            TermCluster.model_validate({"recommended": "x"})


class TestDeduplicateTerms:
    """术语去重逻辑测试（仅当字符完全匹配时视为重复）。"""

    def test_deduplicate_exact_matches(self):
        terms = [
            {"term": "Machine Learning", "term_type": "concept"},
            {"term": "Machine Learning", "term_type": "concept"},
            {"term": "machine learning", "term_type": "concept"},
        ]
        unique = _deduplicate_terms(terms)
        assert len(unique) == 2
        assert "Machine Learning" in unique
        assert "machine learning" in unique

    def test_deduplicate_preserves_order(self):
        terms = [
            {"term": "B", "term_type": "concept"},
            {"term": "A", "term_type": "concept"},
            {"term": "B", "term_type": "concept"},
        ]
        unique = _deduplicate_terms(terms)
        assert unique == ["B", "A"]

    def test_deduplicate_empty(self):
        assert _deduplicate_terms([]) == []

    def test_deduplicate_all_same(self):
        terms = [{"term": "ML", "term_type": "abbreviation"}] * 5
        unique = _deduplicate_terms(terms)
        assert unique == ["ML"]

    def test_deduplicate_case_sensitive(self):
        terms = [
            {"term": "DL", "term_type": "abbreviation"},
            {"term": "dl", "term_type": "abbreviation"},
            {"term": "Dl", "term_type": "abbreviation"},
        ]
        unique = _deduplicate_terms(terms)
        assert len(unique) == 3


class TestBuildResults:
    """基于 LLM 聚类的结果构建测试。"""

    def test_build_results_no_clusters(self, service):
        para_data = [
            {"para_index": 0, "plain_text": "hello", "text": "hello", "marker_map": {}}
        ]
        all_terms = [
            {
                "term": "hello",
                "term_type": "concept",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "",
            }
        ]
        results = service._build_results(para_data, all_terms, [])
        assert len(results) == 1
        assert results[0]["changes"] == []
        assert results[0]["original"] == "hello"
        assert results[0]["corrected"] == "hello"

    def test_build_results_with_cluster(self, service):
        para_data = [
            {
                "para_index": 0,
                "plain_text": "use machine learning here",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
            {
                "para_index": 1,
                "plain_text": "use Machine Learning here",
                "text": "tex1",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "machine learning",
                "term_type": "concept",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "use",
            },
            {
                "term": "Machine Learning",
                "term_type": "concept",
                "paragraph_index": 1,
                "file_path": "main.tex",
                "context": "use",
            },
        ]
        clusters = [
            {
                "recommended": "Machine Learning",
                "variants": ["machine learning", "Machine Learning"],
            },
        ]
        results = service._build_results(para_data, all_terms, clusters)

        r0 = results[0]
        assert len(r0["changes"]) == 1
        assert r0["changes"][0]["original"] == "machine learning"
        assert r0["changes"][0]["corrected"] == "Machine Learning"
        assert r0["changes"][0]["type"] == "inconsistency"
        assert r0["corrected"] == "use Machine Learning here"

        r1 = results[1]
        assert len(r1["changes"]) == 0
        assert r1["corrected"] == r1["plain_text"]
        assert r1["original"] == "tex1"

    def test_build_results_recommended_in_variants(self, service):
        """推荐项本身在 variants 中不被替换。"""
        para_data = [
            {
                "para_index": 0,
                "plain_text": "深度学习 is great",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
            {
                "para_index": 1,
                "plain_text": "deep learning is great",
                "text": "tex1",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "深度学习",
                "term_type": "concept",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "is great",
            },
            {
                "term": "deep learning",
                "term_type": "concept",
                "paragraph_index": 1,
                "file_path": "main.tex",
                "context": "is great",
            },
        ]
        clusters = [
            {
                "recommended": "深度学习",
                "variants": ["深度学习", "deep learning", "Deep Learning"],
            },
        ]
        results = service._build_results(para_data, all_terms, clusters)

        r0 = results[0]
        assert len(r0["changes"]) == 0  # 推荐项不在 replace_map 中

        r1 = results[1]
        assert len(r1["changes"]) == 1
        assert r1["changes"][0]["original"] == "deep learning"
        assert r1["changes"][0]["corrected"] == "深度学习"
        assert r1["corrected"] == "深度学习 is great"

    def test_build_results_empty_paragraphs(self, service):
        results = service._build_results([], [], [])
        assert results == []

    def test_build_results_term_not_in_text(self, service):
        """提取的术语不在段落文本中时不生成 change（安全处理）。"""
        para_data = [
            {
                "para_index": 0,
                "plain_text": "some text",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "missing_term",
                "term_type": "concept",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "",
            },
        ]
        clusters = [
            {"recommended": "standard", "variants": ["missing_term", "standard"]},
        ]
        results = service._build_results(para_data, all_terms, clusters)
        # 未发生实际替换时不保留 change，展示与主字段一致
        assert len(results) == 1
        assert results[0]["changes"] == []
        assert results[0]["corrected"] == "some text"

    def test_build_results_word_boundary_prevents_substring_corruption(self, service):
        """短变体 "ML" 不得命中 "HTML" 等单词的子串。"""
        para_data = [
            {
                "para_index": 0,
                "plain_text": "render HTML output with ML",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "ML",
                "term_type": "abbreviation",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "",
            },
        ]
        clusters = [
            {
                "recommended": "Machine Learning",
                "variants": ["ML", "Machine Learning"],
            },
        ]
        results = service._build_results(para_data, all_terms, clusters)

        r0 = results[0]
        assert len(r0["changes"]) == 1
        assert r0["corrected"] == "render HTML output with Machine Learning"

    def test_build_results_does_not_corrupt_marker_internals(self, service):
        """变体与 marker 内部子串重合时（"CMD" 在 __CMD_0000__ 内），
        只替换独立出现的变体，marker 保持原样。"""
        para_data = [
            {
                "para_index": 0,
                "plain_text": "the __CMD_0000__ block with CMD",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "CMD",
                "term_type": "abbreviation",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "",
            },
        ]
        clusters = [
            {"recommended": "command", "variants": ["CMD", "command"]},
        ]
        results = service._build_results(para_data, all_terms, clusters)

        r0 = results[0]
        assert len(r0["changes"]) == 1
        assert r0["corrected"] == "the __CMD_0000__ block with command"

    def test_build_results_falls_back_when_replacement_creates_marker(self, service):
        """替换会产生新 marker 串时整体回退原文，marker 序列不得改变。"""
        para_data = [
            {
                "para_index": 0,
                "plain_text": "use MATH here",
                "text": "tex0",
                "marker_map": {},
                "file_path": "main.tex",
            },
        ]
        all_terms = [
            {
                "term": "MATH",
                "term_type": "abbreviation",
                "paragraph_index": 0,
                "file_path": "main.tex",
                "context": "",
            },
        ]
        clusters = [
            {"recommended": "__MATH_0001__", "variants": ["MATH"]},
        ]
        results = service._build_results(para_data, all_terms, clusters)

        r0 = results[0]
        assert r0["changes"] == []
        assert r0["corrected"] == "use MATH here"


class TestPreviewParagraphs:
    """预览段落测试。"""

    def test_preview_strips_latex(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        result = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        assert result["total"] >= 1
        assert result["file_path"] == "main.tex"

        for p in result["paragraphs"]:
            assert "plain_text" in p
            assert "text" in p
            assert r"\documentclass" not in p["plain_text"]
            assert r"\section" not in p["plain_text"]


class TestApplyChanges:
    """修改写回测试。"""

    def test_apply_restores_markers(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        preview = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        operation_id = preview["operation_id"]

        applied = False
        for p in preview["paragraphs"]:
            if "machine learning" in p["plain_text"]:
                corrected_plain = p["plain_text"].replace(
                    "machine learning", "Machine Learning"
                )
                result = service.apply_changes(
                    "main.tex",
                    [{"paragraph_index": p["index"], "new_text": corrected_plain}],
                    workspace_id=ws_id,
                    operation_id=operation_id,
                )
                assert result["applied"] == 1
                applied = True
                break

        assert applied, "Expected to find and apply a change"
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "Machine Learning" in content


class TestExtractor:
    """术语提取器测试。"""

    def test_extract_terms_fallback_abbreviations(self):
        extractor = TermExtractor()
        text = "The CNN and RNN models are used in NLP tasks."
        terms = extractor.extract_terms_fallback(text)
        abbrevs = [t.term for t in terms]
        assert "CNN" in abbrevs
        assert "RNN" in abbrevs

    def test_extract_terms_fallback_empty(self):
        extractor = TermExtractor()
        text = "No uppercase abbreviations here."
        terms = extractor.extract_terms_fallback(text)
        assert terms == []

    def test_extract_terms_fallback_filters_markers(self):
        extractor = TermExtractor()
        text = "__CMD_0000__ The NLP model works well."
        terms = extractor.extract_terms_fallback(text)
        assert "__CMD_0000__" not in [t.term for t in terms]
        assert "NLP" in [t.term for t in terms]

    @pytest.mark.asyncio
    async def test_extract_terms_with_llm(self):
        extractor = TermExtractor()
        text = "Machine Learning (ML) is widely used in AI."

        with patch(
            "backend.modules.terminology_check.extractor.get_client_for"
        ) as mock_get:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(
                return_value={
                    "terms": [
                        {
                            "term": "Machine Learning",
                            "term_type": "concept",
                            "context": "is widely used",
                        },
                        {
                            "term": "ML",
                            "term_type": "abbreviation",
                            "context": "(ML) is",
                        },
                        {"term": "AI", "term_type": "abbreviation", "context": "in AI"},
                    ],
                }
            )
            mock_get.return_value = mock_llm

            terms = await extractor.extract_terms(0, text)
            assert len(terms) == 3
            assert terms[0].term == "Machine Learning"
            assert terms[1].term == "ML"
            assert terms[1].term_type == "abbreviation"
            # 槽位锁定：extractor 直调走 terminology_check 槽（multi-model §6）
            mock_get.assert_called_once_with("terminology_check")

    @pytest.mark.asyncio
    async def test_extract_terms_llm_failure_returns_empty(self):
        extractor = TermExtractor()
        text = "Some text."

        with patch(
            "backend.modules.terminology_check.extractor.get_client_for"
        ) as mock_get:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))
            mock_get.return_value = mock_llm

            terms = await extractor.extract_terms(0, text)
            assert terms == []


class TestClusterTerms:
    """LLM 语义聚类测试。"""

    @pytest.mark.asyncio
    async def test_cluster_terms_success(self):
        extractor = TermExtractor()
        unique_terms = ["Deep Learning", "deep learning", "深度学习", "DL", "CNN"]

        with patch(
            "backend.modules.terminology_check.extractor.get_client_for"
        ) as mock_get:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(
                return_value={
                    "clusters": [
                        {
                            "recommended": "深度学习",
                            "variants": [
                                "Deep Learning",
                                "deep learning",
                                "深度学习",
                                "DL",
                            ],
                        },
                    ],
                }
            )
            mock_get.return_value = mock_llm

            clusters = await extractor.cluster_terms(unique_terms)
            assert len(clusters) == 1
            assert clusters[0]["recommended"] == "深度学习"
            assert "Deep Learning" in clusters[0]["variants"]
            assert "深度学习" in clusters[0]["variants"]

    @pytest.mark.asyncio
    async def test_cluster_terms_empty_input(self):
        extractor = TermExtractor()
        clusters = await extractor.cluster_terms([])
        assert clusters == []

    @pytest.mark.asyncio
    async def test_cluster_terms_no_inconsistencies(self):
        extractor = TermExtractor()
        unique_terms = ["Python", "Java", "C++"]

        with patch(
            "backend.modules.terminology_check.extractor.get_client_for"
        ) as mock_get:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value={"clusters": []})
            mock_get.return_value = mock_llm

            clusters = await extractor.cluster_terms(unique_terms)
            assert clusters == []

    @pytest.mark.asyncio
    async def test_cluster_terms_llm_failure_returns_empty(self):
        extractor = TermExtractor()
        unique_terms = ["ML", "machine learning"]

        with patch(
            "backend.modules.terminology_check.extractor.get_client_for"
        ) as mock_get:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))
            mock_get.return_value = mock_llm

            clusters = await extractor.cluster_terms(unique_terms)
            assert clusters == []


class TestCheckNoLLM:
    """无 API Key 降级路径测试。"""

    def test_no_llm_path_returns_results_without_changes(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        paragraphs = pm.get_paragraphs("main.tex")
        all_para_data = []
        for p in paragraphs:
            stripped, marker_map = service._parser.strip_to_plain_text(p.text)
            all_para_data.append(
                {
                    "file_path": "main.tex",
                    "para_index": p.index,
                    "text": p.text,
                    "plain_text": stripped,
                    "marker_map": marker_map,
                }
            )
        result = service._check_no_llm("main.tex", all_para_data)
        assert result["file_path"] == "main.tex"
        assert "results" in result
        assert len(result["results"]) == len(paragraphs)

        for r in result["results"]:
            assert "paragraph_index" in r
            assert "original" in r
            assert "corrected" in r
            assert "changes" in r
            # 无 LLM 时不生成 changes
            assert r["changes"] == []


class TestEmptyResult:
    """空段落处理。"""

    def test_empty_result(self, service):
        result = service._empty_result(
            {
                "para_index": 5,
                "plain_text": "",
                "text": "original tex",
                "marker_map": {},
            }
        )
        assert result["paragraph_index"] == 5
        assert result["original"] == ""
        assert result["corrected"] == ""
        assert result["changes"] == []
        assert result["terms"] == []


class TestParagraphIndicesSelection:
    """check_terminology 的段落选择参数必须落地，不得接受但忽略。"""

    @pytest.mark.asyncio
    async def test_check_terminology_respects_paragraph_indices(
        self, temp_project, service, monkeypatch
    ):
        from backend.core.workspace_manager import get_project_manager_for_workspace
        from backend.modules.terminology_check import service as svc_mod

        monkeypatch.setattr(svc_mod, "has_api_key_for", lambda role: False)

        pm = get_project_manager_for_workspace("ws-term-idx")
        pm.open(str(temp_project))
        result = await service.check_terminology(
            "main.tex", paragraph_indices=[1], workspace_id="ws-term-idx"
        )

        assert [r["paragraph_index"] for r in result["results"]] == [1]

    @pytest.mark.asyncio
    async def test_check_terminology_without_indices_checks_all(
        self, temp_project, service, monkeypatch
    ):
        from backend.core.workspace_manager import get_project_manager_for_workspace
        from backend.modules.terminology_check import service as svc_mod

        monkeypatch.setattr(svc_mod, "has_api_key_for", lambda role: False)

        pm = get_project_manager_for_workspace("ws-term-all")
        pm.open(str(temp_project))
        result = await service.check_terminology(
            "main.tex", paragraph_indices=None, workspace_id="ws-term-all"
        )

        assert len(result["results"]) > 1

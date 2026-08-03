"""
code_review 模块集成测试。

覆盖内容：
    - Pydantic 模型校验（CodeReviewResult / CodeReviewSuggestion）
    - _check_one_paragraph LLM mock（双数据线架构）
    - _check_one_paragraph 纯 marker 段落（跳过 LLM）
    - _check_one_paragraph LLM 报告无需审查
    - _check_one_paragraph LLM 报告有审查建议
    - apply_changes 直接写回（无需 marker 还原）
    - _format_source 降级（无 latexindent 时使用内置格式化器）
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.code_review.service import (
    CodeReviewService,
    CodeReviewResult,
    CodeReviewSuggestion,
)

# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def temp_project():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        main_content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"  \item poorly indented item" + "\n"
            r"\setlength{\parskip}{0pt}" + "\n"
            r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        yield root


@pytest.fixture
def service():
    return CodeReviewService()


# ── Pydantic model tests ───────────────────────────────────────────────────


class TestCodeReviewResultModel:
    """CodeReviewResult / CodeReviewSuggestion 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "paragraph_index": 0,
            "needs_review": False,
            "suggestions": [],
        }
        result = CodeReviewResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.needs_review is False
        assert result.suggestions == []

    def test_valid_with_suggestions(self):
        data = {
            "paragraph_index": 1,
            "needs_review": True,
            "suggestions": [
                {
                    "type": "code_smell",
                    "severity": "warning",
                    "description": r"Use \centering instead of \centerline",
                    "original": r"\centerline{text}",
                    "suggested": r"\begin{center}text\end{center}",
                },
            ],
        }
        result = CodeReviewResult.model_validate(data)
        assert len(result.suggestions) == 1
        assert result.suggestions[0].type == "code_smell"
        assert result.suggestions[0].severity == "warning"

    def test_suggestion_defaults(self):
        data = {
            "type": "modernization",
            "description": "suggestion",
            "original": "old",
            "suggested": "new",
        }
        suggestion = CodeReviewSuggestion.model_validate(data)
        assert suggestion.type == "modernization"
        assert suggestion.severity == "info"

    def test_rejects_control_char_in_original(self):
        """\\rho 经 JSON 转义损坏后变成回车 + 'ho'，original 必须被拦截。"""
        data = {
            "type": "code_smell",
            "description": "x",
            "original": "R^" + "\r" + "ho",
            "suggested": r"\rho",
        }
        with pytest.raises(ValidationError, match="control character"):
            CodeReviewSuggestion.model_validate(data)

    def test_rejects_unbalanced_braces_in_suggested(self):
        data = {
            "type": "best_practice",
            "description": "x",
            "original": r"\label{sec:1}",
            "suggested": r"\label{sec:1",
        }
        with pytest.raises(ValidationError, match="unbalanced braces"):
            CodeReviewSuggestion.model_validate(data)

    def test_missing_required_field(self):
        # paragraph_index is the only field without a default
        data = {"needs_review": False}
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate(data)

    def test_needs_review_true_empty_suggestions(self):
        data = {
            "paragraph_index": 0,
            "needs_review": True,
            "suggestions": [],
        }
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate(data)

    def test_needs_review_false_nonempty_suggestions(self):
        data = {
            "paragraph_index": 0,
            "needs_review": False,
            "suggestions": [
                {
                    "type": "modernization",
                    "description": "test",
                    "original": "old",
                    "suggested": "new",
                }
            ],
        }
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate(data)

    def test_invalid_suggestion_type(self):
        data = {
            "paragraph_index": 0,
            "needs_review": True,
            "suggestions": [
                {
                    "type": "invalid_type",
                    "description": "test",
                    "original": "old",
                    "suggested": "new",
                }
            ],
        }
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate(data)

    def test_invalid_severity(self):
        data = {
            "paragraph_index": 0,
            "needs_review": True,
            "suggestions": [
                {
                    "type": "code_smell",
                    "severity": "critical",
                    "description": "test",
                    "original": "old",
                    "suggested": "new",
                }
            ],
        }
        with pytest.raises(ValidationError):
            CodeReviewResult.model_validate(data)


# ── service tests with mocked LLM ──────────────────────────────────────────


class TestPreviewParagraphs:
    """预览段落测试。"""

    def test_returns_raw_latex_and_plain_text(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        result = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        assert result["total"] >= 1
        assert result["file_path"] == "main.tex"

        for p in result["paragraphs"]:
            assert "text" in p
            assert "plain_text" in p


class TestCheckOneParagraph:
    """LLM 调用 + 双数据线架构逻辑测试。"""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_successful_code_review(self, mock_llm, service):
        original = r"\centerline{Hello World}"
        text_with_markers = "Hello World"  # assume markers already stripped

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "needs_review": True,
                "suggestions": [
                    {
                        "type": "code_smell",
                        "severity": "warning",
                        "description": r"Use \centering instead of \centerline",
                        "original": r"\centerline{Hello World}",
                        "suggested": r"\begin{center}Hello World\end{center}",
                    }
                ],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 5, text_with_markers, original, "main.tex"
        )

        assert result["paragraph_index"] == 5
        assert "new_text" in result
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["type"] == "code_smell"

    @pytest.mark.asyncio
    async def test_no_review_needed(self, mock_llm, service):
        original = r"\section{Introduction}"
        text_with_markers = r"\section{Introduction}"

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "needs_review": False,
                "suggestions": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, text_with_markers, original, "main.tex"
        )

        assert result["paragraph_index"] == 0
        assert result["suggestions"] == []
        assert result["needs_review"] is False

    @pytest.mark.asyncio
    async def test_pydantic_validation_retry_exhausted(self, mock_llm, service):
        original = r"\section{Intro}"

        mock_llm.chat = AsyncMock(return_value={"bad": "json"})

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, "Intro", original, "main.tex"
        )

        assert "new_text" in result
        assert result["suggestions"] == []
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self, mock_llm, service):
        original = r"\section{Intro}"

        mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))

        result = await service._check_one_paragraph(
            mock_llm, 0, 7, "Intro", original, "main.tex"
        )

        assert result["paragraph_index"] == 7
        assert result["suggestions"] == []

    @pytest.mark.asyncio
    async def test_control_char_retries_then_recovers(self, mock_llm, service):
        """suggestions 里的 LaTeX 片段损坏 → 触发重试并最终恢复。"""
        original = r"\centerline{Hello World}"

        mock_llm.chat = AsyncMock(
            side_effect=[
                {
                    "paragraph_index": 0,
                    "needs_review": True,
                    "suggestions": [
                        {
                            "type": "code_smell",
                            "severity": "warning",
                            "description": "x",
                            "original": "R^" + "\r" + "ho",
                            "suggested": r"\rho",
                        }
                    ],
                },
                {
                    "paragraph_index": 0,
                    "needs_review": True,
                    "suggestions": [
                        {
                            "type": "code_smell",
                            "severity": "warning",
                            "description": "x",
                            "original": r"\centerline{Hello World}",
                            "suggested": r"\begin{center}Hello World\end{center}",
                        }
                    ],
                },
            ]
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 5, "Hello World", original, "main.tex"
        )

        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["suggested"] == r"\begin{center}Hello World\end{center}"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_pure_marker_paragraph_skips_llm(self, mock_llm, service):
        """纯 marker 段落（无可见文本）应跳过 LLM 审查。"""
        original = r"\begin{equation}\end{equation}"
        # 仅有 marker 的纯文本
        text_with_markers = "__ENV_0000__"

        # 即使 LLM 不可用也不应被调用
        result = await service._check_one_paragraph(
            mock_llm, 0, 0, text_with_markers, original, "main.tex"
        )

        assert result["needs_review"] is False
        assert result["suggestions"] == []
        mock_llm.chat.assert_not_called()


class TestApplyChanges:
    """修改写回测试——无需 marker 还原，直接写回。"""

    def test_apply_writes_formatted_latex(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        paragraphs = pm.get_paragraphs("main.tex")
        for para in paragraphs:
            if "poorly indented" in para.text:
                new_text = para.text.replace(
                    r"  \item poorly indented item",
                    r"\item poorly indented item",
                )
                result = service.apply_changes(
                    "main.tex",
                    [{"paragraph_index": para.index, "new_text": new_text}],
                    workspace_id=ws_id,
                )
                assert result["applied"] == 1
                break

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert r"  \item poorly indented item" not in content
        assert r"\item poorly indented item" in content


class TestBuiltinFormatter:
    """内置格式化器测试。"""

    def test_format_latex_basic_indent(self):
        from backend.utils.builtin_formatter import format_latex

        source = (
            r"\begin{document}" + "\n"
            r"\section{Test}" + "\n"
            r"\begin{itemize}" + "\n"
            r"\item item 1" + "\n"
            r"\item item 2" + "\n"
            r"\end{itemize}" + "\n"
            r"\end{document}"
        )
        result = format_latex(source)
        assert r"\begin{document}" in result
        assert r"\end{document}" in result
        # itemize 内的 items 应被缩进
        assert "    \\item" in result


class TestLatexindentUtils:
    """latexindent 工具测试。"""

    def test_is_latexindent_available_returns_bool(self):
        from backend.utils.latexindent_utils import is_latexindent_available

        result = is_latexindent_available()
        assert isinstance(result, bool)


class TestEmptyResult:
    """空白段落结果测试。"""

    @pytest.mark.asyncio
    async def test_empty_result_for_blank_paragraph(self, service):
        result = service._make_fallback(2, "   ", "   ", "main.tex")
        assert result["paragraph_index"] == 2
        assert result["original"] == "   "
        assert result["new_text"] == "   "
        assert result["suggestions"] == []

"""
formatting 模块集成测试。

覆盖内容：
    - Pydantic 模型校验（FormattingResult / FormattingChangeItem）
    - preview_paragraphs 原始 LaTeX + 纯文本上下文
    - _check_one_paragraph LLM mock（成功 / 无修改 / 校验失败回退 / 异常回退 / 空输出回退）
    - apply_changes 直接写回（无需 marker 还原）
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.formatting.service import (
    FormattingService,
    FormattingResult,
    FormattingChangeItem,
)
from backend.modules.typo_check.latex_parser import LatexParser
from backend.core.config import load_config, get_config


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _ensure_config():
    try:
        get_config()
    except RuntimeError:
        project_root = Path(__file__).resolve().parent.parent.parent
        config_path = project_root / "config.json"
        if config_path.exists():
            load_config(str(config_path))
        else:
            load_config("config.json")


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
    return FormattingService()


@pytest.fixture
def parser():
    return LatexParser()


# ── Pydantic model tests ───────────────────────────────────────────────────


class TestFormattingResultModel:
    """FormattingResult / FormattingChangeItem 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "paragraph_index": 0,
            "original": r"\section{Intro}",
            "corrected": r"\section{Intro}",
            "changes": [],
        }
        result = FormattingResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.corrected == r"\section{Intro}"
        assert result.changes == []

    def test_valid_with_changes(self):
        data = {
            "paragraph_index": 1,
            "original": r"  \item bad indent",
            "corrected": r"\item bad indent",
            "changes": [
                {
                    "type": "code_formatting",
                    "description": "Removed leading indentation",
                    "original": r"  \item bad indent",
                    "corrected": r"\item bad indent",
                },
            ],
        }
        result = FormattingResult.model_validate(data)
        assert len(result.changes) == 1
        assert result.changes[0].type == "code_formatting"
        assert result.changes[0].description == "Removed leading indentation"

    def test_change_item_defaults(self):
        data = {
            "type": "spacing",
            "original": r"\setlength{\parskip}{0pt}",
            "corrected": r"\setlength{\parskip}{1em}",
        }
        change = FormattingChangeItem.model_validate(data)
        assert change.type == "spacing"
        assert change.description == ""

    def test_missing_required_field(self):
        data = {"paragraph_index": 0, "original": "text"}
        with pytest.raises(ValidationError):
            FormattingResult.model_validate(data)

    def test_wrong_type(self):
        data = {
            "paragraph_index": "wrong",
            "original": "text",
            "corrected": "text",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            FormattingResult.model_validate(data)


# ── service tests with mocked LLM ──────────────────────────────────────────


class TestPreviewParagraphs:
    """预览段落测试。"""

    def test_returns_raw_latex_and_plain_text(self, temp_project, service):
        from backend.core.project_manager import get_project_manager

        pm = get_project_manager()
        pm.open(str(temp_project))

        result = service.preview_paragraphs("main.tex")
        assert result["total"] >= 1
        assert result["file_path"] == "main.tex"

        for p in result["paragraphs"]:
            assert "text" in p
            assert "plain_text" in p
            assert r"\documentclass" not in p["plain_text"]
            assert r"\begin" not in p["plain_text"]


class TestCheckOneParagraph:
    """LLM 调用 + 校验逻辑测试。"""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_successful_formatting(self, mock_llm, service):
        original = r"  \item poorly indented"

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": original,
            "corrected": r"\item poorly indented",
            "changes": [
                {
                    "type": "code_formatting",
                    "description": "Removed leading indentation",
                    "original": original,
                    "corrected": r"\item poorly indented",
                }
            ],
        })

        result = await service._check_one_paragraph(mock_llm, 0, 5, original)

        assert result["paragraph_index"] == 5
        assert result["corrected"] == r"\item poorly indented"
        assert len(result["changes"]) == 1
        assert result["changes"][0]["type"] == "code_formatting"

    @pytest.mark.asyncio
    async def test_no_changes_needed(self, mock_llm, service):
        original = r"\section{Introduction}"

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": original,
            "corrected": original,
            "changes": [],
        })

        result = await service._check_one_paragraph(mock_llm, 0, 0, original)

        assert result["paragraph_index"] == 0
        assert result["corrected"] == original
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_empty_corrected_falls_back(self, mock_llm, service):
        original = r"\setlength{\parskip}{0pt}"

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": original,
            "corrected": "   ",
            "changes": [],
        })

        result = await service._check_one_paragraph(mock_llm, 0, 0, original)

        assert result["corrected"] == original
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_pydantic_validation_retry_exhausted(self, mock_llm, service):
        original = r"\section{Intro}"

        mock_llm.chat = AsyncMock(return_value={"bad": "json"})

        result = await service._check_one_paragraph(mock_llm, 0, 3, original)

        assert result["corrected"] == original
        assert result["changes"] == []
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self, mock_llm, service):
        original = r"\section{Intro}"

        mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))

        result = await service._check_one_paragraph(mock_llm, 0, 7, original)

        assert result["paragraph_index"] == 7
        assert result["corrected"] == original
        assert result["changes"] == []


class TestApplyChanges:
    """修改写回测试——无需 marker 还原，直接写回。"""

    def test_apply_writes_corrected_latex(self, temp_project, service):
        from backend.core.project_manager import get_project_manager

        pm = get_project_manager()
        pm.open(str(temp_project))

        service.preview_paragraphs("main.tex")

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
                )
                assert result["applied"] == 1
                break

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert r"  \item poorly indented item" not in content
        assert r"\item poorly indented item" in content


class TestEmptyResult:
    """空白段落结果测试。"""

    @pytest.mark.asyncio
    async def test_empty_result_for_blank_paragraph(self, service):
        result = await service._empty_result(2, "   ")
        assert result["paragraph_index"] == 2
        assert result["original"] == "   "
        assert result["corrected"] == "   "
        assert result["changes"] == []

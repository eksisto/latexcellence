"""
paragraph_translate 模块测试。

覆盖内容：
    - Pydantic 模型校验（ParagraphTranslateResult / TranslateChangeItem）
    - _check_one_paragraph 的 LLM 响应处理（含 mock）
    - 轻量内容校验（控制字符 / 垃圾占位符 → 重试回退；\\t / \\n 放行）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.paragraph_translate.service import (
    ParagraphTranslateService,
    ParagraphTranslateResult,
    TranslateChangeItem,
)

# ── Pydantic model tests ───────────────────────────────────────────────────


class TestParagraphTranslateResultModel:
    """ParagraphTranslateResult / TranslateChangeItem 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "paragraph_index": 0,
            "translated": "你好，世界。",
            "changes": [],
        }
        result = ParagraphTranslateResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.translated == "你好，世界。"
        assert result.changes == []

    def test_valid_with_changes(self):
        data = {
            "paragraph_index": 1,
            "translated": r"Hello __CMD_0000__ world.",
            "changes": [
                {
                    "type": "translation",
                    "original": "你好",
                    "translated": "Hello",
                    "explanation": "直接翻译",
                },
            ],
        }
        result = ParagraphTranslateResult.model_validate(data)
        assert len(result.changes) == 1
        assert result.changes[0].type == "translation"
        assert result.changes[0].explanation == "直接翻译"

    def test_change_item_defaults(self):
        data = {
            "type": "translation",
            "original": "a",
            "translated": "b",
        }
        change = TranslateChangeItem.model_validate(data)
        assert change.explanation == ""

    def test_missing_required_field(self):
        data = {"paragraph_index": 0}
        with pytest.raises(ValidationError):
            ParagraphTranslateResult.model_validate(data)

    def test_wrong_type(self):
        data = {
            "paragraph_index": "not_a_number",
            "translated": "text",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            ParagraphTranslateResult.model_validate(data)

    def test_control_char_rejected(self):
        """translated 含控制字符（JSON 反斜杠转义损坏特征）→ 校验失败。"""
        data = {
            "paragraph_index": 0,
            "translated": "R^" + "\r" + "ho",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            ParagraphTranslateResult.model_validate(data)

    def test_garbage_placeholder_rejected(self):
        data = {
            "paragraph_index": 0,
            "translated": "[object Object]",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            ParagraphTranslateResult.model_validate(data)

    def test_tab_and_newline_accepted(self):
        """\\t / \\n 是合法空白，轻量校验放行。"""
        data = {
            "paragraph_index": 0,
            "translated": "line1\nline2\ttab",
            "changes": [],
        }
        result = ParagraphTranslateResult.model_validate(data)
        assert result.translated == "line1\nline2\ttab"


# ── service tests with mocked LLM ──────────────────────────────────────────


class TestCheckOneParagraph:
    """LLM 调用 + 轻量校验逻辑测试。"""

    @pytest.fixture
    def service(self):
        return ParagraphTranslateService()

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_successful_translation(self, mock_llm, service):
        """LLM 返回正常翻译结果。"""
        plain = r"Hello __CMD_0000__ world."
        original_tex = r"Hello \textbf{world}."

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "translated": r"你好 __CMD_0000__ 世界。",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 5, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 5
        assert result["translated"] == r"你好 __CMD_0000__ 世界。"
        assert result["changes"] == []
        assert "marker_error" not in result

    @pytest.mark.asyncio
    async def test_marker_mismatch_falls_back(self, mock_llm, service):
        """LLM 修改/遗漏了 marker → 回退到原始文本。"""
        plain = r"Text with __CMD_0000__ marker."
        original_tex = r"Text with \textbf{marker.}"

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "translated": "Text with marker.",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["translated"] == plain
        assert result.get("marker_error") is True

    @pytest.mark.asyncio
    async def test_corrupted_output_control_char_falls_back(self, mock_llm, service):
        """translated 含控制字符（JSON 反斜杠转义损坏特征）→ 重试耗尽回退。"""
        plain = "Some text."
        original_tex = "Some text."

        corrupted = "R^" + "\r" + "ho"
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "translated": corrupted,
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, plain, original_tex, "main.tex"
        )

        assert result["translated"] == plain
        assert result["changes"] == []
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_corrupted_output_garbage_placeholder_falls_back(
        self, mock_llm, service
    ):
        """translated 含 '[object Object]' 垃圾占位符 → 重试耗尽回退。"""
        plain = "Some text."
        original_tex = "Some text."

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "translated": "[object Object]",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, plain, original_tex, "main.tex"
        )

        assert result["translated"] == plain
        assert result["changes"] == []
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_output_with_tab_and_newline_passes(self, mock_llm, service):
        """translated 含 \\t / \\n 属合法空白，轻量校验放行、不误报。"""
        plain = "Line one.\nLine two."
        original_tex = "Line one.\nLine two."

        translated = "第一行\t修正。\n第二行。"
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "translated": translated,
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, plain, original_tex, "main.tex"
        )

        assert result["translated"] == translated
        assert "marker_error" not in result
        assert mock_llm.chat.call_count == 1

"""
compile_error_translator 单元测试。

覆盖内容：
    - ErrorTranslation Pydantic 模型校验
    - extract_key_errors 错误日志过滤
    - translate_compile_errors LLM 翻译流程（Mock LLM）
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.utils.compile_error_translator import (
    ErrorTranslation,
    extract_key_errors,
    translate_compile_errors,
)

# ── ErrorTranslation 模型测试 ─────────────────────────────────────────────


class TestErrorTranslationModel:
    """ErrorTranslation 模型校验测试。"""

    def test_valid(self):
        data = {"explanation": "缺少宏包", "fixes": ["添加 \\usepackage{amsmath}"]}
        result = ErrorTranslation.model_validate(data)
        assert result.explanation == "缺少宏包"
        assert len(result.fixes) == 1

    def test_empty_fixes(self):
        data = {"explanation": "无建议", "fixes": []}
        result = ErrorTranslation.model_validate(data)
        assert result.fixes == []

    def test_missing_explanation_raises(self):
        with pytest.raises(ValidationError):
            ErrorTranslation.model_validate({"fixes": []})


# ── extract_key_errors 测试 ────────────────────────────────────────────────


class TestExtractKeyErrors:
    """extract_key_errors 错误日志过滤测试。"""

    def test_extracts_undefined_control_sequence(self):
        log = (
            "Some preamble\n! Undefined control sequence.\nl.42 \\badcommand\nMore text"
        )
        result = extract_key_errors(log)
        assert "Undefined control sequence" in result
        assert "\\badcommand" in result

    def test_extracts_latex_error(self):
        log = "Header\n! LaTeX Error: File `missing.sty' not found.\nTrailer"
        result = extract_key_errors(log)
        assert "LaTeX Error" in result
        assert "missing.sty" in result

    def test_extracts_emergency_stop(self):
        log = "Text\n! Emergency stop.\nl.10 \\end{document}\n"
        result = extract_key_errors(log)
        assert "Emergency stop" in result

    def test_extracts_package_error(self):
        log = "normal\nPackage hyperref Error: driver file not found.\ntrailer"
        result = extract_key_errors(log)
        assert "hyperref Error" in result

    def test_extracts_missing_inserted(self):
        log = "some text\nMissing $ inserted\nl.5 \\alfa\nend"
        result = extract_key_errors(log)
        assert "Missing $ inserted" in result

    def test_no_error_patterns_returns_tail(self):
        log = "a" * 4000
        result = extract_key_errors(log)
        assert len(result) <= 3000

    def test_empty_log(self):
        result = extract_key_errors("")
        assert result == ""

    def test_respects_max_chars(self):
        lines = ["! Error line\n"] + ["x" * 100 + "\n"] * 50
        log = "".join(lines)
        result = extract_key_errors(log, max_chars=200)
        assert len(result) > 0
        assert len(result) < 5000


# ── translate_compile_errors 测试 ──────────────────────────────────────────


class TestTranslateCompileErrors:
    """translate_compile_errors LLM 翻译流程测试。"""

    @pytest.mark.asyncio
    async def test_empty_output_returns_empty(self):
        """空输出直接返回空结果。"""
        explanation, fixes = await translate_compile_errors("", "")
        assert explanation == ""
        assert fixes == []

    @pytest.mark.asyncio
    async def test_whitespace_only_output_returns_empty(self):
        """纯空白输出直接返回空结果。"""
        explanation, fixes = await translate_compile_errors("  \n  \n", "  ")
        assert explanation == ""
        assert fixes == []

    @pytest.mark.asyncio
    async def test_llm_success(self):
        """LLM 成功翻译。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value='{"explanation": "缺少宏包 amsmath", "fixes": ["添加 \\\\usepackage{amsmath}"]}'
        )

        stdout = "! LaTeX Error: File `amsmath.sty' not found.\n"
        with patch(
            "backend.utils.compile_error_translator.get_llm_client",
            return_value=mock_llm,
        ):
            explanation, fixes = await translate_compile_errors(stdout, "")

        assert explanation == "缺少宏包 amsmath"
        assert len(fixes) == 1
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        """LLM 失败时返回兜底提示。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM offline"))

        stdout = "! Undefined control sequence.\nl.42 \\bad\n"
        with patch(
            "backend.utils.compile_error_translator.get_llm_client",
            return_value=mock_llm,
        ):
            explanation, fixes = await translate_compile_errors(stdout, "")

        assert "LLM" in explanation
        assert fixes == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json_retries(self):
        """LLM 返回无效 JSON 时重试。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            side_effect=[
                "not json at all",
                '{"explanation": "OK", "fixes": []}',
            ]
        )

        stdout = "! Emergency stop.\n"
        with patch(
            "backend.utils.compile_error_translator.get_llm_client",
            return_value=mock_llm,
        ):
            explanation, fixes = await translate_compile_errors(stdout, "")

        assert explanation == "OK"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_with_filtered_output_skips_regex(self):
        """传入 filtered_output 时跳过 extract_key_errors 正则。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value='{"explanation": "已过滤的错误", "fixes": ["修复"]}'
        )

        filtered = "! Undefined control sequence.\nl.42 \\bad\n"
        with patch(
            "backend.utils.compile_error_translator.get_llm_client",
            return_value=mock_llm,
        ):
            with patch(
                "backend.utils.compile_error_translator.extract_key_errors"
            ) as mock_extract:
                explanation, fixes = await translate_compile_errors(
                    "raw stdout", "raw stderr", filtered_output=filtered
                )

        # extract_key_errors 不应被调用
        mock_extract.assert_not_called()
        assert explanation == "已过滤的错误"
        # LLM prompt 中应包含 filtered 而非 raw
        call_args = mock_llm.chat.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        assert "Undefined control sequence" in user_msg
        assert "raw stdout" not in user_msg

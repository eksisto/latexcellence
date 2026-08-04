"""
table_ocr 模块集成测试。
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.table_ocr.service import (
    TableOCRResult,
    TableOCRService,
)

TABLE_LATEX = (
    r"\begin{table}[htbp]\centering"
    r"\caption{Results}\begin{tabular}{lr}\toprule Name & Value \\"
    r"\midrule A & 10 \\\bottomrule\end{tabular}\end{table}"
)


def ocr_response(latex, confidence="0.95"):
    """构造符合 <<<LATEX>>> 分隔符协议的模型原始输出。"""
    return f"<<<LATEX>>>\n{latex}\n<<<END>>>\n" f"CONFIDENCE: {confidence}"


@pytest.fixture
def service():
    return TableOCRService()


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.chat = AsyncMock()
    return llm


class TestTableOCRResultModel:
    """TableOCRResult 模型校验测试。"""

    def test_valid(self):
        data = {"latex": TABLE_LATEX, "confidence": 0.95}
        result = TableOCRResult.model_validate(data)
        assert result.confidence == 0.95

    def test_accepts_crlf_and_tab(self):
        """分隔符协议（非 JSON）路径放行合法空白 \\t / \\r（宽松模式）。"""
        latex = TABLE_LATEX.replace(
            r"Name & Value", "Name" + "\t" + "&" + "\r" + "Value"
        )
        result = TableOCRResult.model_validate({"latex": latex})
        assert result.latex == latex

    def test_defaults(self):
        data = {"latex": r"\begin{table}[htbp]\end{table}"}
        result = TableOCRResult.model_validate(data)
        assert result.confidence == 0.0

    def test_missing_latex(self):
        with pytest.raises(ValidationError):
            TableOCRResult.model_validate({"confidence": 0.9})

    def test_wrong_confidence_type(self):
        with pytest.raises(ValidationError):
            TableOCRResult.model_validate(
                {
                    "latex": r"\begin{table}[htbp]\end{table}",
                    "confidence": "high",
                }
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            TableOCRResult.model_validate({"latex": TABLE_LATEX, "confidence": 1.5})

    def test_rejects_control_char(self):
        """\\begin 经 JSON 转义损坏后变成退格 + 'egin'，必须被内容校验器拦截。"""
        latex = "\b" + r"egin{table}[htbp]\end{table}"
        with pytest.raises(ValidationError, match="control character"):
            TableOCRResult.model_validate({"latex": latex})

    def test_rejects_garbage_placeholder(self):
        latex = (
            r"\begin{table}[htbp]\centering"
            r"\begin{tabular}{c}[object Object]\end{tabular}\end{table}"
        )
        with pytest.raises(ValidationError, match="garbage"):
            TableOCRResult.model_validate({"latex": latex})

    def test_rejects_unbalanced_braces(self):
        latex = r"\begin{table}[htbp]\caption{Results\end{table}"
        with pytest.raises(ValidationError, match="unbalanced braces"):
            TableOCRResult.model_validate({"latex": latex})

    def test_rejects_missing_table_env(self):
        """必须输出 table 浮动体，仅 tabular 不通过。"""
        latex = r"\begin{tabular}{cc}A & B\end{tabular}"
        with pytest.raises(ValidationError, match="table"):
            TableOCRResult.model_validate({"latex": latex})

    def test_table_star_accepted(self):
        """table*（跨栏宽表格）是合法浮动体，应放行。"""
        latex = r"\begin{table*}[htbp]\centering A & B \end{table*}"
        result = TableOCRResult.model_validate({"latex": latex, "confidence": 0.9})
        assert result.latex == latex

    def test_empty_latex_zero_confidence_passes(self):
        """图片不是表格时按协议输出空块 + CONFIDENCE 0.0，应放行。"""
        result = TableOCRResult.model_validate({"latex": "", "confidence": 0.0})
        assert result.latex == ""
        assert result.confidence == 0.0

    def test_empty_latex_high_confidence_rejected(self):
        """空输出却给了非 0 置信度 → 协议不一致，拒绝并触发重试。"""
        with pytest.raises(ValidationError, match="empty latex"):
            TableOCRResult.model_validate({"latex": "", "confidence": 0.6})

    def test_rejects_unclosed_environment(self):
        latex = r"\begin{table}[htbp]\begin{tabular}{cc}A & B\end{tabular}"
        with pytest.raises(ValidationError, match="unclosed"):
            TableOCRResult.model_validate({"latex": latex})

    def test_rejects_mismatched_environment(self):
        latex = (
            r"\begin{table}[htbp]\begin{tabular}{cc}A & B" r"\end{table}\end{tabular}"
        )
        with pytest.raises(ValidationError, match="mismatched"):
            TableOCRResult.model_validate({"latex": latex})


class TestImageValidation:
    """图片格式/大小校验测试。"""

    def test_supported_png(self, service):
        service._validate_content_type("image/png")

    def test_supported_jpeg(self, service):
        service._validate_content_type("image/jpeg")

    def test_supported_webp(self, service):
        service._validate_content_type("image/webp")

    def test_unsupported_format(self, service):
        with pytest.raises(ValueError, match="Unsupported"):
            service._validate_content_type("image/gif")

    def test_empty_content_type(self, service):
        with pytest.raises(ValueError, match="Missing content type"):
            service._validate_content_type("")

    def test_image_too_large(self, service):
        large = b"\x00" * (11 * 1024 * 1024)
        with pytest.raises(ValueError, match="too large"):
            service._preprocess(large)


class TestPreprocess:
    """图片预处理测试。"""

    def test_small_image_unchanged(self, service, minimal_png):
        png = minimal_png
        img = service._preprocess(png)
        assert img.size == (1, 1)

    def test_to_base64(self, service, minimal_png):
        png = minimal_png
        img = service._preprocess(png)
        b64 = service._to_base64(img)
        assert len(b64) > 0
        assert isinstance(b64, str)

    def test_large_image_resized(self, service, monkeypatch):
        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.table_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (3000, 100), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        res = service._preprocess(buf.read())
        assert res.size[0] <= 2000


class TestRecognize:
    """分隔符协议识别测试（不再使用 JSON 输出）。"""

    @pytest.mark.asyncio
    async def test_successful_recognition(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response(TABLE_LATEX)

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert "table" in result["latex"]
        assert result["confidence"] == 0.95
        assert "low_confidence" not in result

    @pytest.mark.asyncio
    async def test_low_confidence(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response(
            r"\begin{table}[htbp]\end{table}", confidence="0.4"
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.4
        assert result["low_confidence"] is True

    @pytest.mark.asyncio
    async def test_not_a_table_returns_empty(self, mock_llm, service, minimal_png):
        """非表格图片 → 空 LaTeX 块 + CONFIDENCE 0.0 → 优雅返回（不重试）。"""
        mock_llm.chat.return_value = ocr_response("", confidence="0.0")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == ""
        assert result["confidence"] == 0.0
        assert result["low_confidence"] is True
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_confidence_inside_latex_block_ignored(
        self, mock_llm, service, minimal_png
    ):
        """LaTeX 单元格内容里的 'CONFIDENCE: x' 不应被当成元数据解析。"""
        latex = TABLE_LATEX.replace(
            r"\caption{Results}", r"\caption{Results CONFIDENCE: 0.99}"
        )
        mock_llm.chat.return_value = ocr_response(latex, confidence="0.9")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_missing_metadata_defaults(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = f"<<<LATEX>>>\n{TABLE_LATEX}\n<<<END>>>"

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_block_retries_then_recovers(
        self, mock_llm, service, minimal_png
    ):
        mock_llm.chat.side_effect = [
            "I cannot see the table clearly.",
            ocr_response(TABLE_LATEX, confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert "table" in result["latex"]
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_control_char_retries(self, mock_llm, service, minimal_png):
        """模拟旧的 JSON 转义损坏（\\begin 变成退格 + 'egin'），应触发重试并恢复。"""
        bad = "\b" + r"egin{table}[htbp]\end{table}"
        mock_llm.chat.side_effect = [
            ocr_response(bad),
            ocr_response(TABLE_LATEX, confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert "table" in result["latex"]
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_garbage_placeholder_retries(self, mock_llm, service, minimal_png):
        bad = (
            r"\begin{table}[htbp]\centering"
            r"\begin{tabular}{c}[object Object]\end{tabular}\end{table}"
        )
        mock_llm.chat.side_effect = [
            ocr_response(bad),
            ocr_response(TABLE_LATEX, confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert "table" in result["latex"]
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_missing_table_env_retries(self, mock_llm, service, minimal_png):
        """只输出 tabular 而没有 table 浮动体 → 校验失败并重试恢复。"""
        bad = r"\begin{tabular}{cc}A & B\end{tabular}"
        mock_llm.chat.side_effect = [
            ocr_response(bad),
            ocr_response(TABLE_LATEX, confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert r"\begin{table}" in result["latex"]
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = "no block here"

        png = minimal_png
        with pytest.raises(RuntimeError, match="Table recognition failed"):
            await service._recognize_with_llm(mock_llm, png, "image/png")

        assert mock_llm.chat.call_count == 3


class TestRecognizeWithPackageAnalysis:
    """完整流水线测试（第一次分隔符协议识别 + 第二次宏包分析）。"""

    @pytest.fixture(autouse=True)
    def _mock_get_llm_client(self, mock_llm, monkeypatch):
        """将 get_llm_client() 返回 mock_llm，避免调用真实 LLM。"""
        monkeypatch.setattr(
            "backend.modules.table_ocr.service.get_llm_client",
            lambda: mock_llm,
        )

    @pytest.mark.asyncio
    async def test_with_packages_detected(self, mock_llm, service, monkeypatch):
        """第一次返回表格代码，第二次返回宏包列表 → 结果含注释。"""
        mock_llm.chat.side_effect = [
            ocr_response(TABLE_LATEX),
            {"packages": ["booktabs"]},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.table_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.95
        assert r"% \usepackage{booktabs}" in result["latex"]
        assert r"\toprule" in result["latex"]
        latex_part = result["latex"].split("\n")
        assert latex_part[0] == r"% \usepackage{booktabs}"

    @pytest.mark.asyncio
    async def test_without_packages(self, mock_llm, service, monkeypatch):
        """第一次返回表格代码，第二次返回空列表 → 无注释。"""
        hline_latex = TABLE_LATEX.replace(r"\toprule", r"\hline").replace(
            r"\midrule", r"\hline"
        )
        mock_llm.chat.side_effect = [
            ocr_response(hline_latex, confidence="0.90"),
            {"packages": []},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.table_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.90
        assert not result["latex"].startswith(r"%")

    @pytest.mark.asyncio
    async def test_package_analysis_fails_gracefully(
        self, mock_llm, service, monkeypatch
    ):
        """第二次 LLM 调用失败 → 返回原始表格代码（优雅降级）。"""
        mock_llm.chat.side_effect = [
            ocr_response(TABLE_LATEX, confidence="0.92"),
            RuntimeError("API error"),
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.table_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.92
        assert r"\toprule" in result["latex"]
        assert not result["latex"].startswith(r"%")

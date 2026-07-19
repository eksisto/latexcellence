"""
table_ocr 模块集成测试。
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.table_ocr.service import (
    TableOCRService,
    TableOCRResult,
)


@pytest.fixture
def service():
    return TableOCRService()


class TestTableOCRResultModel:
    """TableOCRResult 模型校验测试。"""

    def test_valid(self):
        data = {"latex": r"\begin{table}[htbp]\centering\begin{tabular}{cc}\toprule A & B \\\midrule 1 & 2 \\\bottomrule\end{tabular}\end{table}", "confidence": 0.95}
        result = TableOCRResult.model_validate(data)
        assert result.confidence == 0.95

    def test_defaults(self):
        data = {"latex": r"\begin{table}[htbp]\end{table}"}
        result = TableOCRResult.model_validate(data)
        assert result.confidence == 0.0

    def test_missing_latex(self):
        with pytest.raises(ValidationError):
            TableOCRResult.model_validate({"confidence": 0.9})

    def test_wrong_confidence_type(self):
        with pytest.raises(ValidationError):
            TableOCRResult.model_validate({
                "latex": r"\begin{table}[htbp]\end{table}",
                "confidence": "high",
            })


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
    """LLM 调用测试。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_successful_recognition(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = {
            "latex": r"\begin{table}[htbp]\centering\caption{Results}\begin{tabular}{lr}\toprule Name & Value \\\midrule A & 10 \\\bottomrule\end{tabular}\end{table}",
            "confidence": 0.95,
        }

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert "table" in result["latex"]
        assert result["confidence"] == 0.95
        assert "low_confidence" not in result

    @pytest.mark.asyncio
    async def test_low_confidence(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = {
            "latex": r"\begin{table}[htbp]\end{table}",
            "confidence": 0.4,
        }

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.4
        assert result["low_confidence"] is True

    @pytest.mark.asyncio
    async def test_pydantic_retry_exhausted(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = {"bad": True}

        png = minimal_png
        with pytest.raises(RuntimeError, match="Table recognition failed"):
            await service._recognize_with_llm(mock_llm, png, "image/png")

        assert mock_llm.chat.call_count == 3

"""
formula_ocr 模块集成测试。
"""

import io
import struct
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.formula_ocr.service import (
    FormulaOCRService,
    FormulaOCRResult,
)
from backend.core.config import load_config, get_config


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
def service():
    return FormulaOCRService()


def _make_minimal_png():
    """生成最小有效 PNG 图片（1x1 红色像素）。"""
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class TestFormulaOCRResultModel:
    """FormulaOCRResult 模型校验测试。"""

    def test_valid(self):
        data = {"latex": r"\frac{a}{b}", "inline": False, "confidence": 0.95}
        result = FormulaOCRResult.model_validate(data)
        assert result.latex == r"\frac{a}{b}"
        assert result.inline is False
        assert result.confidence == 0.95

    def test_defaults(self):
        data = {"latex": r"x^2"}
        result = FormulaOCRResult.model_validate(data)
        assert result.inline is False
        assert result.confidence == 0.0

    def test_missing_latex(self):
        with pytest.raises(ValidationError):
            FormulaOCRResult.model_validate({"inline": True})

    def test_wrong_confidence_type(self):
        with pytest.raises(ValidationError):
            FormulaOCRResult.model_validate({
                "latex": "x",
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

    def test_small_image_unchanged(self, service):
        png = _make_minimal_png()
        img = service._preprocess(png)
        assert img.size == (1, 1)

    def test_to_base64(self, service):
        png = _make_minimal_png()
        img = service._preprocess(png)
        b64 = service._to_base64(img)
        assert len(b64) > 0
        assert isinstance(b64, str)

    def test_large_image_resized(self, service, monkeypatch):
        import backend.core.config as cfg_mod
        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

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
    async def test_successful_recognition(self, mock_llm, service):
        mock_llm.chat.return_value = {
            "latex": r"\int_0^\infty e^{-x^2}\,dx",
            "inline": False,
            "confidence": 0.95,
        }

        png = _make_minimal_png()
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\int_0^\infty e^{-x^2}\,dx"
        assert result["inline"] is False
        assert result["confidence"] == 0.95
        assert "low_confidence" not in result

    @pytest.mark.asyncio
    async def test_low_confidence(self, mock_llm, service):
        mock_llm.chat.return_value = {
            "latex": r"??",
            "inline": True,
            "confidence": 0.4,
        }

        png = _make_minimal_png()
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.4
        assert result["low_confidence"] is True

    @pytest.mark.asyncio
    async def test_pydantic_retry_exhausted(self, mock_llm, service):
        mock_llm.chat.return_value = {"bad": True}

        png = _make_minimal_png()
        with pytest.raises(RuntimeError, match="Failed to"):
            await service._recognize_with_llm(mock_llm, png, "image/png")

        assert mock_llm.chat.call_count == 3

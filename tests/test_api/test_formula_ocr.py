"""
formula_ocr 模块集成测试。
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.formula_ocr.service import (
    FormulaOCRResult,
    FormulaOCRService,
)


def ocr_response(latex, inline="false", confidence="0.95"):
    """构造符合 <<<LATEX>>> 分隔符协议的模型原始输出。"""
    return (
        f"<<<LATEX>>>\n{latex}\n<<<END>>>\n"
        f"INLINE: {inline}\nCONFIDENCE: {confidence}"
    )


@pytest.fixture
def service():
    return FormulaOCRService()


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
            FormulaOCRResult.model_validate(
                {
                    "latex": "x",
                    "confidence": "high",
                }
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            FormulaOCRResult.model_validate({"latex": "x", "confidence": 1.5})

    def test_rejects_control_char(self):
        """\rho 经 JSON 转义损坏后变成回车 + 'ho'，必须被内容校验器拦截。"""
        latex = "R^{" + "\r" + "ho}"
        with pytest.raises(ValidationError, match="control character"):
            FormulaOCRResult.model_validate({"latex": latex})

    def test_rejects_garbage_placeholder(self):
        with pytest.raises(ValidationError, match="garbage"):
            FormulaOCRResult.model_validate({"latex": r"\sqrt[[object Object]]{x}"})

    def test_rejects_unbalanced_braces(self):
        with pytest.raises(ValidationError, match="unbalanced braces"):
            FormulaOCRResult.model_validate({"latex": r"\frac{a}{b"})

    def test_accepts_escaped_braces(self):
        result = FormulaOCRResult.model_validate({"latex": r"\{x \mid x>0\}"})
        assert result.latex == r"\{x \mid x>0\}"

    def test_rejects_unbalanced_dollars(self):
        with pytest.raises(ValidationError, match="unbalanced dollar"):
            FormulaOCRResult.model_validate({"latex": "x = $a$ and $b"})


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
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (3000, 100), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        res = service._preprocess(buf.read())
        assert res.size[0] <= 2000


class TestPasteWrapping:
    """确定性包裹测试：复制结果即贴即显。"""

    def test_display_bare_wrapped_in_brackets(self):
        assert FormulaOCRService._wrap_latex_for_paste(r"x^2", False) == r"\[x^2\]"

    def test_inline_bare_wrapped_in_dollars(self):
        assert FormulaOCRService._wrap_latex_for_paste(r"x^2", True) == r"$x^2$"

    def test_standalone_display_env_kept(self):
        latex = r"\begin{align*} a &= b \\ c &= d \end{align*}"
        assert FormulaOCRService._wrap_latex_for_paste(latex, False) == latex

    def test_inner_env_wrapped(self):
        latex = r"\begin{aligned} a &= b \\ c &= d \end{aligned}"
        assert (
            FormulaOCRService._wrap_latex_for_paste(latex, False)
            == r"\[\begin{aligned} a &= b \\ c &= d \end{aligned}\]"
        )

    def test_empty_unchanged(self):
        assert FormulaOCRService._wrap_latex_for_paste("", False) == ""


class TestRecognize:
    """分隔符协议识别测试（不再使用 JSON 输出）。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_successful_recognition(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response(r"\int_0^\infty e^{-x^2}\,dx")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\int_0^\infty e^{-x^2}\,dx"
        assert result["inline"] is False
        assert result["confidence"] == 0.95
        assert "low_confidence" not in result

    @pytest.mark.asyncio
    async def test_inline_true(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response(
            "x^2", inline="true", confidence="0.9"
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["inline"] is True

    @pytest.mark.asyncio
    async def test_low_confidence(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response("??", confidence="0.4")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.4
        assert result["low_confidence"] is True

    @pytest.mark.asyncio
    async def test_missing_metadata_defaults(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = "<<<LATEX>>>\nx^2\n<<<END>>>"

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["inline"] is False
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_strips_display_dollar_wrapper(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response("$$x^2$$")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == "x^2"

    @pytest.mark.asyncio
    async def test_strips_bracket_wrapper(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response(r"\[x^2\]")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == "x^2"

    @pytest.mark.asyncio
    async def test_missing_block_retries_then_recovers(
        self, mock_llm, service, minimal_png
    ):
        mock_llm.chat.side_effect = [
            "I cannot see the formula clearly.",
            ocr_response("x^2", confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == "x^2"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_control_char_retries(self, mock_llm, service, minimal_png):
        """模拟旧的 JSON 转义损坏（\rho 变成回车 + 'ho'），应触发重试并恢复。"""
        bad = "R^{" + "\r" + "ho}"
        mock_llm.chat.side_effect = [
            ocr_response(bad),
            ocr_response(r"R^{\rho}", confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"R^{\rho}"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_garbage_placeholder_retries(self, mock_llm, service, minimal_png):
        mock_llm.chat.side_effect = [
            ocr_response(r"\sqrt[[object Object]]{x}"),
            ocr_response(r"\sqrt[3]{x}", confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\sqrt[3]{x}"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = "no block here"

        png = minimal_png
        with pytest.raises(RuntimeError, match="Formula recognition failed"):
            await service._recognize_with_llm(mock_llm, png, "image/png")

        assert mock_llm.chat.call_count == 3


class TestRecognizeWithPackageAnalysis:
    """完整流水线测试（第一次分隔符协议识别 + 第二次宏包分析）。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.fixture(autouse=True)
    def _mock_get_llm_client(self, mock_llm, monkeypatch):
        monkeypatch.setattr(
            "backend.modules.formula_ocr.service.get_llm_client",
            lambda: mock_llm,
        )

    @pytest.mark.asyncio
    async def test_with_packages_detected(self, mock_llm, service, monkeypatch):
        """第一次返回公式代码，第二次返回宏包列表 → 结果含注释。"""
        mock_llm.chat.side_effect = [
            ocr_response(r"\int_0^\infty e^{-x^2}\,dx"),
            {"packages": ["amsmath"]},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.95
        assert r"% \usepackage{amsmath}" in result["latex"]
        assert r"\int" in result["latex"]
        latex_part = result["latex"].split("\n")
        assert latex_part[0] == r"% \usepackage{amsmath}"

    @pytest.mark.asyncio
    async def test_without_packages(self, mock_llm, service, monkeypatch):
        """第一次返回公式代码，第二次返回空列表 → 无注释。"""
        mock_llm.chat.side_effect = [
            ocr_response("x^2 + y^2 = z^2", inline="true", confidence="0.90"),
            {"packages": []},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.90
        assert not result["latex"].startswith("%")

    @pytest.mark.asyncio
    async def test_package_analysis_fails_gracefully(
        self, mock_llm, service, monkeypatch
    ):
        """第二次 LLM 调用失败 → 返回原始公式代码（优雅降级）。"""
        mock_llm.chat.side_effect = [
            ocr_response(r"\frac{a}{b}", confidence="0.92"),
            RuntimeError("API error"),
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["confidence"] == 0.92
        assert r"\frac" in result["latex"]
        assert not result["latex"].startswith("%")

    @pytest.mark.asyncio
    async def test_recognize_returns_paste_ready_latex(
        self, mock_llm, service, monkeypatch
    ):
        """完整流水线：展示公式返回 \\[...\\] 包裹，复制即贴即显。"""
        mock_llm.chat.side_effect = [
            ocr_response("x^2 + y^2 = z^2"),
            {"packages": []},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        from PIL import Image

        img = Image.new("RGB", (100, 50), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result = await service.recognize(buf.read(), "image/png")

        assert result["latex"] == r"\[x^2 + y^2 = z^2\]"

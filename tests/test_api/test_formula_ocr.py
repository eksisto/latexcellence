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
        """\\begin 经 JSON 转义损坏后变成退格 + 'egin'；宽松模式仍拦截此类真正损坏。"""
        latex = "\b" + r"egin{matrix}x\end{matrix}"
        with pytest.raises(ValidationError, match="control character"):
            FormulaOCRResult.model_validate({"latex": latex})

    def test_accepts_crlf_and_tab(self):
        """分隔符协议（非 JSON）路径放行合法空白 \\t / \\r（宽松模式）。"""
        latex = "R^{" + "\t" + "x}" + "\r\n" + "$y$"
        result = FormulaOCRResult.model_validate({"latex": latex})
        assert result.latex == latex

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


class TestInlineReconciliation:
    """inline/display 确定性矫正测试（视觉决策树中可由 LaTeX 结构判定的部分）。"""

    def test_numbered_equation_env_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{equation} E = mc^2 \end{equation}", True
            )
            is False
        )

    def test_tag_is_display(self):
        assert FormulaOCRService._reconcile_inline(r"x = 1 \tag{2.3}", True) is False
        assert FormulaOCRService._reconcile_inline(r"x = 1 \tag*{A}", True) is False

    def test_unstarred_align_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{align} a &= b \end{align}", True
            )
            is False
        )

    def test_display_only_env_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{align*} a &= b \\ c &= d \end{align*}", True
            )
            is False
        )

    def test_multiline_aligned_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{aligned} a &= b \\ c &= d \end{aligned}", True
            )
            is False
        )

    def test_single_line_aligned_keeps_llm_decision(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{aligned} a &= b \end{aligned}", True
            )
            is True
        )
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{aligned} a &= b \end{aligned}", False
            )
            is False
        )

    def test_displaystyle_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(r"\displaystyle \sum_{i=1}^n i", True)
            is False
        )

    def test_textstyle_is_inline(self):
        assert (
            FormulaOCRService._reconcile_inline(r"\textstyle \sum_{i=1}^n i", False)
            is True
        )

    def test_textstyle_does_not_override_numbered_env(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{equation} \textstyle x \end{equation}", True
            )
            is False
        )

    def test_limits_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(r"\sum\limits_{i=1}^n i^2", True)
            is False
        )

    def test_nolimits_is_inline(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\int\nolimits_0^\infty e^{-x}\,dx", False
            )
            is True
        )

    def test_limits_overrides_textstyle(self):
        """\\limits 直接决定上下标的渲染位置，优先于 \\textstyle（以实际渲染为准）。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\textstyle \sum\limits_{i=1}^n i", False
            )
            is False
        )

    def test_nolimits_overrides_displaystyle(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\displaystyle \sum\nolimits_{i=1}^n i", True
            )
            is True
        )

    def test_verbatim_content_not_scanned(self):
        """逐字内容中的 \\limits / \\displaystyle 不应触发矫正。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\verb|\sum\limits_{i=1}^n|", True)
            is True
        )
        assert (
            FormulaOCRService._reconcile_inline(r"\verb|\displaystyle|", True) is True
        )

    def test_text_wrapper_content_not_scanned(self):
        """文本模式包裹（\\text{...}）中的信号不应触发矫正。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\text{\displaystyle x}", True) is True
        )
        assert (
            FormulaOCRService._reconcile_inline(
                r"\text{\begin{equation} x \end{equation}}", True
            )
            is True
        )

    def test_masked_content_does_not_hide_real_signals(self):
        """屏蔽只影响字面内容，真实数学结构仍生效。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\sum\limits_{i=1}^n \text{above} + \verb|x|", True
            )
            is False
        )

    def test_bare_big_op_keeps_llm_decision(self):
        """\\sum_{i=1}^n 无 \\limits/\\nolimits 时源码无法判定，保留模型结论。"""
        assert FormulaOCRService._reconcile_inline(r"\sum_{i=1}^n i^2", True) is True
        assert FormulaOCRService._reconcile_inline(r"\sum_{i=1}^n i^2", False) is False

    def test_plain_formula_keeps_llm_decision(self):
        assert FormulaOCRService._reconcile_inline(r"x^2 + y^2 = z^2", True) is True
        assert FormulaOCRService._reconcile_inline(r"x^2 + y^2 = z^2", False) is False


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
    async def test_numbered_env_overrides_llm_inline(
        self, mock_llm, service, minimal_png
    ):
        """LLM 误判 inline，但 LaTeX 含编号 display 环境 → 矫正为 display。"""
        mock_llm.chat.return_value = ocr_response(
            r"\begin{equation} E = mc^2 \end{equation}",
            inline="true",
            confidence="0.95",
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == "E = mc^2"
        assert result["inline"] is False

    @pytest.mark.asyncio
    async def test_textstyle_overrides_llm_display(
        self, mock_llm, service, minimal_png
    ):
        """LLM 误判 display，但 LaTeX 显式 \\textstyle → 矫正为 inline。"""
        mock_llm.chat.return_value = ocr_response(
            r"\textstyle \sum_{i=1}^n i", inline="false", confidence="0.9"
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\textstyle \sum_{i=1}^n i"
        assert result["inline"] is True

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
        """模拟真正的损坏特征（\\frac 经 JSON 转义损坏后变成换页 + 'rac'），应触发重试并恢复。"""
        bad = "R^{" + "\f" + "rac}"
        mock_llm.chat.side_effect = [
            ocr_response(bad),
            ocr_response(r"R^{\frac{a}{b}}", confidence="0.9"),
        ]

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"R^{\frac{a}{b}}"
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

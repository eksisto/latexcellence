"""
formula_ocr 模块集成测试。
"""

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from PIL import Image

from backend.modules.formula_ocr.visual_inline import (
    VisualInlineHint,
    analyze_formula_image,
)
from backend.modules.formula_ocr.service import (
    FormulaOCRResult,
    FormulaOCRService,
)

_VISUAL_FIXTURES = (
    Path(__file__).resolve().parent.parent / "fixtures" / "formula_ocr" / "visual"
)


def ocr_response(latex, confidence="0.95"):
    """构造符合 <<<LATEX>>> 分隔符协议的模型原始输出。"""
    return f"<<<LATEX>>>\n{latex}\n<<<END>>>\n" f"CONFIDENCE: {confidence}"


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
    """inline/display 确定性判定测试（结构信号 + 视觉判定 + 默认 display）。"""

    def test_numbered_equation_env_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{equation} E = mc^2 \end{equation}"
            )
            is False
        )

    def test_tag_is_display(self):
        assert FormulaOCRService._reconcile_inline(r"x = 1 \tag{2.3}") is False
        assert FormulaOCRService._reconcile_inline(r"x = 1 \tag*{A}") is False

    def test_unstarred_align_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(r"\begin{align} a &= b \end{align}")
            is False
        )

    def test_display_only_env_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{align*} a &= b \\ c &= d \end{align*}"
            )
            is False
        )

    def test_multiline_aligned_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{aligned} a &= b \\ c &= d \end{aligned}"
            )
            is False
        )

    def test_single_line_aligned_default_display(self):
        """单行 aligned（无 \\\\）无结构信号；无视觉时默认 display。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\begin{aligned} a &= b \end{aligned}")
            is False
        )
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{aligned} a &= b \end{aligned}", visual=True
            )
            is True
        )

    def test_displaystyle_is_display(self):
        assert (
            FormulaOCRService._reconcile_inline(r"\displaystyle \sum_{i=1}^n i")
            is False
        )

    def test_textstyle_is_inline(self):
        assert FormulaOCRService._reconcile_inline(r"\textstyle \sum_{i=1}^n i") is True

    def test_textstyle_does_not_override_numbered_env(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{equation} \textstyle x \end{equation}"
            )
            is False
        )

    def test_limits_not_a_structure_signal(self):
        """\\limits 只决定上下标位置，不决定行内/行间：无结构且无视觉时默认 display。"""
        assert FormulaOCRService._reconcile_inline(r"\sum\limits_{i=1}^n i^2") is False

    def test_integral_limits_not_a_structure_signal(self):
        """积分上下限（\\int\\limits / \\int\\nolimits）不构成结构信号。"""
        assert FormulaOCRService._reconcile_inline(r"\int\limits_a^b f(x)\,dx") is False
        assert (
            FormulaOCRService._reconcile_inline(r"\int\nolimits_0^\infty e^{-x}\,dx")
            is False
        )

    def test_textstyle_wins_over_limits(self):
        """显式 \\textstyle 是唯一的样式信号：即使带 \\limits 也判 inline。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\textstyle \sum\limits_{i=1}^n i")
            is True
        )

    def test_displaystyle_wins_over_nolimits(self):
        assert (
            FormulaOCRService._reconcile_inline(
                r"\displaystyle \sum\nolimits_{i=1}^n i"
            )
            is False
        )

    def test_verbatim_content_not_scanned(self):
        """逐字内容中的 \\limits / \\displaystyle 不应触发矫正。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\verb|\sum\limits_{i=1}^n|", visual=True
            )
            is True
        )
        assert (
            FormulaOCRService._reconcile_inline(r"\verb|\displaystyle|", visual=True)
            is True
        )

    def test_text_wrapper_content_not_scanned(self):
        """文本模式包裹（\\text{...}）中的信号不应触发矫正。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\text{\displaystyle x}", visual=True)
            is True
        )
        assert (
            FormulaOCRService._reconcile_inline(
                r"\text{\begin{equation} x \end{equation}}", visual=True
            )
            is True
        )

    def test_masked_content_does_not_hide_real_signals(self):
        """屏蔽只影响字面内容，真实数学结构仍生效。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\displaystyle \sum_{i=1}^n \text{above} + \verb|x|"
            )
            is False
        )

    def test_bare_big_op_default_display(self):
        """\\sum_{i=1}^n 无 \\limits/\\nolimits 时源码无法判定；无视觉则默认 display。"""
        assert FormulaOCRService._reconcile_inline(r"\sum_{i=1}^n i^2") is False

    def test_plain_formula_default_display(self):
        """无任何结构信号的普通公式默认 display。"""
        assert FormulaOCRService._reconcile_inline(r"x^2 + y^2 = z^2") is False

    def test_visual_hint_used_when_no_structure(self):
        """无结构信号时，视觉判定（图片中的上下标位置）直接生效。"""
        assert (
            FormulaOCRService._reconcile_inline(r"\sum_{i=1}^n x_i", visual=True)
            is True
        )
        assert (
            FormulaOCRService._reconcile_inline(r"\sum_{i=1}^n x_i", visual=False)
            is False
        )

    def test_visual_hint_does_not_override_display_structure(self):
        """编号环境 / \\displaystyle 等结构信号优先于视觉判定。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\begin{equation} \sum_{i=1}^n x_i \end{equation}", visual=True
            )
            is False
        )
        assert (
            FormulaOCRService._reconcile_inline(
                r"\displaystyle \sum_{i=1}^n x_i", visual=True
            )
            is False
        )

    def test_visual_hint_does_not_override_textstyle(self):
        """显式 \\textstyle 是作者意图，视觉判定不得反向覆盖。"""
        assert (
            FormulaOCRService._reconcile_inline(
                r"\textstyle \sum_{i=1}^n x_i", visual=False
            )
            is True
        )


class TestVisualInlineAnalysis:
    """输入图片的确定性行内/行间视觉判定（固定语料图，不依赖 TeX）。"""

    def _hint(self, name):
        return analyze_formula_image(Image.open(_VISUAL_FIXTURES / f"{name}.png"))

    def test_sum_inline_detected(self):
        """\\sum 上下标在右侧（行内样式）→ 判行内。"""
        hint = self._hint("sum_inline")
        assert hint.inline is True
        assert "side-pair" in hint.reason

    def test_sum_display_detected(self):
        """\\sum 上下标在正上下方（行间样式）→ 判行间。"""
        hint = self._hint("sum_display")
        assert hint.inline is False
        assert "above-below" in hint.reason

    def test_integral_inline_detected(self):
        """文本样式 \\int（宽高比约 2.0）且上下限在右侧 → 判行内。"""
        hint = self._hint("int_inline")
        assert hint.inline is True
        assert "side+aspect" in hint.reason

    def test_integral_display_neutral(self):
        """显示样式 \\int 默认上下限也在右侧（宽高比约 2.5），证据不足不投票。"""
        hint = self._hint("int_display")
        assert hint.inline is None

    def test_limit_inline_detected(self):
        """\\lim 的条件在右下方 → 判行内。"""
        hint = self._hint("lim_inline")
        assert hint.inline is True
        assert "side-lower" in hint.reason

    def test_limit_display_neutral(self):
        """行间 \\lim 的字形不构成足够证据时保持中立。"""
        hint = self._hint("lim_display")
        assert hint.inline is None

    def test_plain_formula_neutral(self):
        """无大型算子的公式（如勾股定理）不投票，交给模型判定。"""
        hint = self._hint("plain_inline")
        assert hint.inline is None


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
        assert result["inline_source"] == "default"
        assert "low_confidence" not in result

    @pytest.mark.asyncio
    async def test_model_inline_true_ignored_default_display(
        self, mock_llm, service, minimal_png
    ):
        """模型不再提供 INLINE 判定：无结构且无视觉时默认 display。"""
        mock_llm.chat.return_value = ocr_response("x^2", confidence="0.9")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["inline"] is False
        assert result["inline_source"] == "default"

    @pytest.mark.asyncio
    async def test_inline_sum_with_limits_not_forced_display(
        self, mock_llm, service, minimal_png
    ):
        """回归：含 \\limits 的求和公式不被结构规则误判为 display；
        无视觉判定时按默认值返回 display。"""
        mock_llm.chat.return_value = ocr_response(
            r"\sum\limits_{i=1}^n x_i", confidence="0.9"
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\sum\limits_{i=1}^n x_i"
        assert result["inline"] is False
        assert result["inline_source"] == "default"

    @pytest.mark.asyncio
    async def test_low_confidence(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = ocr_response("??", confidence="0.4")

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["confidence"] == 0.4
        assert result["low_confidence"] is True
        assert result["inline_source"] == "default"

    @pytest.mark.asyncio
    async def test_missing_metadata_defaults(self, mock_llm, service, minimal_png):
        mock_llm.chat.return_value = "<<<LATEX>>>\nx^2\n<<<END>>>"

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["inline"] is False
        assert result["confidence"] == 0.0
        assert result["inline_source"] == "default"

    @pytest.mark.asyncio
    async def test_numbered_env_is_display(self, mock_llm, service, minimal_png):
        """LaTeX 含编号 display 环境 → 结构信号判定为 display。"""
        mock_llm.chat.return_value = ocr_response(
            r"\begin{equation} E = mc^2 \end{equation}",
            confidence="0.95",
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == "E = mc^2"
        assert result["inline"] is False
        assert result["inline_source"] == "structure"

    @pytest.mark.asyncio
    async def test_textstyle_forces_inline(self, mock_llm, service, minimal_png):
        """LaTeX 显式 \\textstyle → 结构信号判定为 inline。"""
        mock_llm.chat.return_value = ocr_response(
            r"\textstyle \sum_{i=1}^n i", confidence="0.9"
        )

        png = minimal_png
        result = await service._recognize_with_llm(mock_llm, png, "image/png")

        assert result["latex"] == r"\textstyle \sum_{i=1}^n i"
        assert result["inline"] is True
        assert result["inline_source"] == "structure"

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
            ocr_response("x^2 + y^2 = z^2", confidence="0.90"),
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


class TestVisualHintMerge:
    """视觉判定与结构判定的合并流程。"""

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
    async def test_visual_hint_decides_inline_when_no_structure(
        self, mock_llm, service, minimal_png
    ):
        """无结构信号时，视觉判定（上下标在右侧）直接决定 inline。"""
        mock_llm.chat.return_value = ocr_response(
            r"\sum_{i=1}^n x_i", confidence="0.95"
        )
        hint = VisualInlineHint(inline=True, confidence=0.8, reason="side-pair")

        result = await service._recognize_with_llm(
            mock_llm, minimal_png, "image/png", visual_hint=hint
        )

        assert result["latex"] == r"\sum_{i=1}^n x_i"
        assert result["inline"] is True
        assert result["inline_source"] == "visual"

    @pytest.mark.asyncio
    async def test_display_structure_wins_over_visual(
        self, mock_llm, service, minimal_png
    ):
        """编号环境是最强信号，即使视觉说行内也保持行间。"""
        mock_llm.chat.return_value = ocr_response(
            r"\begin{equation} \sum_{i=1}^n x_i \end{equation}",
            confidence="0.95",
        )
        hint = VisualInlineHint(inline=True, confidence=0.8, reason="side-pair")

        result = await service._recognize_with_llm(
            mock_llm, minimal_png, "image/png", visual_hint=hint
        )

        assert result["inline"] is False
        assert result["inline_source"] == "structure"

    @pytest.mark.asyncio
    async def test_undecided_visual_defaults_to_display(
        self, mock_llm, service, minimal_png
    ):
        """视觉证据不足（None）且无结构信号时，默认 display。"""
        mock_llm.chat.return_value = ocr_response(
            r"\int_0^1 f(x)\,dx", confidence="0.95"
        )
        hint = VisualInlineHint(inline=None, confidence=0.0, reason="none")

        result = await service._recognize_with_llm(
            mock_llm, minimal_png, "image/png", visual_hint=hint
        )

        assert result["inline"] is False
        assert result["inline_source"] == "default"

    @pytest.mark.asyncio
    async def test_visual_hint_decides_inline_for_limits_formula(
        self, mock_llm, service, minimal_png
    ):
        """含 \\limits 的公式不受结构规则干扰，视觉判定直接生效。"""
        mock_llm.chat.return_value = ocr_response(
            r"\sum\limits_{i=1}^n x_i", confidence="0.9"
        )
        hint = VisualInlineHint(inline=True, confidence=0.8, reason="side-pair")

        result = await service._recognize_with_llm(
            mock_llm, minimal_png, "image/png", visual_hint=hint
        )

        assert result["latex"] == r"\sum\limits_{i=1}^n x_i"
        assert result["inline"] is True
        assert result["inline_source"] == "visual"

    @pytest.mark.asyncio
    async def test_recognize_pipeline_flips_sum_to_inline(
        self, mock_llm, service, monkeypatch
    ):
        """端到端：模型误报 display 的行内求和，经视觉分析后以 $...$ 返回。"""
        mock_llm.chat.side_effect = [
            ocr_response(r"\sum_{i=1}^n x_i", confidence="0.95"),
            {"packages": []},
        ]

        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.features.formula_ocr, "max_image_size_mb", 100)

        png_bytes = (_VISUAL_FIXTURES / "sum_inline.png").read_bytes()
        result = await service.recognize(png_bytes, "image/png")

        assert result["inline"] is True
        assert result["inline_source"] == "visual"
        assert result["latex"].startswith("$")
        assert result["latex"].endswith("$")
        assert r"\sum" in result["latex"]

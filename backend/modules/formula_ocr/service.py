"""
公式识别业务逻辑。

流程：
    1. 校验图片格式（PNG/JPEG/WebP）和大小
    2. 预处理：超过 2000px 的边等比缩放到 2000px
    3. 转为 base64 发送给 LLM Vision API（第一次 LLM 调用）
    4. 模型按分隔符协议输出 LaTeX（<<<LATEX>>>...<<<END>>> + INLINE/CONFIDENCE），
       后端提取并做内容校验（控制字符 / 垃圾占位符 / 花括号与 $ 配平）
    5. 调用 LaTeX 宏包分析器（第二次 LLM 调用），将宏包注释前置后返回

设计说明：
    LaTeX 不放在 JSON 字符串中传输——JSON 会把未转义的 \\rho 解释为回车、
    \\nu 解释为换行（静默损坏），且 \\sigma 等非法转义会直接解析失败。
    改用分隔符协议让反斜杠原样传输，并用内容校验器拦截损坏输出。
"""

import base64
import io
import re
from typing import Any

from PIL import Image
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.core.config import get_config
from backend.core.llm_client import get_llm_client
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.json_utils import format_validation_error
from backend.utils.latex_json_guard import validate_latex_field
from backend.utils.latex_literals import extract_delimited_block
from backend.utils.latex_package_analyzer import prepend_package_comments

# 分隔符协议：LaTeX 以纯文本形式夹在标记之间，避免 JSON 反斜杠转义损坏
_INLINE_RE = re.compile(r"INLINE\s*:\s*(true|false)", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"CONFIDENCE\s*:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)

# 自带显示模式的顶层环境：复制时不再额外包裹（aligned/cases/matrix 等内层环境仍需包裹）
_STANDALONE_DISPLAY_ENV_RE = re.compile(
    r"^\s*\\begin\{(equation|equation\*|displaymath|align|align\*|alignat|alignat\*|"
    r"flalign|flalign\*|gather|gather\*|multline|multline\*|eqnarray|eqnarray\*)\}"
)

# 仅剥离"空壳显示环境"（不携带公式结构），结构型环境（aligned/cases/matrix/align* 等）保留
_WRAPPER_ENVS = (
    (r"\begin{equation*}", r"\end{equation*}"),
    (r"\begin{equation}", r"\end{equation}"),
    (r"\begin{displaymath}", r"\end{displaymath}"),
)

_MAX_OCR_RETRIES = 3
_MAX_DIMENSION = 2000
_SUPPORTED_FORMATS = {"image/png", "image/jpeg", "image/webp"}


class FormulaOCRResult(BaseModel):
    """公式识别输出（后端解析分隔符协议后构造，模型不再直接输出 JSON）。"""

    latex: str
    inline: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("latex")
    @classmethod
    def _validate_latex_content(cls, value: str) -> str:
        """内容校验器（非 JSON 宽松模式，放行 \\t / \\r）：拦截控制字符损坏、
        垃圾占位符、花括号与 $ 不配平（共享实现）。"""
        return validate_latex_field(value, relaxed=True)


class FormulaOCRService:
    """公式识别服务。"""

    def __init__(self):
        self._logger = get_logger("formula_ocr")

    async def recognize(self, image_data: bytes, content_type: str) -> dict[str, Any]:
        """
        识别图片中的 LaTeX 公式。

        参数：
            image_data: 图片二进制数据
            content_type: MIME 类型

        返回：
            包含 latex / inline / confidence 的 dict
        """
        self._validate_content_type(content_type)

        img = self._preprocess(image_data)
        base64_image = self._to_base64(img)

        cfg = get_config()
        if not cfg.llm.api_key:
            self._logger.warning("No API key configured, returning empty result")
            return {
                "latex": "",
                "inline": False,
                "confidence": 0.0,
                "low_confidence": True,
            }

        llm = get_llm_client()
        result = await self._recognize_with_llm(llm, base64_image, content_type)

        if result.get("latex"):
            # 确定性包裹：让复制结果即贴即显（模型自身加包裹不可靠，故由后端统一处理）
            result["latex"] = self._wrap_latex_for_paste(
                result["latex"], result.get("inline", False)
            )
            # 第二步：分析 LaTeX 代码所需的宏包（math 领域：排除表格/版式类宏包）
            result["latex"] = await prepend_package_comments(
                llm, result["latex"], domain="math"
            )

        return result

    async def _recognize_with_llm(
        self, llm, base64_image: str, content_type: str
    ) -> dict[str, Any]:
        """按分隔符协议调用 Vision 模型，提取并校验 LaTeX，失败时回喂重试。"""
        messages = [
            {"role": "system", "content": get_prompt("formula_ocr", "vision_system")},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Convert this formula image to LaTeX:",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{content_type};base64,{base64_image}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ]

        last_error: str | None = None
        for attempt in range(_MAX_OCR_RETRIES):
            try:
                response_text = await llm.chat(
                    messages,
                    temperature=0.1,
                    max_tokens=4096,
                )
                latex = self._extract_latex_block(response_text)
                if latex is None:
                    raise ValueError(
                        "Response did not contain a <<<LATEX>>>...<<<END>>> block. "
                        "Output the LaTeX code strictly between those two markers."
                    )
                inline, confidence = self._parse_metadata(response_text)
                validated = FormulaOCRResult(
                    latex=latex, inline=inline, confidence=confidence
                )
                result = validated.model_dump()

                # 置信度阈值处理
                if validated.confidence < 0.7:
                    result["low_confidence"] = True

                return result

            except ValidationError as e:
                last_error = format_validation_error(e.errors())
            except ValueError as e:
                last_error = str(e)
            except Exception as e:
                self._logger.error(f"Formula OCR error: {e}")
                raise RuntimeError(f"Formula recognition failed: {e}")

            self._logger.warning(
                f"Formula OCR protocol/validation failed on attempt "
                f"{attempt + 1}/{_MAX_OCR_RETRIES}: {last_error}"
            )
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed the output protocol. "
                        f"Error: {last_error}\n\n"
                        "Please re-read the formula image and respond with exactly:\n"
                        "<<<LATEX>>>\n<LaTeX code>\n<<<END>>>\n"
                        "INLINE: <true|false>\n"
                        "CONFIDENCE: <float between 0 and 1>"
                    ),
                }
            ]

        raise RuntimeError(
            f"Formula recognition failed after {_MAX_OCR_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    def _extract_latex_block(self, response_text: str) -> str | None:
        """提取 <<<LATEX>>>...<<<END>>> 块，并剥离误加的数学包裹。

        逐字内容（\\verb / \\lstinline 等）中的 <<<END>>> 不是协议结束，
        提取由 latex_literals.extract_delimited_block 统一处理。
        """
        block = extract_delimited_block(response_text, "<<<LATEX>>>", "<<<END>>>")
        if block is None:
            return None
        return self._strip_math_wrappers(block.strip())

    @staticmethod
    def _strip_math_wrappers(latex: str) -> str:
        """去除模型误加的显示模式包裹（$$ / \\[ \\] / \\( \\) / $ / equation 等）。"""
        s = latex.strip()
        if len(s) >= 4 and s.startswith("$$") and s.endswith("$$"):
            s = s[2:-2].strip()
        if len(s) >= 4 and s.startswith(r"\[") and s.endswith(r"\]"):
            s = s[2:-2].strip()
        if len(s) >= 4 and s.startswith(r"\(") and s.endswith(r"\)"):
            s = s[2:-2].strip()
        if len(s) >= 2 and s.startswith("$") and s.endswith("$"):
            s = s[1:-1].strip()
        for begin, end in _WRAPPER_ENVS:
            if (
                len(s) >= len(begin) + len(end)
                and s.startswith(begin)
                and s.endswith(end)
            ):
                s = s[len(begin) : -len(end)].strip()
                break
        return s

    @staticmethod
    def _parse_metadata(response_text: str) -> tuple[bool, float]:
        """解析 INLINE / CONFIDENCE 元数据行，缺省时返回 (False, 0.0)。"""
        inline = False
        confidence = 0.0
        inline_match = _INLINE_RE.search(response_text)
        if inline_match:
            inline = inline_match.group(1).lower() == "true"
        conf_match = _CONFIDENCE_RE.search(response_text)
        if conf_match:
            confidence = float(conf_match.group(1))
        return inline, confidence

    @staticmethod
    def _wrap_latex_for_paste(latex: str, inline: bool) -> str:
        """按 inline 标志给公式加确定性包裹，复制结果可直接粘贴显示。

        - 已是完整显示环境（align*/gather*/multline* 等）→ 原样保留
        - 行内公式 → $...$
        - 展示公式 → \\[...\\]
        """
        s = latex.strip()
        if not s:
            return s
        if _STANDALONE_DISPLAY_ENV_RE.match(s):
            return s
        if inline:
            return f"${s}$"
        return rf"\[{s}\]"

    def _validate_content_type(self, content_type: str) -> None:
        """校验图片格式。"""
        if not content_type:
            raise ValueError("Missing content type")
        mime_type = content_type.lower()
        if mime_type not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported image format: {content_type}. "
                f"Supported: PNG, JPEG, WebP"
            )

    def _preprocess(self, image_data: bytes) -> Image.Image:
        """预处理图片：校验大小，超过 MAX_DIMENSION 时等比缩放。"""
        cfg = get_config()
        max_size_mb = cfg.features.formula_ocr.max_image_size_mb
        max_size_bytes = max_size_mb * 1024 * 1024

        if len(image_data) > max_size_bytes:
            raise ValueError(
                f"Image too large: {len(image_data) / 1024 / 1024:.1f}MB. "
                f"Maximum: {max_size_mb}MB"
            )

        img = Image.open(io.BytesIO(image_data))
        original_size = img.size

        width, height = img.size
        if width > _MAX_DIMENSION or height > _MAX_DIMENSION:
            ratio = min(_MAX_DIMENSION / width, _MAX_DIMENSION / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
            self._logger.info(f"Resized image: {original_size} -> {img.size}")

        return img

    def _to_base64(self, img: Image.Image) -> str:
        """将 PIL Image 转为 base64 字符串。"""
        buffer = io.BytesIO()
        # 用 JPEG 格式输出以保证兼容性
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

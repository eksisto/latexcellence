"""
表格 OCR 识别业务逻辑。

流程：
    1. 校验图片格式（PNG/JPEG/WebP）和大小
    2. 预处理：超过 2000px 的边等比缩放到 2000px
    3. 转为 base64 发送给 LLM Vision API（第一次 LLM 调用）
    4. 获取返回的 LaTeX 表格代码后，调用 LaTeX 宏包分析器（第二次 LLM 调用）
    5. 将宏包注释前置到 LaTeX 代码中，返回合并结果
"""

import base64
import io
from typing import Any

from PIL import Image
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation
from backend.utils.latex_package_analyzer import prepend_package_comments


class TableOCRResult(BaseModel):
    """表格 OCR 识别输出"""
    latex: str
    confidence: float = 0.0


_MAX_DIMENSION = 2000
_SUPPORTED_FORMATS = {"image/png", "image/jpeg", "image/webp"}


class TableOCRService:
    """表格 OCR 识别服务。"""

    def __init__(self):
        self._logger = get_logger("table_ocr")

    async def recognize(self, image_data: bytes, content_type: str) -> dict[str, Any]:
        """
        识别图片中的表格，返回 LaTeX 浮动体代码。

        参数：
            image_data: 图片二进制数据
            content_type: MIME 类型

        返回：
            包含 latex / confidence 的 dict
        """
        self._validate_content_type(content_type)

        img = self._preprocess(image_data)
        base64_image = self._to_base64(img)

        cfg = get_config()
        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning empty result")
            return {
                "latex": "",
                "confidence": 0.0,
                "low_confidence": True,
            }

        llm = get_llm_client()
        result = await self._recognize_with_llm(llm, base64_image, content_type)

        # 第二步：分析 LaTeX 代码所需的宏包
        if result.get("latex"):
            result["latex"] = await prepend_package_comments(llm, result["latex"])

        return result

    async def _recognize_with_llm(
        self, llm, base64_image: str, content_type: str
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": get_prompt("table_ocr", "system")},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Convert this table image to LaTeX float code:",
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

        try:
            validated: TableOCRResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=TableOCRResult,
                max_retries=3,
                logger_name="table_ocr",
                temperature=0.1,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

            result = validated.model_dump()

            if validated.confidence < 0.7:
                result["low_confidence"] = True

            return result

        except ValueError as e:
            self._logger.error(f"Table OCR failed after retries: {e}")
            raise RuntimeError(f"Table recognition failed: {e}")
        except Exception as e:
            self._logger.error(f"Table OCR error: {e}")
            raise RuntimeError(f"Table recognition failed: {e}")

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
        max_size_mb = cfg.features.table_ocr.max_image_size_mb
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
            self._logger.info(
                f"Resized image: {original_size} -> {img.size}"
            )

        return img

    def _to_base64(self, img: Image.Image) -> str:
        """将 PIL Image 转为 base64 字符串。"""
        buffer = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

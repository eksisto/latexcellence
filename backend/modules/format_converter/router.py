"""
文档格式转换 API 路由（Markdown/DOCX/HTML ↔ LaTeX）。

提供文本和文件两种输入方式的格式转换接口。
"""

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.core.config import get_config
from backend.modules.format_converter.service import (
    FormatConverterService,
    SUPPORTED_FORMATS,
)

router = APIRouter(prefix="/api/format-converter", tags=["format_converter"])

_service: FormatConverterService | None = None


def get_service() -> FormatConverterService:
    global _service
    if _service is None:
        _service = FormatConverterService()
    return _service


class ConvertRequest(BaseModel):
    """向后兼容：Markdown 转 LaTeX 请求体"""

    markdown: str


class ConvertTextRequest(BaseModel):
    """文本格式转换请求体"""

    text: str
    source_format: str = "markdown"
    target_format: str = "latex"


@router.post("/convert")
async def convert_markdown(request: ConvertRequest):
    """
    向后兼容接口：将 Markdown 文本转换为 LaTeX 代码。

    请求体：
        { markdown: "..." }

    返回格式：
        { latex: "..." }
    """
    try:
        result = await get_service().convert(request.markdown)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/convert-text")
async def convert_text(request: ConvertTextRequest):
    """
    文本格式转换接口。

    支持格式：markdown, html, latex
    请求体：
        { text: "...", source_format: "markdown", target_format: "latex" }

    返回格式：
        { result: "..." }
    """
    try:
        result = await get_service().convert_text(
            request.text, request.source_format, request.target_format
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/convert-file")
async def convert_file(
    file: UploadFile = File(...),
    source_format: str = Query(..., description="源格式"),
    target_format: str = Query(..., description="目标格式"),
):
    """
    文件上传格式转换接口。

    支持格式：markdown, html, docx, latex
    Query 参数：
        source_format: 源格式
        target_format: 目标格式

    返回格式：
        { result: "...", output_filename: "output.tex" }
    """
    try:
        cfg = get_config()
        max_size = cfg.features.format_converter.max_file_size_mb * 1024 * 1024

        file_bytes = await file.read()
        if len(file_bytes) > max_size:
            max_mb = cfg.features.format_converter.max_file_size_mb
            raise ValueError(
                f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB. "
                f"Maximum: {max_mb}MB"
            )

        result = await get_service().convert_file(
            file_bytes,
            file.filename or "upload",
            source_format,
            target_format,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

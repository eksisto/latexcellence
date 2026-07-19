"""
Markdown 转 LaTeX API 路由。

提供 Markdown 文本到 LaTeX 代码的转换接口。
"""



from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.markdown_converter.service import MarkdownConverterService

router = APIRouter(prefix="/api/markdown-converter", tags=["markdown_converter"])

_service: MarkdownConverterService | None = None


def get_service() -> MarkdownConverterService:
    global _service
    if _service is None:
        _service = MarkdownConverterService()
    return _service


class ConvertRequest(BaseModel):
    """Markdown 转 LaTeX 请求体"""
    markdown: str


@router.post("/convert")
async def convert_markdown(request: ConvertRequest):
    """
    将 Markdown 文本转换为 LaTeX 代码。

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

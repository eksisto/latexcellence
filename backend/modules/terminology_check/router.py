"""
术语一致性检查 API 路由。

提供段落预览、术语检查、修改应用等接口。
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.modules.terminology_check.service import TerminologyCheckService

router = APIRouter(prefix="/api/terminology-check", tags=["terminology_check"])

_service: Optional[TerminologyCheckService] = None


def get_service() -> TerminologyCheckService:
    global _service
    if _service is None:
        _service = TerminologyCheckService()
    return _service


class TerminologyCheckRequest(BaseModel):
    file_path: str
    paragraph_indices: Optional[list[int]] = None


class ChangeItem(BaseModel):
    paragraph_index: int
    new_text: str


class TerminologyApplyRequest(BaseModel):
    file_path: str
    changes: list[ChangeItem]


@router.get("/preview")
async def preview_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
):
    """
    预览指定文件的段落（含 LaTeX 剥离后的纯文本）。
    """
    try:
        result = get_service().preview_paragraphs(file_path)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/check")
async def check_terminology(request: TerminologyCheckRequest):
    """
    对项目进行术语一致性检查。

    扫描目标文件的所有段落，用 LLM 提取关键术语，
    然后进行跨段落一致性检测（大小写、缩写、混用）。
    """
    try:
        result = await get_service().check_terminology(
            request.file_path,
            request.paragraph_indices,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/apply")
async def apply_changes(request: TerminologyApplyRequest):
    """
    将用户审核确认的术语修改写回源文件。
    """
    try:
        changes = [c.model_dump() for c in request.changes]
        result = get_service().apply_changes(request.file_path, changes)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/stream/{project_id}")
async def stream_terminology_check(
    project_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
):
    """
    SSE 流式端点：逐段推送术语检查进度和结果。
    """
    async def event_stream():
        try:
            async for event in get_service().check_terminology_stream(file_path):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

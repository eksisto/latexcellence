"""
源码格式化 API 路由。

提供段落查看、格式化检查和修改生效等接口。
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.base_paragraph_check import ApplyChangeItem
from backend.modules.formatting.service import FormattingService

router = APIRouter(prefix="/api/formatting", tags=["formatting"])

_service: Optional[FormattingService] = None


def get_service() -> FormattingService:
    global _service
    if _service is None:
        _service = FormattingService()
    return _service


# ── request models ─────────────────────────────────────────────────────────

class FormattingRequest(BaseModel):
    file_path: str
    paragraph_indices: Optional[list[int]] = None
    resolve_input: bool = False


class FormattingApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]


# ── preview ────────────────────────────────────────────────────────────────

@router.get("/preview")
async def preview_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
):
    """
    预览指定文件的段落（含原始 LaTeX 源码和剥离后的纯文本）。

    原始源码将交给 LLM 进行格式化优化；纯文本仅供前端展示上下文。
    当 resolve_input=True 时，递归解析 \\input/\\include 引用。
    """
    try:
        result = get_service().preview_paragraphs(file_path, resolve_input)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── check ──────────────────────────────────────────────────────────────────

@router.post("/check")
async def check_formatting(request: FormattingRequest):
    """
    对指定段落的 LaTeX 源码进行格式化优化。

    调用 LLM 逐段调整间距、环境对齐、缩进等格式，输出经 Pydantic 校验。
    仅检查 paragraph_indices 中指定的段落；未指定时检查所有段落。
    """
    try:
        result = await get_service().check_formatting(
            request.file_path,
            request.paragraph_indices,
            request.resolve_input,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── apply ──────────────────────────────────────────────────────────────────

@router.post("/apply")
async def apply_changes(request: FormattingApplyRequest):
    """
    将用户审核确认的格式修改写回源文件。

    每个 change 包含 paragraph_index 和 new_text（格式化后的完整 LaTeX 源码）。
    后端直接写回，无需还原 marker。
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


# ── stream ─────────────────────────────────────────────────────────────────

@router.get("/stream/{project_id}")
async def stream_formatting(
    project_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
):
    """
    SSE 流式端点：按段落逐批推送格式化进度和结果。

    事件格式：
        { "type": "progress", "current": 3, "total": 12, "file_path": "..." }
        { "type": "result", "paragraph_index": 0, "data": {...} }
        { "type": "error", "message": "...", "retrying": true }
        { "type": "done" }
    """
    async def event_stream():
        try:
            async for event in get_service().check_formatting_stream(
                file_path, resolve_input
            ):
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

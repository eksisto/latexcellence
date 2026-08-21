"""
LaTeX 代码审查 API 路由。

提供段落查看、代码审查和建议展示等接口。
"""

import json


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core import access_control
from backend.core.base_paragraph_check import ApplyChangeItem, inject_expected_md5s
from backend.modules.code_review.service import CodeReviewService

router = APIRouter(prefix="/api/code-review", tags=["code_review"])


def get_service() -> CodeReviewService:
    # 每次请求创建独立实例，避免并发请求共享 marker 映射等请求级状态。
    return CodeReviewService()


# ── request models ─────────────────────────────────────────────────────────


class CodeReviewRequest(BaseModel):
    file_path: str
    paragraph_indices: list[int] | None = None
    resolve_input: bool = False
    language: str = "zh"


class CodeReviewApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]
    operation_id: str | None = None
    expected_md5s: dict[str, str] | None = None


# ── preview ────────────────────────────────────────────────────────────────


@router.get("/preview")
async def preview_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    预览指定文件的段落（含原始 LaTeX 源码和剥离后的纯文本）。

    当 resolve_input=True 时，递归解析 \\input/\\include 引用。
    """
    try:
        result = get_service().preview_paragraphs(
            file_path, resolve_input, workspace_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── check ──────────────────────────────────────────────────────────────────


@router.post("/check")
async def check_code_review(
    request: CodeReviewRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    对指定段落进行 LaTeX 代码审查。

    先通过 latexindent.pl（或内置格式化器）机械化格式化源码，
    再由 LLM 审查代码质量（坏味道、过时用法、最佳实践等）。
    """
    try:
        result = await get_service().check_code_review(
            request.file_path,
            request.paragraph_indices,
            request.resolve_input,
            workspace_id,
            request.language,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── apply ──────────────────────────────────────────────────────────────────


@router.post("/apply")
async def apply_changes(
    request: CodeReviewApplyRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """
    将格式化后的 LaTeX 源码写回源文件。

    每个 change 包含 paragraph_index 和 new_text（格式化后的完整 LaTeX 源码）。
    LLM 代码审查建议仅在前端展示，不自动写回。
    """
    try:
        changes = [c.model_dump() for c in request.changes]
        inject_expected_md5s(changes, request.file_path, request.expected_md5s)
        result = get_service().apply_changes(
            request.file_path, changes, workspace_id, request.operation_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── stream ─────────────────────────────────────────────────────────────────


@router.get("/stream/{workspace_id}")
async def stream_code_review(
    workspace_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    language: str = Query("zh", description="前端 UI 语言"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    SSE 流式端点：按段落逐批推送代码审查进度和结果。

    事件格式：
        { "type": "progress", "current": 3, "total": 12, "file_path": "..." }
        { "type": "result", "paragraph_index": 0, "data": {...} }
        { "type": "error", "message": "...", "retrying": true }
        { "type": "done" }
    """

    async def event_stream():
        try:
            async for event in get_service().check_code_review_stream(
                file_path, resolve_input, workspace_id, language
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

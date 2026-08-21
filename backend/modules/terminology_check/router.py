"""
术语一致性检查 API 路由。

提供段落预览、术语检查、修改应用等接口。
"""

import json


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core import access_control
from backend.core.base_paragraph_check import ApplyChangeItem, inject_expected_md5s
from backend.modules.terminology_check.service import TerminologyCheckService

router = APIRouter(prefix="/api/terminology-check", tags=["terminology_check"])


def get_service() -> TerminologyCheckService:
    # 每次请求创建独立实例，避免并发请求共享 marker 映射等请求级状态。
    return TerminologyCheckService()


class TerminologyCheckRequest(BaseModel):
    file_path: str
    paragraph_indices: list[int] | None = None
    resolve_input: bool = False


class TerminologyApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]
    operation_id: str | None = None
    expected_md5s: dict[str, str] | None = None


@router.get("/preview")
async def preview_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    预览指定文件的段落（含 LaTeX 剥离后的纯文本）。
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


@router.post("/check")
async def check_terminology(
    request: TerminologyCheckRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    对项目进行术语一致性检查。

    扫描目标文件的所有段落，用 LLM 提取关键术语，
    然后进行跨段落一致性检测（大小写、缩写、混用）。
    """
    try:
        result = await get_service().check_terminology(
            request.file_path,
            request.paragraph_indices,
            request.resolve_input,
            workspace_id,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/apply")
async def apply_changes(
    request: TerminologyApplyRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """
    将用户审核确认的术语修改写回源文件。
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


@router.get("/stream/{workspace_id}")
async def stream_terminology_check(
    workspace_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    SSE 流式端点：逐段推送术语检查进度和结果。
    """

    async def event_stream():
        try:
            async for event in get_service().check_terminology_stream(
                file_path, resolve_input, workspace_id
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

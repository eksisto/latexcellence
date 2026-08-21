"""
LaTeX 样式修改 API 路由。

同时提供：
    1. 旧粘贴代码模式：/modify
    2. 项目 Agent 模式：run / refine / session / trace / apply / undo / delete
"""

import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask

from backend.core import access_control
from backend.core.logger import get_logger
from backend.core.multiuser.dependencies import current_user_context
from backend.modules.style_modify.service import StyleModifyService

# 会话类资源按用户隔离（P6）：依赖注入当前用户上下文，
# 多用户模式资源定向到当前用户衍生目录，单用户模式回退存量全局路径行为不变。
router = APIRouter(
    prefix="/api/style-modify",
    tags=["style_modify"],
    dependencies=[Depends(current_user_context)],
)
logger = get_logger(__name__)

_service: StyleModifyService | None = None


def get_service() -> StyleModifyService:
    global _service
    if _service is None:
        _service = StyleModifyService()
    return _service


class ModifyRequest(BaseModel):
    """样式修改请求体"""

    latex: str
    description: str

    @field_validator("latex")
    @classmethod
    def latex_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("LaTeX code cannot be empty")
        return v

    @field_validator("description")
    @classmethod
    def description_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v


class AgentRunRequest(BaseModel):
    """项目 Agent 启动请求体"""

    description: str
    workspace_id: str
    language: str = "zh"

    @field_validator("description")
    @classmethod
    def description_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v


class AgentRefineRequest(BaseModel):
    """项目 Agent refinement 请求体"""

    workspace_id: str
    message: str
    language: str = "zh"

    @field_validator("message")
    @classmethod
    def message_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v


class AgentApplyRequest(BaseModel):
    """应用 proposal 请求体"""

    revision: int
    workspace_id: str


class AgentUndoRequest(BaseModel):
    """撤销 operation 请求体"""

    workspace_id: str


@router.post("/modify")
async def modify_latex(request: ModifyRequest):
    """
    根据用户描述修改 LaTeX 代码。

    请求体：
        { latex: "...", description: "..." }

    返回格式：
        { latex: "..." }
    """
    try:
        result = await get_service().modify(request.latex, request.description)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/run")
async def agent_run(
    request: AgentRunRequest,
    _: dict = Depends(access_control.require_workspace_read_json),
):
    """创建 style_modify Agent session 并通过 SSE 执行。"""

    async def event_stream():
        try:
            async for event in get_service().agent_run(
                request.workspace_id,
                request.description,
                request.language,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except HTTPException as e:
            # 结构化错误（如衍生配额超限 413）：透传 error 错误码供前端 translateError
            detail = e.detail
            payload = (
                {"type": "error", "message": detail}
                if isinstance(detail, dict)
                else {"type": "error", "message": str(detail)}
            )
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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


@router.post("/agent/sessions/{session_id}/refine")
async def agent_refine(
    session_id: str,
    request: AgentRefineRequest,
    _: dict = Depends(access_control.require_workspace_read_json),
):
    """基于最新 proposal 追加要求并流式生成新 revision。"""

    async def event_stream():
        try:
            async for event in get_service().agent_refine(
                session_id,
                request.workspace_id,
                request.message,
                request.language,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except HTTPException as e:
            detail = e.detail
            payload = (
                {"type": "error", "message": detail}
                if isinstance(detail, dict)
                else {"type": "error", "message": str(detail)}
            )
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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


@router.get("/agent/sessions/{session_id}")
async def get_agent_session(
    session_id: str,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """恢复 session 和最新 proposal。"""
    try:
        return get_service().get_agent_session(session_id, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agent/sessions/{session_id}/trace")
async def get_agent_trace(
    session_id: str,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取脱敏后的结构化执行轨迹。"""
    try:
        return get_service().get_agent_trace(session_id, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agent/sessions/{session_id}/trace/download")
async def download_agent_trace(
    session_id: str,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """下载完整可公开轨迹。"""
    try:
        data = get_service().get_agent_trace(session_id, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not data["events"]:
        raise HTTPException(
            status_code=404, detail="No trace recorded for this session"
        )
    content = json.dumps(data, ensure_ascii=False, indent=2)
    buf = io.BytesIO(content.encode("utf-8"))

    def _cleanup():
        buf.close()

    return StreamingResponse(
        buf,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="style-modify-{session_id[:8]}-trace.json"'
            )
        },
        background=BackgroundTask(_cleanup),
    )


@router.post("/agent/sessions/{session_id}/apply")
async def apply_agent_proposal(
    session_id: str,
    request: AgentApplyRequest,
    _: dict = Depends(access_control.require_workspace_write_json),
):
    """提交指定 revision 的完整 proposal。"""
    try:
        return await get_service().apply_agent_proposal(
            session_id,
            request.workspace_id,
            request.revision,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/operations/{operation_id}/undo")
async def undo_agent_operation(
    operation_id: str,
    request: AgentUndoRequest,
    _: dict = Depends(access_control.require_workspace_write_json),
):
    """安全撤销已应用操作。"""
    try:
        return await get_service().undo_agent_operation(
            operation_id,
            request.workspace_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agent/sessions/{session_id}")
async def delete_agent_session(
    session_id: str,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """删除未运行中的 session 和临时文件。"""
    try:
        return await get_service().delete_agent_session(session_id, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

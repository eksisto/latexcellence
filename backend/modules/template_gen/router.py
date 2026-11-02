"""
模板生成 API 路由。

薄路由层：仅做参数校验与状态码映射，业务逻辑在 service.py，Agent 主循环在 agent/。
"""

import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.core.logger import get_logger
from backend.core.multiuser.dependencies import current_user_context
from backend.modules.template_gen.service import get_template_gen_service

logger = get_logger(__name__)

# 会话类资源按用户隔离（P6）：依赖注入当前用户上下文，
# 多用户模式资源定向到当前用户衍生目录，单用户模式回退存量全局路径行为不变。
router = APIRouter(
    prefix="/api/template-gen",
    tags=["template_gen"],
    dependencies=[Depends(current_user_context)],
)


class GenerateRequest(BaseModel):
    description: str
    language: str = "zh"


@router.post("/generate")
async def generate(request: GenerateRequest):
    """
    自主 Agent 生成完整 LaTeX 项目（SSE 流式）。

    事件格式 data: {"type":"...","content":"..."}\\n\\n，事件类型：
        plan / tool_call / tool_result / compile / warning / done / error
    """
    service = get_template_gen_service()

    async def event_stream():
        try:
            async for event in service.generate(request.description, request.language):
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
        except Exception:
            # 未预期异常原文（OSError 等携带服务器用户空间绝对路径）不下发 SSE：
            # 固定 internal_error，原文进服务端日志
            logger.exception("Unhandled error while streaming template-gen SSE")
            payload = {
                "type": "error",
                "message": {
                    "error": "internal_error",
                    "message": "An unexpected server error occurred",
                },
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/files")
async def list_files(session_id: str = Query(..., description="Agent 会话 ID")):
    """列出某次 Agent 生成产出的文件树。"""
    try:
        return get_template_gen_service().list_session_files(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file")
async def read_file(
    session_id: str = Query(...),
    path: str = Query(..., description="沙箱内相对路径"),
):
    """读取沙箱内单个文件内容。"""
    try:
        return get_template_gen_service().read_session_file(session_id, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/preview")
async def preview_file(
    session_id: str = Query(...),
    path: str = Query(..., description="沙箱内相对路径"),
):
    """内联预览产物文件（当前仅支持 PDF），供前端 PDFPreview 渲染。"""
    try:
        resolved, media_type = get_template_gen_service().resolve_preview_file(
            session_id, path
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileResponse(
        path=str(resolved),
        media_type=media_type,
        filename=resolved.name,
        content_disposition_type="inline",
    )


@router.get("/download")
async def download_zip(session_id: str = Query(..., description="Agent 会话 ID")):
    """将沙箱内所有文件打包为 ZIP 下载。"""
    try:
        data, filename = get_template_gen_service().zip_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/trace")
async def get_session_trace(session_id: str = Query(..., description="Agent 会话 ID")):
    """读取 Agent 运行追踪日志。"""
    try:
        return get_template_gen_service().get_session_trace(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trace/download")
async def download_session_trace(
    session_id: str = Query(..., description="Agent 会话 ID"),
):
    """导出 Agent 运行追踪日志（JSON）。"""
    try:
        data = get_template_gen_service().get_session_trace(session_id)
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
                f'attachment; filename="latex-template-{session_id[:8]}-trace.json"'
            )
        },
        background=BackgroundTask(_cleanup),
    )

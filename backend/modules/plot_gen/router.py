"""科研绘图 Agent API 路由。"""

import io
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backend.core.config import get_config
from backend.core.multiuser.dependencies import current_user_context
from backend.modules.plot_gen.agent import get_plot_output_dir, load_plot_sandbox
from backend.modules.plot_gen.models import GeneratePlotRequest, RefinePlotRequest
from backend.modules.plot_gen.service import get_plot_gen_service
from backend.utils.path_guard import resolve_project_path

# 会话类资源按用户隔离（P6）：依赖注入当前用户上下文，
# 多用户模式资源定向到当前用户衍生目录，单用户模式回退存量全局路径行为不变。
router = APIRouter(
    prefix="/api/plot-gen",
    tags=["plot_gen"],
    dependencies=[Depends(current_user_context)],
)

_service = None


def get_service():
    global _service
    if _service is None:
        _service = get_plot_gen_service()
    return _service


@router.post("/upload")
async def upload_data(
    file: UploadFile = File(..., description="CSV/TSV/Excel/JSON 数据文件"),
    description: str = Form(""),
    style: str = Form("nature"),
    language: str = Form("zh"),
):
    """上传数据并创建制图 session。"""
    try:
        file_bytes = await file.read()
        result = await get_service().create_session_from_upload(
            file_bytes,
            file.filename or "",
            description=description,
            style=style,
            language=language,
        )
        return result
    except HTTPException:  # 结构化错误（如衍生配额超限 413）原样透传
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scientific plotting failed: {e}")


@router.post("/agent/generate")
async def agent_generate(request: GeneratePlotRequest):
    """SSE 启动科研绘图 Agent。"""

    async def event_stream():
        try:
            async for event in get_service().generate(
                request.session_id,
                request.description,
                request.style,
                request.language,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Scientific plotting failed: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/refine")
async def refine_session(session_id: str, request: RefinePlotRequest):
    """SSE 自然语言微调。"""

    async def event_stream():
        try:
            async for event in get_service().refine(
                session_id, request.message, request.language
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Scientific plotting failed: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/files")
async def list_session_files(session_id: str):
    try:
        files = get_service().list_session_files(session_id)
        return {"session_id": session_id, "files": files}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}/file")
async def read_session_file(
    session_id: str, path: str = Query(..., description="沙箱内相对路径")
):
    content, err = get_service().read_session_file(session_id, path)
    if err is not None:
        raise HTTPException(status_code=400, detail=err)
    return {"session_id": session_id, "path": path, "content": content}


@router.get("/sessions/{session_id}/trace")
async def get_session_trace(session_id: str):
    """读取 Agent 运行追踪日志。"""
    try:
        return get_service().get_session_trace(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}/trace/download")
async def download_session_trace(session_id: str):
    """导出 Agent 运行追踪日志（JSON）。"""
    try:
        data = get_service().get_session_trace(session_id)
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
                f'attachment; filename="plot-gen-{session_id[:8]}-trace.json"'
            )
        },
        background=BackgroundTask(_cleanup),
    )


@router.get("/sessions/{session_id}/download")
async def download_session(session_id: str):
    try:
        data, files = get_service().zip_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not files:
        raise HTTPException(status_code=404, detail="No files in sandbox")
    buf = io.BytesIO(data)

    def _cleanup():
        buf.close()

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="plot-gen-{session_id[:8]}.zip"'
            )
        },
        background=BackgroundTask(_cleanup),
    )


@router.get("/sessions/{session_id}/preview")
async def preview_session_file(
    session_id: str, path: str = Query(..., description="预览文件相对路径")
):
    cfg = get_config()
    try:
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        resolved = resolve_project_path(
            sandbox,
            path,
            allowed_suffixes=(".png", ".svg", ".pdf", ".eps", ".tex"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    media_types = {
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".eps": "application/postscript",
        ".tex": "text/plain",
    }
    return FileResponse(
        path=str(resolved),
        media_type=media_types.get(resolved.suffix.lower(), "application/octet-stream"),
        filename=resolved.name,
        content_disposition_type="inline",
    )


@router.get("/styles")
async def get_styles():
    return {"styles": get_service().get_styles()}

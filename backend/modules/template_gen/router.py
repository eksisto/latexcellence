"""
模板生成 API 路由。

薄路由层：仅做参数校验与状态码映射，业务逻辑在 service.py，Agent 主循环在 agent.py。
"""

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.modules.template_gen.service import get_template_gen_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/template-gen", tags=["template_gen"])


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
        except Exception as e:
            yield (
                f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            )

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

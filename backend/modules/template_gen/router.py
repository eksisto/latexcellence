"""
模板生成 API 路由。

提供模板生成、保存接口，以及自主 Agent 生成接口。
"""

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.template_gen.service import TemplateGenService
from backend.modules.template_gen.agent import (
    get_template_agent_service,
    load_sandbox,
    safe_read_sandbox_file,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/template-gen", tags=["template_gen"])

_service: "TemplateGenService | None" = None


def get_service() -> TemplateGenService:
    global _service
    if _service is None:
        _service = TemplateGenService()
    return _service


class TemplateGenRequest(BaseModel):
    description: str
    language: str = "zh"


class TemplateSaveRequest(BaseModel):
    template: dict


class AgentGenerateRequest(BaseModel):
    description: str
    language: str = "zh"


@router.post("/generate")
async def generate_template(request: TemplateGenRequest):
    """
    根据用户描述生成 LaTeX 模板（旧的单次生成，保留向后兼容）。

    返回 LaTeX preamble、body 骨架、推荐包列表和说明。
    """
    try:
        result = await get_service().generate_template(request.description, request.language)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def save_template(request: TemplateSaveRequest):
    """
    将生成的模板保存为 .tex 文件到配置的输出目录。
    """
    try:
        result = await get_service().save_template(request.template)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 自主 Agent：SSE 流式生成 ────────────────────────────────────────────


@router.post("/agent/generate")
async def agent_generate(request: AgentGenerateRequest):
    """
    自主 Agent 生成完整 LaTeX 项目（SSE 流式）。

    事件格式 data: {"type":"...","content":"..."}\\n\\n，事件类型：
        plan / tool_call / tool_result / compile / done / error
    """
    service = get_template_agent_service()

    async def event_stream():
        try:
            async for event in service.generate(request.description,
                                                request.language):
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


@router.get("/agent/files")
async def agent_list_files(session_id: str = Query(..., description="Agent 会话 ID")):
    """列出某次 Agent 生成产出的文件树。"""
    cfg = get_config()
    try:
        sandbox = load_sandbox(
            cfg.features.template_gen.output_dir, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sandbox.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    files: list[dict] = []
    root = sandbox.resolve()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        files.append({"path": rel, "size": p.stat().st_size})
    return {"session_id": session_id, "sandbox": str(sandbox), "files": files}


@router.get("/agent/file")
async def agent_read_file(
    session_id: str = Query(...),
    path: str = Query(..., description="沙箱内相对路径"),
):
    """读取沙箱内单个文件内容。"""
    cfg = get_config()
    try:
        sandbox = load_sandbox(
            cfg.features.template_gen.output_dir, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sandbox.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    content, err = safe_read_sandbox_file(sandbox, path)
    if err is not None:
        raise HTTPException(status_code=400, detail=err)
    return {"session_id": session_id, "path": path, "content": content}


@router.get("/agent/download")
async def agent_download_zip(session_id: str = Query(..., description="Agent 会话 ID")):
    """将沙箱内所有文件打包为 ZIP 下载。"""
    cfg = get_config()
    try:
        sandbox = load_sandbox(
            cfg.features.template_gen.output_dir, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sandbox.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    root = sandbox.resolve()
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="No files in sandbox")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            arcname = p.relative_to(root).as_posix()
            zf.write(p, arcname)
            logger.debug("ZIP included: %s", arcname)
    buf.seek(0)

    def _cleanup():
        buf.close()

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="latex-template-{session_id[:8]}.zip"',
        },
        background=BackgroundTask(_cleanup),
    )

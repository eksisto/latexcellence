"""
表达优化 API 路由。

提供段落查看、表达优化和修改生效等接口。
"""

import json


from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.base_paragraph_check import ApplyChangeItem
from backend.modules.expression_opt.service import ExpressionOptService
from backend.modules.expression_opt.prompt_manager import get_prompt_manager

router = APIRouter(prefix="/api/expression-opt", tags=["expression_opt"])

_service: ExpressionOptService | None = None


def get_service() -> ExpressionOptService:
    global _service
    if _service is None:
        _service = ExpressionOptService()
    return _service


# ── request models ─────────────────────────────────────────────────────────

class ExpressionOptRequest(BaseModel):
    file_path: str
    paragraph_indices: list[int] | None = None
    resolve_input: bool = False


class ExpressionApplyRequest(BaseModel):
    file_path: str
    changes: list[ApplyChangeItem]
    operation_id: str | None = None


# ── preview ────────────────────────────────────────────────────────────────

@router.get("/preview")
async def preview_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """
    预览指定文件的段落（含 LaTeX 剥离后的纯文本）。
    当 resolve_input=True 时，递归解析 \\input/\\include 引用。
    """
    try:
        result = get_service().preview_paragraphs(file_path, resolve_input, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── optimize ───────────────────────────────────────────────────────────────

@router.post("/optimize")
async def optimize_expression(request: ExpressionOptRequest, workspace_id: str = Query(..., description="工作区唯一标识符")):
    """
    对指定段落的纯文本进行表达优化。

    调用 LLM 逐段优化，输出经 Pydantic 校验。
    """
    try:
        result = await get_service().optimize(
            request.file_path,
            request.paragraph_indices,
            request.resolve_input,
            workspace_id,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── apply ──────────────────────────────────────────────────────────────────

@router.post("/apply")
async def apply_changes(request: ExpressionApplyRequest, workspace_id: str = Query(..., description="工作区唯一标识符")):
    """
    将用户审核确认的修改写回源文件。

    每个 change 包含 paragraph_index 和 new_text（优化后的纯文本）。
    """
    try:
        changes = [c.model_dump() for c in request.changes]
        result = get_service().apply_changes(
            request.file_path, changes, workspace_id, request.operation_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── stream ─────────────────────────────────────────────────────────────────

@router.get("/stream/{workspace_id}")
async def stream_optimize(
    workspace_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
):
    """SSE 流式端点：按段落逐批推送优化进度和结果。"""
    async def event_stream():
        try:
            async for event in get_service().optimize_stream(
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


# ── prompt management ─────────────────────────────────────────────────────

class PromptSaveRequest(BaseModel):
    name: str
    content: str


class PromptSetActiveRequest(BaseModel):
    name: str


@router.get("/prompts")
async def list_prompts():
    """列出所有自定义提示词。"""
    pm = get_prompt_manager()
    return {"prompts": pm.list_prompts()}


@router.get("/prompts/{name}")
async def load_prompt(name: str):
    """加载指定提示词内容。"""
    pm = get_prompt_manager()
    content = pm.load_prompt(name)
    if content is None:
        raise HTTPException(
            status_code=404, detail=f"Prompt not found: {name}")
    return {"name": name, "content": content}


@router.post("/prompts")
async def save_prompt(request: PromptSaveRequest):
    """保存（创建或更新）提示词。"""
    pm = get_prompt_manager()
    pm.save_prompt(request.name, request.content)
    return {"status": "ok", "name": request.name}


@router.delete("/prompts/{name}")
async def delete_prompt(name: str):
    """删除指定提示词。"""
    pm = get_prompt_manager()
    if not pm.delete_prompt(name):
        raise HTTPException(
            status_code=404, detail=f"Prompt not found: {name}")
    return {"status": "ok"}


@router.post("/prompts/active")
async def set_active_prompt(request: PromptSetActiveRequest):
    """设置当前激活的提示词。"""
    pm = get_prompt_manager()
    pm.set_active(request.name)
    return {"status": "ok", "name": request.name}

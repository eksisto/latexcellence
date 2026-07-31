"""
写作助手 Chat API 路由。

端点：
    POST /api/writing-chat/send           — 流式发送消息（SSE）
    POST /api/writing-chat/generate-edit    — 生成编辑操作建议
    POST /api/writing-chat/apply-edit        — 应用一条编辑操作
    POST /api/writing-chat/message/delete   — 删除消息（summary 消息还原其快照）
    GET  /api/writing-chat/sessions          — 列出所有会话
    GET  /api/writing-chat/sessions/{id}     — 加载单个会话完整数据
    DELETE /api/writing-chat/sessions/{id}   — 删除会话
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.modules.writing_chat.models import (
    ApplyEditRequest,
    AssistantSaveRequest,
    AssistantSetActiveRequest,
    CancelClarifyRequest,
    CancelToolRequest,
    ChatSendRequest,
    ClarifyRequest,
    ConfirmToolRequest,
    DeleteMessageRequest,
    GenerateEditRequest,
    RenameSessionRequest,
)
from backend.modules.writing_chat.prompt_manager import get_prompt_manager
from backend.modules.writing_chat.service import get_writing_chat_service

router = APIRouter(prefix="/api/writing-chat", tags=["writing_chat"])


# ── send：SSE 流式 ───────────────────────────────────────────────────────


@router.post("/send")
async def send_message(
    request: ChatSendRequest,
    workspace_id: str = Query(default="", description="工作区唯一标识符"),
):
    """流式发送对话消息。返回 SSE，事件为 data: {json}\\n\\n。"""
    service = get_writing_chat_service()

    async def event_stream():
        try:
            async for event in service.send(request, workspace_id):
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


# ── 生成编辑 ────────────────────────────────────────────────────────────


@router.post("/generate-edit")
async def generate_edit(
    request: GenerateEditRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """基于对话指令生成段落编辑建议。"""
    service = get_writing_chat_service()
    try:
        result = await service.generate_edit(request, workspace_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 应用编辑 ────────────────────────────────────────────────────────────


@router.post("/apply-edit")
async def apply_edit(
    request: ApplyEditRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """把某条 assistant 消息附带的编辑操作写回源文件。"""
    from backend.core.project_manager import FileConflictError

    service = get_writing_chat_service()
    try:
        return service.apply_edit(request, workspace_id)
    except FileConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_conflict",
                "message": str(e),
                "file_path": e.file_path,
                "expected_md5": e.expected_md5,
                "current_md5": e.current_md5,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 工具调用确认 ─────────────────────────────────────────────────────


@router.post("/confirm-tool")
async def confirm_tool(request: ConfirmToolRequest):
    """确认一个待定的工具调用（如 edit_paragraph），允许 LLM 继续执行。"""
    service = get_writing_chat_service()
    ok = service.confirm_pending_tool(request.pending_id, request.modified_args)
    if not ok:
        raise HTTPException(
            status_code=404, detail="Pending tool not found or already resolved"
        )
    return {"status": "ok", "pending_id": request.pending_id}


@router.post("/cancel-tool")
async def cancel_tool(request: CancelToolRequest):
    """取消一个待定的工具调用（如 edit_paragraph），阻止 LLM 执行。"""
    service = get_writing_chat_service()
    ok = service.cancel_pending_tool(request.pending_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="Pending tool not found or already resolved"
        )
    return {"status": "ok", "pending_id": request.pending_id}


# ── 澄清请求 ──────────────────────────────────────────────────────


@router.post("/clarify")
async def submit_clarification(request: ClarifyRequest):
    """提交对 LLM 澄清请求的回复。"""
    service = get_writing_chat_service()
    ok = service.submit_clarification(request.clarify_id, request.answer)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Clarification request not found or already resolved",
        )
    return {"status": "ok", "clarify_id": request.clarify_id}


@router.post("/cancel-clarify")
async def cancel_clarification(request: CancelClarifyRequest):
    """取消一个待定的澄清请求。"""
    service = get_writing_chat_service()
    ok = service.cancel_clarification(request.clarify_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Clarification request not found or already resolved",
        )
    return {"status": "ok", "clarify_id": request.clarify_id}


# ── 删除消息 ──────────────────────────────────────────────────────────────


@router.post("/message/delete")
async def delete_message(request: DeleteMessageRequest):
    """删除消息；summary 消息会还原其快照（原始对话）。"""
    service = get_writing_chat_service()
    try:
        result = service.delete_message(request)
        if not result["removed"] and not result["restored"]:
            raise HTTPException(status_code=404, detail="Message not found")
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 会话管理 ──────────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    workspace_id: str = Query(
        default="", description="工作区唯一标识符，用于按项目隔离会话"
    )
):
    service = get_writing_chat_service()
    return {"sessions": service.list_sessions(workspace_id)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    service = get_writing_chat_service()
    ctx = service.load_session(session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": ctx.session_id,
        "created_at": ctx.created_at,
        "last_updated_at": ctx.last_updated_at,
        "cumulative_tokens": ctx.cumulative_tokens,
        "file_path": ctx.file_path,
        "messages": [m.model_dump(mode="json") for m in ctx.messages],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    service = get_writing_chat_service()
    if not service.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.patch("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: RenameSessionRequest):
    """重命名会话。"""
    service = get_writing_chat_service()
    try:
        return service.rename_session(session_id, request.title)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 助手提示词管理 ─────────────────────────────────────────────────────────


@router.get("/prompts")
async def list_prompts():
    """列出所有助手提示词。"""
    pm = get_prompt_manager()
    return {"prompts": pm.list_prompts()}


@router.get("/prompts/{name}")
async def load_prompt(name: str):
    """加载指定助手提示词内容。"""
    pm = get_prompt_manager()
    content = pm.load_prompt(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {name}")
    return {"name": name, "content": content}


@router.post("/prompts")
async def save_prompt(request: AssistantSaveRequest):
    """保存（创建或更新）助手提示词。"""
    pm = get_prompt_manager()
    pm.save_prompt(request.name, request.content)
    return {"status": "ok", "name": request.name}


@router.delete("/prompts/{name}")
async def delete_prompt(name: str):
    """删除指定助手提示词。"""
    pm = get_prompt_manager()
    if not pm.delete_prompt(name):
        raise HTTPException(status_code=404, detail=f"Prompt not found: {name}")
    return {"status": "ok"}


@router.post("/prompts/active")
async def set_active_prompt(request: AssistantSetActiveRequest):
    """设置当前激活的助手。"""
    pm = get_prompt_manager()
    pm.set_active(request.name)
    return {"status": "ok", "name": request.name}

"""
写作助手 API 路由。

端点：
    POST /api/writing-chat/send           — 流式发送消息（SSE）
    POST /api/writing-chat/generate-edit    — 生成编辑操作建议
    POST /api/writing-chat/apply-edit        — 应用一条编辑操作
    POST /api/writing-chat/message/delete   — 删除消息（summary 消息还原其快照）
    GET  /api/writing-chat/sessions          — 列出所有会话
    GET  /api/writing-chat/sessions/{id}     — 加载单个会话完整数据
    DELETE /api/writing-chat/sessions/{id}   — 删除会话
    POST /api/writing-chat/sessions/{id}/compact — 手动压缩会话

编辑器侧 AI 助手（editor-plan §5.2，阶段四）：
    POST /api/writing-chat/editor/send             — 编辑器缓冲区协议流式对话（SSE）
    POST /api/writing-chat/editor/apply-suggestion  — 记录建议应用结果（消息级标记）
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.core import access_control
from backend.core.config import get_config
from backend.core.multiuser.dependencies import (
    current_user_context,
    require_admin_if_multiuser,
)
from backend.modules.writing_chat.models import (
    ApplyEditRequest,
    ApplySuggestionRequest,
    AssistantSaveRequest,
    AssistantSetActiveRequest,
    CancelClarifyRequest,
    CancelToolRequest,
    ChatSendRequest,
    ClarifyRequest,
    ConfirmToolRequest,
    DeleteMessageRequest,
    EditorSendRequest,
    GenerateEditRequest,
    RenameSessionRequest,
)
from backend.modules.writing_chat.prompt_manager import get_prompt_manager
from backend.modules.writing_chat.service import get_writing_chat_service

# 会话类资源按用户隔离（P6）：依赖注入当前用户上下文，
# 多用户模式资源定向到当前用户衍生目录，单用户模式回退存量全局路径行为不变。
router = APIRouter(
    prefix="/api/writing-chat",
    tags=["writing_chat"],
    dependencies=[Depends(current_user_context)],
)


def _ensure_editor_assistant_available() -> None:
    """编辑器 / 助手开关把关（editor-plan §5.3，决策 Q26）。

    控制 LLM 开销是明确动机：editor.enabled=false 或
    editor.assistant_enabled=false 时服务端直接拒绝助手端点，
    不进入任何 LLM 调用路径。结构化错误码供前端 translateError 翻译。
    """
    editor_cfg = get_config().features.editor
    if not editor_cfg.enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "editor_disabled",
                "message": "The web editor is disabled on this server",
            },
        )
    if not editor_cfg.assistant_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "assistant_disabled",
                "message": "The editor AI assistant is disabled on this server",
            },
        )


# ── send：SSE 流式 ───────────────────────────────────────────────────────


@router.post("/send")
async def send_message(
    request: ChatSendRequest,
    workspace_id: str = Query(default="", description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace(write=False, required=False)),
):
    """流式发送对话消息。返回 SSE，事件为 data: {json}\\n\\n。"""
    service = get_writing_chat_service()

    async def event_stream():
        try:
            async for event in service.send(request, workspace_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except HTTPException as e:
            # 结构化错误（如衍生配额超限 413）：透传 {error, message} 供前端
            # translateError 按错误码翻译
            detail = e.detail
            payload = (
                {"type": "error", "message": detail}
                if isinstance(detail, dict)
                else {"type": "error", "message": str(detail)}
            )
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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


# ── 编辑器侧 AI 助手（editor-plan §5.2 / §7） ─────────────────────────────


@router.post("/editor/send")
async def editor_send(
    request: EditorSendRequest,
    workspace_id: str = Query(default="", description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace(write=False, required=False)),
):
    """编辑器缓冲区协议流式对话（SSE）。

    请求携带 CM6 缓冲区全文与光标/选区元信息；上下文注入量由服务端
    自适应裁剪（§7.3）。建议以结构化形式随 done 事件返回，不经后端落盘。
    """
    _ensure_editor_assistant_available()
    service = get_writing_chat_service()

    async def event_stream():
        try:
            async for event in service.editor_send(request, workspace_id):
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


@router.post("/editor/apply-suggestion")
async def apply_editor_suggestion(request: ApplySuggestionRequest):
    """记录建议应用结果（消息级标记），供会话历史展示。

    仅登记应用状态，不做任何文件写入——建议的实际应用发生在前端
    CM6 缓冲区，落盘仍走 save-file 单一路径。
    """
    _ensure_editor_assistant_available()
    service = get_writing_chat_service()
    try:
        return await service.apply_suggestion(request.session_id, request.message_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 生成编辑 ────────────────────────────────────────────────────────────


@router.post("/generate-edit")
async def generate_edit(
    request: GenerateEditRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
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
    _: dict = Depends(access_control.require_workspace_write),
):
    """把某条 assistant 消息附带的编辑操作写回源文件。"""
    from backend.core.project_manager import FileConflictError

    service = get_writing_chat_service()
    try:
        return await service.apply_edit(request, workspace_id)
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
        result = await service.delete_message(request)
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
    ),
    _: dict = Depends(access_control.require_workspace(write=False, required=False)),
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
    if not await service.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.patch("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: RenameSessionRequest):
    """重命名会话。"""
    service = get_writing_chat_service()
    try:
        return await service.rename_session(session_id, request.title)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/compact")
async def compact_session(session_id: str):
    """手动压缩会话（不追加用户消息，保留最近轮次原样）。"""
    service = get_writing_chat_service()
    try:
        return await service.compact_session(session_id)
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
    try:
        content = pm.load_prompt(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if content is None:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {name}")
    return {"name": name, "content": content}


@router.post("/prompts")
async def save_prompt(
    request: AssistantSaveRequest,
    _: None = Depends(require_admin_if_multiuser),
):
    """保存（创建或更新）助手提示词。

    自定义提示词为全局共享资源：多用户模式下写操作仅限管理员
    （单用户模式不受影响）；读取保持对所有登录用户开放。"""
    pm = get_prompt_manager()
    try:
        pm.save_prompt(request.name, request.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "name": request.name}


@router.delete("/prompts/{name}")
async def delete_prompt(
    name: str,
    _: None = Depends(require_admin_if_multiuser),
):
    """删除指定助手提示词（多用户模式仅管理员，见 save_prompt 说明）。"""
    pm = get_prompt_manager()
    try:
        deleted = pm.delete_prompt(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {name}")
    return {"status": "ok"}


@router.post("/prompts/active")
async def set_active_prompt(
    request: AssistantSetActiveRequest,
    _: None = Depends(require_admin_if_multiuser),
):
    """设置当前激活的助手（多用户模式仅管理员，见 save_prompt 说明）。"""
    pm = get_prompt_manager()
    try:
        pm.set_active(request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "name": request.name}

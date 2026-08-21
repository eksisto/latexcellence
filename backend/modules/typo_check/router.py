"""
文本校对 API 路由。

提供段落查看（含 LaTeX 剥离后的纯文本）、错字检查和修改生效等接口。
"""

import json


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core import access_control
from backend.core.base_paragraph_check import ApplyChangeItem, inject_expected_md5s
from backend.modules.typo_check.service import TypoCheckService

router = APIRouter(prefix="/api/typo-check", tags=["typo_check"])


def get_service() -> TypoCheckService:
    # 每次请求创建独立实例：BaseParagraphCheckService 持有 marker 映射等
    # 请求级状态，模块级单例会在并发请求间互相覆盖（结果串台/写回错误）。
    # 操作上下文（operation_id → marker 映射）保存在进程级共享存储中，
    # apply 请求仍可通过 operation_id 精确还原。
    return TypoCheckService()


# ── request models ─────────────────────────────────────────────────────────


class TypoCheckRequest(BaseModel):
    file_path: str
    paragraph_indices: list[int] | None = None
    resolve_input: bool = False
    language: str = "zh"


class TypoApplyRequest(BaseModel):
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
    预览指定文件的段落（含 LaTeX 剥离后的纯文本）。

    返回每段的原始文本和剥离后的纯文本，供前端展示。
    LLM 将基于纯文本进行错字检查。
    当 resolve_input=True 时，递归解析 \\input/\\include 引用并返回所有子文件的段落。
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
async def check_typos(
    request: TypoCheckRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    对指定段落的纯文本进行错字检查。

    调用 LLM 逐段检查拼写和语法错误，输出经 Pydantic 校验。
    仅检查 paragraph_indices 中指定的段落；未指定时检查所有段落。
    """
    try:
        result = await get_service().check_typos(
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
    request: TypoApplyRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """
    将用户审核确认的修改写回源文件。

    每个 change 包含 paragraph_index 和 new_text（修正后的纯文本）。
    后端通过 marker_map 将纯文本还原为完整 LaTeX 后写回。
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
async def stream_typo_check(
    workspace_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    language: str = Query("zh", description="前端 UI 语言"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    SSE 流式端点：按段落逐批推送错字检查进度和结果。

    事件格式：
        { "type": "progress", "current": 3, "total": 12, "file_path": "..." }
        { "type": "result", "paragraph_index": 0, "data": {...} }
        { "type": "error", "message": "...", "retrying": true }
        { "type": "done" }
    """

    async def event_stream():
        try:
            async for event in get_service().check_typos_stream(
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

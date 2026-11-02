"""
逐段翻译 API 路由。

提供段落查看（含 LaTeX 剥离后的纯文本）、翻译和修改生效等接口。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core import access_control
from backend.core.base_paragraph_check import ApplyChangeItem, inject_expected_md5s
from backend.modules.paragraph_translate.service import ParagraphTranslateService

router = APIRouter(prefix="/api/paragraph-translate", tags=["paragraph_translate"])


def get_service() -> ParagraphTranslateService:
    # 每次请求创建独立实例，避免并发请求共享 marker 映射/语言等请求级状态。
    return ParagraphTranslateService()


# ── request models ─────────────────────────────────────────────────────────


class ParagraphTranslateRequest(BaseModel):
    file_path: str
    paragraph_indices: list[int] | None = None
    resolve_input: bool = False
    language: str = "zh"
    target_language: str = "English"


class ParagraphTranslateApplyRequest(BaseModel):
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
    LLM 将基于纯文本进行翻译。
    当 resolve_input=True 时，递归解析 \\input/\\include 引用并返回所有子文件的段落。
    """
    try:
        result = await get_service().preview_paragraphs_async(
            file_path, resolve_input, workspace_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── translate ──────────────────────────────────────────────────────────────


@router.post("/translate")
async def translate_paragraphs(
    request: ParagraphTranslateRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    对指定段落的纯文本进行翻译。

    调用 LLM 逐段翻译为目标语言，输出经 Pydantic 校验。
    仅翻译 paragraph_indices 中指定的段落；未指定时翻译所有段落。
    """
    try:
        result = await get_service().translate(
            request.file_path,
            request.paragraph_indices,
            request.resolve_input,
            workspace_id,
            request.language,
            request.target_language,
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
    request: ParagraphTranslateApplyRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """
    将用户审核确认的翻译写回源文件。

    每个 change 包含 paragraph_index 和 new_text（翻译后的纯文本）。
    后端通过 marker_map 将纯文本还原为完整 LaTeX 后写回。
    """
    try:
        changes = [c.model_dump() for c in request.changes]
        inject_expected_md5s(changes, request.file_path, request.expected_md5s)
        result = await get_service().apply_changes_async(
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
async def stream_paragraph_translate(
    workspace_id: str,
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    resolve_input: bool = Query(False, description="是否递归展开 \\input 引用"),
    language: str = Query("zh", description="前端 UI 语言"),
    target_language: str = Query("English", description="翻译目标语言"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    SSE 流式端点：按段落逐批推送翻译进度和结果。

    事件格式：
        { "type": "progress", "current": 3, "total": 12, "file_path": "..." }
        { "type": "result", "paragraph_index": 0, "data": {...} }
        { "type": "error", "message": "...", "retrying": true }
        { "type": "done" }
    """

    async def event_stream():
        try:
            async for event in get_service().translate_stream(
                file_path, resolve_input, workspace_id, language, target_language
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

"""Git 差异高亮 API 路由。"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger
from backend.modules.git_diff.service import (
    get_pdf_path,
    list_git_revisions,
    render_diff_pdf,
)


router = APIRouter(prefix="/api/git-diff", tags=["git-diff"])
logger = get_logger(__name__)


class DiffRenderRequest(BaseModel):
    """差异高亮生成请求。"""

    model_config = ConfigDict(extra="forbid")

    main_file: str
    base_ref: str
    target_ref: str
    # 高级选项
    diff_engine: str = "difflib"  # difflib / latexdiff
    markup_type: str = "UNDERLINE"  # latexdiff 标记类型
    math_markup: str = "fine"  # latexdiff 数学标记粒度
    color_add: str = ""  # 新增内容颜色（HEX，空则用配置默认值）
    color_del: str = ""  # 删除内容颜色（HEX，空则用配置默认值）
    exclude_safecmd: str = ""  # 排除的安全命令列表（逗号分隔）
    exclude_textcmd: str = ""  # 排除的文本命令列表（逗号分隔）


@router.get("/refs")
async def get_refs(workspace_id: str = Query(..., description="工作区唯一标识符")):
    """获取当前项目可用于差异比较的 Git revision。"""

    try:
        result = await list_git_revisions(workspace_id)
        return result.model_dump()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/render-pdf")
async def render_pdf(request: DiffRenderRequest, workspace_id: str = Query(..., description="工作区唯一标识符")):
    """生成差异高亮 PDF。"""

    try:
        result = await render_diff_pdf(
            request.main_file,
            request.base_ref,
            request.target_ref,
            workspace_id,
            diff_engine=request.diff_engine,
            markup_type=request.markup_type,
            math_markup=request.math_markup,
            color_add=request.color_add,
            color_del=request.color_del,
            exclude_safecmd=request.exclude_safecmd,
            exclude_textcmd=request.exclude_textcmd,
        )
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pdf/{job_id}")
async def download_pdf(job_id: str):
    """
    返回已生成的差异 PDF。

    使用 ``Content-Disposition: inline`` 以便浏览器在 iframe 中内联预览，
    而不是触发自动下载。用户如需下载，前端“下载 PDF”按钮通过 ``<a download>``
    强制下载。
    """

    try:
        pdf_path = get_pdf_path(job_id)
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename="diff-highlight.pdf",
            content_disposition_type="inline",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

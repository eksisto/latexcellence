"""Git 差异高亮 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from backend.core import access_control
from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.git_diff.service import (
    LATEXDIFF_MATH_MARKUPS,
    LATEXDIFF_TYPES,
    get_pdf_path,
    list_git_revisions,
    render_diff_pdf,
)
from backend.utils.sandbox import to_http_exception

router = APIRouter(prefix="/api/git-diff", tags=["git-diff"])
logger = get_logger(__name__)


class DiffOptions(BaseModel):
    """差异引擎可选项。"""

    markup_types: list[str] = LATEXDIFF_TYPES
    math_markups: list[str] = LATEXDIFF_MATH_MARKUPS
    default_engine: str = "difflib"
    default_markup_type: str = "UNDERLINE"
    default_math_markup: str = "fine"
    default_color_add: str = "#D7E8B1"
    default_color_del: str = "#FFA3A3"


@router.get("/options")
async def get_options():
    """获取差异引擎支持的选项（标记类型、数学标记粒度）。"""
    git_diff_cfg = get_config().features.git_diff
    return DiffOptions(
        default_engine=git_diff_cfg.default_engine,
        default_markup_type=git_diff_cfg.default_markup_type,
        default_math_markup=git_diff_cfg.default_math_markup,
        default_color_add=git_diff_cfg.default_color_add,
        default_color_del=git_diff_cfg.default_color_del,
    ).model_dump()


class DiffRenderRequest(BaseModel):
    """差异高亮生成请求。"""

    model_config = ConfigDict(extra="forbid")

    main_file: str
    base_ref: str
    target_ref: str
    # 高级选项
    # 置空时由服务端读取 features.git_diff 配置默认值
    diff_engine: str | None = None  # difflib / latexdiff
    markup_type: str | None = None  # latexdiff 标记类型
    math_markup: str | None = None  # latexdiff 数学标记粒度
    color_add: str = ""  # 新增内容颜色（HEX，空则用配置默认值）
    color_del: str = ""  # 删除内容颜色（HEX，空则用配置默认值）
    exclude_safecmd: str = ""  # 排除的安全命令列表（逗号分隔）
    exclude_textcmd: str = ""  # 排除的文本命令列表（逗号分隔）


@router.get("/refs")
async def get_refs(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取当前项目可用于差异比较的 Git revision。"""

    try:
        result = await list_git_revisions(workspace_id)
        return result.model_dump()
    except RuntimeError as e:
        # 沙箱基础设施错误（503/429）优先于通用 400 映射
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            raise sandbox_http from e
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/render-pdf")
async def render_pdf(
    request: DiffRenderRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
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
        # 沙箱基础设施错误（503/429）优先于通用 400 映射
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            raise sandbox_http from e
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pdf/{job_id}")
async def download_pdf(
    job_id: str,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    返回当前工作区已生成的差异 PDF。

    与 /refs、/render-pdf 一致要求工作区读权限：job_id 虽不可枚举，
    但持有 id 的任意登录用户不应能下载他人项目的差异 PDF。
    响应以 ``Content-Disposition: inline`` 返回，便于浏览器在 iframe
    中内联预览；前端“下载 PDF”按钮通过 ``<a download>`` 强制下载。
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

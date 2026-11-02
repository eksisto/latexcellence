"""
自动编译 API 路由。

提供 LaTeX 项目编译和错误翻译接口。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.core import access_control
from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.compile.service import (
    _validate_main_file,
    compile_latex,
    synctex_backward,
    synctex_forward,
)
from backend.utils.sandbox import to_http_exception

router = APIRouter(prefix="/api/compile", tags=["compile"])
logger = get_logger(__name__)


def _sanitize_main_file_str(v: str) -> str:
    """main_file 字段共享清洗（CompileRequest 与 synctex 请求模型同规则）。"""
    v = v.strip()
    if not v:
        raise ValueError("main_file must not be empty")
    if v.startswith("-"):
        raise ValueError("main_file must not start with '-'")
    return v


class CompileRequest(BaseModel):
    main_file: str
    language: str = "zh"
    engine: str = "auto"  # auto/pdflatex/xelatex/lualatex
    pdfa: bool = False  # PDF/A-2b 归档格式输出
    synctex: bool = False  # 生成 SyncTeX 数据（编辑器正反向同步；editor v3 Q3）
    # 编译前清理可再生产物（编辑器编译链路传 true；既有调用方默认 false 零变化）
    clean: bool = False

    @field_validator("main_file")
    @classmethod
    def sanitize_main_file(cls, v: str) -> str:
        return _sanitize_main_file_str(v)

    @field_validator("engine")
    @classmethod
    def validate_engine(cls, v: str) -> str:
        valid_engines = ("auto", "pdflatex", "xelatex", "lualatex")
        if v not in valid_engines:
            raise ValueError(
                f"Invalid engine: {v}. Must be one of: {', '.join(valid_engines)}"
            )
        return v


@router.post("/run")
async def run_compile(
    request: CompileRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    编译 LaTeX 项目。

    使用 latexmk 编译主文件，支持自动引擎检测和 PDF/A 输出。
    clean=true 时编译前先清理项目内可再生产物（编辑器链路启用）。
    若编译失败，使用 LLM 将错误信息翻译为通俗易懂的解释和修复建议。
    """
    try:
        result = await compile_latex(
            request.main_file,
            workspace_id,
            request.language,
            engine=request.engine,
            pdfa=request.pdfa,
            synctex=request.synctex,
            clean_first=request.clean,
        )
        return result.model_dump()
    except RuntimeError as e:
        # 沙箱基础设施错误（503/429）优先于通用 400 映射
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            raise sandbox_http from e
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # main_file 路径穿越 / 非法后缀 / 符号链接（path_guard 拒绝）按 400 语义返回
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pdf")
async def get_compiled_pdf(
    main_file: str = Query(..., description="主 .tex 文件相对路径"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    返回最近一次编译产出的 PDF 文件。

    latexmk 在项目根目录下生成与主文件同名的 PDF（stem.pdf）。
    使用 ``Content-Disposition: inline`` 以便浏览器在 iframe 中内联预览，
    前端可在编译成功后通过此端点内联预览。
    """
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise HTTPException(status_code=400, detail="No project opened")

    root = Path(project.root_path)

    try:
        _validate_main_file(main_file, root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pdf_path = _validate_main_file(main_file, root).with_suffix(".pdf")

    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found: {pdf_path.name}. Please compile the project first.",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
        content_disposition_type="inline",
    )


class SynctexForwardRequest(BaseModel):
    """正向同步请求：源码位置 → PDF 页面矩形（editor v3 阶段一）。"""

    model_config = ConfigDict(extra="forbid")

    main_file: str
    file_path: str
    # 1 基行号（编辑器缓冲区行号直发，脏缓冲区偏差容忍不补偿，Q4）；
    # col 为 synctex 原生 0 基列号（前端由 1 基列换算）
    line: int = Field(ge=1)
    col: int = Field(default=0, ge=0)

    @field_validator("main_file")
    @classmethod
    def sanitize_main_file(cls, v: str) -> str:
        return _sanitize_main_file_str(v)

    @field_validator("file_path")
    @classmethod
    def sanitize_file_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("file_path must not be empty")
        return v


class SynctexBackwardRequest(BaseModel):
    """反向同步请求：PDF 点位 → 源码位置（editor v3 阶段一）。"""

    model_config = ConfigDict(extra="forbid")

    main_file: str
    page: int = Field(ge=1)
    # 72dpi 点单位，页面左上原点（synctex 约定；前端已完成 y 翻转）。
    # 注：不做 allow_inf_nan=False——NaN/inf 经 pydantic 校验拒绝会让
    # RequestValidationError 的 input 带上非有限值，422 响应体无法 JSON
    # 序列化（Starlette allow_nan=False）反而 500；放行后由 synctex 查询
    # 自然落空，收敛为 found=false 的合法空结果
    x: float
    y: float

    @field_validator("main_file")
    @classmethod
    def sanitize_main_file(cls, v: str) -> str:
        return _sanitize_main_file_str(v)


def _synctex_http_error(e: Exception) -> HTTPException:
    """synctex 端点共享异常映射：沙箱基础设施错误优先，其余按既有
    /api/compile/run 规则收敛（RuntimeError→400 / ValueError→400 /
    FileNotFoundError→404）。"""
    if isinstance(e, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, RuntimeError):
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            return sandbox_http
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=400, detail=str(e))


@router.post("/synctex/forward")
async def run_synctex_forward(
    request: SynctexForwardRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    正向同步：源码位置查询 PDF 页面矩形。

    只读零写盘：synctex view 经 SandboxRunner 在项目根目录执行，
    返回 72dpi 点单位记录（页面左上原点），响应不含绝对路径。
    """
    try:
        result = await synctex_forward(
            workspace_id,
            request.main_file,
            request.file_path,
            request.line,
            col=request.col,
        )
        return result.model_dump()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        raise _synctex_http_error(e) from e


@router.post("/synctex/backward")
async def run_synctex_backward(
    request: SynctexBackwardRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """
    反向同步：PDF 点位查询源码位置。

    只读零写盘：synctex edit 经 SandboxRunner 在项目根目录执行，
    返回项目内相对路径与行号；未命中返回 found=false（合法空结果）。
    """
    try:
        result = await synctex_backward(
            workspace_id,
            request.main_file,
            request.page,
            request.x,
            request.y,
        )
        return result.model_dump()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        raise _synctex_http_error(e) from e

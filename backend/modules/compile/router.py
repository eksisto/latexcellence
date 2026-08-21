"""
自动编译 API 路由。

提供 LaTeX 项目编译和错误翻译接口。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from backend.core import access_control
from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.compile.service import compile_latex, _validate_main_file

router = APIRouter(prefix="/api/compile", tags=["compile"])
logger = get_logger(__name__)


class CompileRequest(BaseModel):
    main_file: str
    language: str = "zh"
    engine: str = "auto"  # auto/pdflatex/xelatex/lualatex
    pdfa: bool = False  # PDF/A-2b 归档格式输出

    @field_validator("main_file")
    @classmethod
    def sanitize_main_file(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("main_file must not be empty")
        if v.startswith("-"):
            raise ValueError("main_file must not start with '-'")
        return v

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
    若编译失败，使用 LLM 将错误信息翻译为通俗易懂的解释和修复建议。
    """
    try:
        result = await compile_latex(
            request.main_file,
            workspace_id,
            request.language,
            engine=request.engine,
            pdfa=request.pdfa,
        )
        return result.model_dump()
    except RuntimeError as e:
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

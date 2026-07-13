"""
自动编译 API 路由。

提供 LaTeX 项目编译和错误翻译接口。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.modules.compile.service import compile_latex

router = APIRouter(prefix="/api/compile", tags=["compile"])
logger = get_logger(__name__)


class CompileRequest(BaseModel):
    main_file: str


@router.post("/run")
async def run_compile(request: CompileRequest):
    """
    编译 LaTeX 项目。

    使用 latexmk -xelatex 编译主文件，拦截 stderr 输出。
    若编译失败，使用 LLM 将错误信息翻译为通俗易懂的解释和修复建议。
    """
    try:
        result = await compile_latex(request.main_file)
        return result.model_dump()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/pdf")
async def get_compiled_pdf(
    main_file: str = Query(..., description="主 .tex 文件相对路径"),
):
    """
    返回最近一次编译产出的 PDF 文件。

    latexmk 在项目根目录下生成与主文件同名的 PDF（stem.pdf）。
    使用 ``Content-Disposition: inline`` 以便浏览器在 iframe 中内联预览，
    前端可在编译成功后通过此端点内联预览。
    """
    project = get_project_manager()
    if project._project_info is None:
        raise HTTPException(status_code=400, detail="No project opened")

    root = Path(project._project_info.root_path)
    pdf_path = root / Path(main_file).with_suffix(".pdf")

    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found: {pdf_path.name}. 请先编译项目。",
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
        content_disposition_type="inline",
    )

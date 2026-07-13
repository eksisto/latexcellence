"""
自动编译 API 路由。

提供 LaTeX 项目编译和错误翻译接口。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.logger import get_logger
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

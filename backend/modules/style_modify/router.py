"""
LaTeX 样式修改 API 路由。

提供 LaTeX 代码样式修改接口：用户粘贴 LaTeX 源码和自然语言修改描述，
LLM 根据文字描述修改 LaTeX 代码并返回。
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend.modules.style_modify.service import StyleModifyService

router = APIRouter(prefix="/api/style-modify", tags=["style_modify"])

_service: Optional[StyleModifyService] = None


def get_service() -> StyleModifyService:
    global _service
    if _service is None:
        _service = StyleModifyService()
    return _service


class ModifyRequest(BaseModel):
    """样式修改请求体"""
    latex: str
    description: str

    @field_validator("latex")
    @classmethod
    def latex_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("LaTeX code cannot be empty")
        return v

    @field_validator("description")
    @classmethod
    def description_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v


@router.post("/modify")
async def modify_latex(request: ModifyRequest):
    """
    根据用户描述修改 LaTeX 代码。

    请求体：
        { latex: "...", description: "..." }

    返回格式：
        { latex: "..." }
    """
    try:
        result = await get_service().modify(request.latex, request.description)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

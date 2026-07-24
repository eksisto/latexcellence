"""
模板生成 API 路由。

提供模板生成和保存接口。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.template_gen.service import TemplateGenService

router = APIRouter(prefix="/api/template-gen", tags=["template_gen"])

_service: "TemplateGenService | None" = None


def get_service() -> TemplateGenService:
    global _service
    if _service is None:
        _service = TemplateGenService()
    return _service


class TemplateGenRequest(BaseModel):
    description: str
    language: str = "zh"


class TemplateSaveRequest(BaseModel):
    template: dict


@router.post("/generate")
async def generate_template(request: TemplateGenRequest):
    """
    根据用户描述生成 LaTeX 模板。

    返回 LaTeX preamble、body 骨架、推荐包列表和说明。
    """
    try:
        result = await get_service().generate_template(request.description, request.language)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def save_template(request: TemplateSaveRequest):
    """
    将生成的模板保存为 .tex 文件到配置的输出目录。
    """
    try:
        result = await get_service().save_template(request.template)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

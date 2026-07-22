"""
参考文献管理 API 路由。

提供格式分析、元数据提取、BibTeX 生成和 .bib 文件读写接口。
"""

import json


from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.modules.bib_manager.models import (
    FormatAnalysisRequest,
    GenerateBibtexRequest,
    GenerateLatexRequest,
    ReadBibRequest,
    SaveBibRequest,
)
from backend.modules.bib_manager.service import BibManagerService
from backend.modules.bib_manager.style_registry import list_all_styles

router = APIRouter(prefix="/api/bib-manager", tags=["bib_manager"])

_service: BibManagerService | None = None


def get_service() -> BibManagerService:
    global _service
    if _service is None:
        _service = BibManagerService()
    return _service


class BibAnalysisResponse(BaseModel):
    format_name: str
    entry_type: str
    required_fields: list[str] = []
    optional_fields: list[str] = []
    field_descriptions: dict[str, str] = {}
    format_description: str = ""


@router.post("/analyze-format")
async def analyze_format(request: FormatAnalysisRequest):
    """分析一条参考文献示例的格式规则。"""
    if not request.example.strip():
        raise HTTPException(status_code=400, detail="Empty reference example")
    try:
        result = await get_service().analyze_format(request.example)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract")
async def extract_metadata(
    text: str | None = Form(None),
    image: UploadFile | None = File(None),
    format_info: str | None = Form(None),
):
    """从文本或图片中提取文献元数据。"""
    try:
        parsed_format_info = None
        if format_info:
            try:
                parsed_format_info = json.loads(format_info)
            except json.JSONDecodeError:
                pass

        svc = get_service()

        if image and image.filename:
            image_data = await image.read()
            if not image_data:
                raise HTTPException(status_code=400, detail="Empty image file")
            content_type = image.content_type or "image/png"
            try:
                result = await svc.extract_from_image(
                    image_data, content_type, parsed_format_info
                )
                return result
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        if text and text.strip():
            return await svc.extract_from_text(text, parsed_format_info)

        raise HTTPException(
            status_code=400,
            detail="Either text or image must be provided",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
async def generate_bibtex(request: GenerateBibtexRequest):
    """将结构化元数据生成为 BibTeX 代码。"""
    if not request.metadata:
        raise HTTPException(status_code=400, detail="Empty metadata")
    try:
        result = await get_service().generate_bibtex(
            request.metadata,
            request.format_info,
            request.existing_keys,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/read-bib")
async def read_bib_file(request: ReadBibRequest, workspace_id: str = Query(..., description="工作区唯一标识符")):
    """读取项目中的 .bib 文件内容。"""
    try:
        result = get_service().read_bib_file(request.file_path, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/save")
async def save_bib_entry(request: SaveBibRequest, workspace_id: str = Query(..., description="工作区唯一标识符")):
    """将 BibTeX 条目写入或更新到 .bib 文件。"""
    if not request.bibtex_code.strip():
        raise HTTPException(status_code=400, detail="Empty BibTeX code")
    try:
        result = get_service().save_bib_entry(
            request.file_path, request.bibtex_code, request.cite_key, workspace_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list-bib-files")
async def list_bib_files(workspace_id: str = Query(..., description="工作区唯一标识符")):
    """列出当前项目中的所有 .bib 文件。"""
    try:
        result = get_service().list_bib_files(workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── format CRUD ────────────────────────────────────────────────────────────

class FormatSaveRequest(BaseModel):
    name: str
    format_info: dict


@router.get("/formats")
async def list_formats():
    """列出所有已保存的参考文献格式。"""
    try:
        result = get_service().list_formats()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/formats/{name}")
async def load_format(name: str):
    """加载指定格式配置。"""
    data = get_service().load_format(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Format not found: {name}")
    return data


@router.post("/formats")
async def save_format(request: FormatSaveRequest):
    """保存格式配置。"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Format name required")
    try:
        result = get_service().save_format(request.name.strip(), request.format_info)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/formats/{name}")
async def delete_format(name: str):
    """删除指定格式配置。"""
    if not get_service().delete_format(name):
        raise HTTPException(status_code=404, detail=f"Format not found: {name}")
    return {"status": "ok"}


# ── LaTeX 样式相关 ─────────────────────────────────────────────────────────

@router.get("/latex-styles")
async def get_latex_styles():
    """列出所有内置的 LaTeX 样式映射。"""
    return {"styles": list_all_styles()}


@router.post("/generate-latex")
async def generate_latex_code(request: GenerateLatexRequest):
    """根据格式信息生成包含正确样式的 LaTeX 参考文献代码。"""
    if not request.metadata:
        raise HTTPException(status_code=400, detail="Empty metadata")
    try:
        result = get_service().generate_latex_code(
            request.metadata,
            request.format_info,
            request.bib_file_path,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

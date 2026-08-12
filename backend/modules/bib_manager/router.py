"""
参考文献管理 API 路由。

提供格式分析、元数据提取、BibTeX 生成和 .bib 文件读写接口。
"""

import json


from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.modules.bib_manager.models import (
    DeleteEntryRequest,
    DetectDuplicatesRequest,
    DoiLookupRequest,
    FormatAnalysisRequest,
    GenerateBibtexRequest,
    GenerateLatexRequest,
    ParseBibRequest,
    ReadBibRequest,
    SaveBibRequest,
    SavedFormatInfo,
    UpdateEntryRequest,
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
    images: list[UploadFile] = File(default=[]),
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

        valid_images = [img for img in images if img.filename]
        if valid_images:
            image_items: list[tuple[bytes, str]] = []
            for img in valid_images:
                image_data = await img.read()
                if not image_data:
                    continue
                content_type = img.content_type or "image/png"
                image_items.append((image_data, content_type))
            if not image_items:
                raise HTTPException(status_code=400, detail="Empty image files")
            try:
                result = await svc.extract_from_images(image_items, parsed_format_info)
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


async def _doi_exception_wrap(coro):
    """DOI 查询异常统一映射：ValueError→404, RuntimeError→502, 其它→500。"""
    try:
        return await coro
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/doi-lookup")
async def doi_lookup(request: DoiLookupRequest):
    """通过 DOI 查询多数据源聚合元数据。"""
    if not request.doi.strip():
        raise HTTPException(status_code=400, detail="Empty DOI")
    return await _doi_exception_wrap(get_service().lookup_doi(request.doi))


@router.post("/generate-from-doi")
async def generate_from_doi(request: GenerateBibtexRequest):
    """通过 DOI 查询元数据并生成 BibTeX 代码。"""
    if not request.doi:
        raise HTTPException(status_code=400, detail="DOI is required")
    return await _doi_exception_wrap(
        get_service().generate_bibtex_from_doi(
            request.doi,
            request.format_info,
            request.existing_keys,
        )
    )


@router.post("/read-bib")
async def read_bib_file(
    request: ReadBibRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """读取项目中的 .bib 文件内容。"""
    try:
        result = get_service().read_bib_file(request.file_path, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/save")
async def save_bib_entry(
    request: SaveBibRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
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
async def list_bib_files(
    workspace_id: str = Query(..., description="工作区唯一标识符")
):
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
    format_info: SavedFormatInfo


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
    try:
        data = get_service().load_format(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Format not found: {name}")
    return data


@router.post("/formats")
async def save_format(request: FormatSaveRequest):
    """保存格式配置。"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Format name required")
    try:
        result = get_service().save_format(
            request.name.strip(), request.format_info.model_dump()
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/formats/{name}")
async def delete_format(name: str):
    """删除指定格式配置。"""
    try:
        deleted = get_service().delete_format(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
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
            request.language,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 文献库管理 ──────────────────────────────────────────────────────────────


@router.post("/parse-entries")
async def parse_entries(
    request: ParseBibRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """解析 .bib 文件为结构化条目列表。"""
    try:
        result = get_service().parse_entries(request.file_path, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detect-duplicates")
async def detect_duplicates(
    request: DetectDuplicatesRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """检测 .bib 文件中的重复条目。"""
    try:
        result = get_service().detect_duplicates(request.file_path, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete-entry")
async def delete_entry(
    request: DeleteEntryRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """从 .bib 文件中删除指定条目。"""
    try:
        result = get_service().delete_entry(
            request.file_path, request.cite_key, workspace_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-entry")
async def update_entry(
    request: UpdateEntryRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """更新 .bib 文件中的指定条目。"""
    if not request.bibtex_code.strip():
        raise HTTPException(status_code=400, detail="Empty BibTeX code")
    try:
        result = get_service().update_entry(
            request.file_path, request.cite_key, request.bibtex_code, workspace_id
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entry-stats")
async def get_entry_stats(
    file_path: str = Query(..., description=".bib 文件路径"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
):
    """获取 .bib 文件的条目统计信息。"""
    try:
        result = get_service().get_entry_stats(file_path, workspace_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

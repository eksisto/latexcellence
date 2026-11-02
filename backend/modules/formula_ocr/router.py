"""
公式识别 API 路由。

提供图片上传和 LaTeX 公式识别接口。
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.core.access_control import require_feature_enabled
from backend.core.config import get_config
from backend.modules.formula_ocr.service import FormulaOCRService


def _feature_gate() -> bool:
    """多用户模式下功能开关关闭时端点整体 404（实时读取，热重载生效）。"""
    return get_config().features.formula_ocr.enabled


router = APIRouter(
    prefix="/api/formula-ocr",
    tags=["formula_ocr"],
    dependencies=[Depends(require_feature_enabled(_feature_gate))],
)

_service: "FormulaOCRService | None" = None


def get_service() -> FormulaOCRService:
    global _service
    if _service is None:
        _service = FormulaOCRService()
    return _service


@router.post("/recognize")
async def recognize_formula(
    file: UploadFile = File(..., description="公式图片（PNG / JPEG / WebP）"),
):
    """
    上传公式图片，返回识别后的 LaTeX 代码。

    返回格式：
        { latex: "...", inline: true/false, confidence: 0.95 }
        confidence < 0.7 时附加 low_confidence: true 警告。
    """
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected")

        image_data = await file.read()

        if not image_data:
            raise HTTPException(status_code=400, detail="Empty file")

        result = await get_service().recognize(
            image_data,
            content_type=file.content_type or "image/png",
        )
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

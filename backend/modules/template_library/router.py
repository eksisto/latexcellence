"""模板库 API 路由（薄路由层，挂载 /api/templates）。

- 仅做参数校验与状态码映射，业务逻辑在 service.py
- enabled=false 时 /api/templates 端点整体关闭（404，对齐认证依赖语义）
- 认证：多用户模式强制登录（401），单用户模式放行（双模式语义）
- 会话类资源按用户隔离：依赖注入请求级当前用户上下文（幂等，见
  dependencies.current_user_context），配额记账与用户库定位均依赖该上下文
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.core.access_control import require_workspace_read_json
from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import userspace
from backend.core.multiuser.dependencies import (
    current_user_context,
    get_current_user_optional,
    unauthorized,
)

from . import service
from .models import (
    CreateProjectFromTemplateRequest,
    SaveProjectAsTemplateRequest,
    SaveTemplateGenRequest,
    UpdateTemplateRequest,
)

logger = get_logger(__name__)


async def _require_feature_enabled() -> None:
    """模板库功能开关：关闭时端点整体关闭（不泄露存在性）。"""
    if not get_config().features.template_library.enabled:
        raise HTTPException(status_code=404, detail="Not found")


async def _current_actor(
    user: dict | None = Depends(get_current_user_optional),
) -> dict | None:
    """双模式认证：多用户强制登录，单用户返回 None（走全局库目录）。"""
    if userspace.is_multiuser() and user is None:
        raise unauthorized()
    return user


router = APIRouter(
    prefix="/api/templates",
    tags=["template_library"],
    dependencies=[Depends(_require_feature_enabled), Depends(current_user_context)],
)


@router.get("")
async def list_templates(
    category: str | None = Query(default=None, description="按分类筛选"),
    actor: dict | None = Depends(_current_actor),
):
    """模板列表（内置 + 我的），支持 category 筛选。"""
    return await service.list_templates(actor, category)


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    actor: dict | None = Depends(_current_actor),
):
    """模板详情 + 文件树（相对路径）。"""
    return await service.get_template(actor, template_id)


@router.get("/{template_id}/file")
async def read_template_file(
    template_id: str,
    path: str = Query(..., description="模板内相对路径"),
    actor: dict | None = Depends(_current_actor),
):
    """模板内源码查看（文本白名单）。"""
    return await service.read_file(actor, template_id, path)


@router.get("/{template_id}/thumbnail")
async def get_thumbnail(
    template_id: str,
    actor: dict | None = Depends(_current_actor),
):
    """内置模板缩略图（仅内置）。"""
    resolved, media_type = await service.resolve_builtin_asset(
        actor, template_id, "thumbnail"
    )
    return FileResponse(
        path=str(resolved), media_type=media_type, content_disposition_type="inline"
    )


@router.get("/{template_id}/preview")
async def get_preview(
    template_id: str,
    actor: dict | None = Depends(_current_actor),
):
    """内置模板 PDF 预览（仅内置）。"""
    resolved, media_type = await service.resolve_builtin_asset(
        actor, template_id, "preview"
    )
    return FileResponse(
        path=str(resolved), media_type=media_type, content_disposition_type="inline"
    )


@router.post("", status_code=201)
async def upload_template(
    archive: UploadFile = File(..., description="模板 zip 压缩包"),
    name: str | None = Form(default=None, description="可选模板名，缺省取文件名"),
    category: str | None = Form(default=None, description="可选分类，缺省 other"),
    actor: dict | None = Depends(_current_actor),
):
    """上传 zip 建模板（安全解压 + 扩展名白名单；无 manifest 自动生成）。"""
    return await service.upload_template(actor, archive, name, category)


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    req: UpdateTemplateRequest,
    actor: dict | None = Depends(_current_actor),
):
    """改名/分类/描述（仅自己的模板）。"""
    return await service.update_template(
        actor,
        template_id,
        name=req.name,
        category=req.category,
        description=req.description,
    )


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    actor: dict | None = Depends(_current_actor),
):
    """删除自己的模板（内置只读）。"""
    return await service.delete_template(actor, template_id)


@router.post("/{template_id}/create-project", status_code=201)
async def create_project_from_template(
    template_id: str,
    req: CreateProjectFromTemplateRequest,
    actor: dict | None = Depends(_current_actor),
):
    """从模板创建项目（多用户配额预检超限 413；单用户 target_dir 落盘）。"""
    return await service.create_project(
        actor, template_id, name=req.name, target_dir=req.target_dir
    )


@router.post("/from-project", status_code=201)
async def save_project_as_template(
    req: SaveProjectAsTemplateRequest,
    binding: dict = Depends(require_workspace_read_json),
    actor: dict | None = Depends(_current_actor),
):
    """当前项目另存为模板（复用清洗规则）。"""
    return await service.save_project_as_template(
        actor,
        binding,
        name=req.name,
        category=req.category,
        description=req.description,
    )


@router.post("/from-template-gen", status_code=201)
async def save_template_gen(
    req: SaveTemplateGenRequest,
    actor: dict | None = Depends(_current_actor),
):
    """template_gen 会话产物保存到我的模板库（剔除 PDF/trace/辅助文件）。"""
    return await service.save_template_gen(
        actor,
        session_id=req.session_id,
        name=req.name,
        category=req.category,
        description=req.description,
    )

"""
多用户项目路由（薄路由层，P3）。

- GET/POST /api/projects：项目列表 / 上传创建
- PUT /api/projects/{project_id}：重命名（owner）
- DELETE /api/projects/{project_id}：进回收站（owner）
- POST /api/projects/{project_id}/restore：恢复（owner，超限禁止）
- DELETE /api/projects/{project_id}/permanent：永久删除（owner）
- GET /api/projects/{project_id}/download：打包下载 zip（读权限）

业务逻辑见 service.py；权限依赖只做最外层拦截，细分（owner-only）在 service 内实时校验。
"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from backend.core.multiuser.dependencies import get_current_user
from backend.modules.projects import service
from backend.modules.projects.models import RenameProjectRequest

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(user: dict = Depends(get_current_user)):
    """我的项目 + 被共享的项目（不含回收站）。"""
    return await service.list_projects(user)


@router.post("", status_code=201)
async def upload_project(
    archive: UploadFile = File(..., description="zip / 7z 压缩包"),
    name: str | None = Form(default=None, description="可选项目名，缺省取文件名"),
    user: dict = Depends(get_current_user),
):
    """上传压缩包创建项目（owner）。"""
    return await service.upload_create_project(user, archive, name)


@router.put("/{project_id}")
async def rename_project(
    project_id: str,
    req: RenameProjectRequest,
    user: dict = Depends(get_current_user),
):
    """重命名项目（owner）。"""
    return await service.rename_project(user["id"], project_id, req.name)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """项目进回收站（owner）；删除即腾出配额。"""
    return await service.delete_project(user["id"], project_id)


@router.post("/{project_id}/restore")
async def restore_project(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """从回收站恢复（owner，超限禁止）。"""
    return await service.restore_project(user["id"], project_id)


@router.delete("/{project_id}/permanent")
async def permanent_delete_project(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """提前永久删除回收站项目（owner）。"""
    return await service.permanent_delete_project(user["id"], project_id)


@router.get("/{project_id}/download")
async def download_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """打包下载项目 zip（owner / editor / viewer 均可）。"""
    zip_path, download_name, temp_root = await service.download_project(
        user["id"], project_id
    )
    background_tasks.add_task(_cleanup, temp_root)
    return FileResponse(
        zip_path,
        filename=download_name,
        media_type="application/zip",
        background=background_tasks,
    )


def _cleanup(temp_root: Path) -> None:
    import shutil

    shutil.rmtree(temp_root, ignore_errors=True)

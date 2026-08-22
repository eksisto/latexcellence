"""
多用户项目路由（薄路由层，P3 / P4 / v2 P5）。

- GET/POST /api/projects：项目列表 / 上传创建
- POST /api/projects/upload-sessions：创建分片上传会话（v2 P5）
- PUT /api/projects/upload-sessions/{sid}/parts/{file_index}/{chunk_index}：上传分片
- GET /api/projects/upload-sessions/{sid}：查询进度位图（断点续传）
- POST /api/projects/upload-sessions/{sid}/complete：校验拼装并创建项目
- DELETE /api/projects/upload-sessions/{sid}：中止并清理
- PUT /api/projects/{project_id}：重命名（owner）
- DELETE /api/projects/{project_id}：进回收站（owner）
- POST /api/projects/{project_id}/restore：恢复（owner，超限禁止）
- DELETE /api/projects/{project_id}/permanent：永久删除（owner）
- GET /api/projects/{project_id}/download：打包下载 zip（读权限）
- GET/POST /api/projects/{project_id}/permissions：协作者列表 / 按用户名添加（owner）
- PUT/DELETE /api/projects/{project_id}/permissions/{user_id}：改角色 / 移除（owner）
- POST /api/projects/{project_id}/transfer：转交 owner（owner）

业务逻辑见 service.py；权限依赖只做最外层拦截，细分（owner-only）在 service 内实时校验。
"""

from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from backend.core.multiuser.dependencies import get_current_user
from backend.modules.projects import service
from backend.modules.projects.models import (
    AddCollaboratorRequest,
    RenameProjectRequest,
    TransferProjectRequest,
    UpdateCollaboratorRequest,
    UploadSessionInitRequest,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(user: dict = Depends(get_current_user)):
    """我的项目 + 被共享的项目（不含回收站）。"""
    return await service.list_projects(user)


@router.get("/recycle-bin")
async def list_recycle_bin(user: dict = Depends(get_current_user)):
    """回收站列表（当前用户已删除项目），供工作台回收站入口使用。"""
    return await service.list_recycle_bin(user["id"])


@router.post("", status_code=201)
async def upload_project(
    archive: UploadFile = File(..., description="zip / 7z 压缩包"),
    name: str | None = Form(default=None, description="可选项目名，缺省取文件名"),
    user: dict = Depends(get_current_user),
):
    """上传压缩包创建项目（owner）。"""
    return await service.upload_create_project(user, archive, name)


# ── v2 P5 分片上传会话 ───────────────────────────────────────────────
@router.post("/upload-sessions", status_code=201)
async def create_upload_session(
    req: UploadSessionInitRequest,
    user: dict = Depends(get_current_user),
):
    """创建分片上传会话（manifest 预检 + 配额预检）。"""
    return await service.upload_session_init(user, req)


@router.put("/upload-sessions/{sid}/parts/{file_index}/{chunk_index}")
async def put_upload_part(
    sid: str,
    file_index: int,
    chunk_index: int,
    request: Request,
    x_part_md5: str | None = Header(default=None, alias="X-Part-MD5"),
    user: dict = Depends(get_current_user),
):
    """上传单个分片（二进制体 ≤ chunk_part_max_mb；幂等覆盖；成功续期）。"""
    return await service.upload_session_put_part(
        user, sid, file_index, chunk_index, x_part_md5, request
    )


@router.get("/upload-sessions/{sid}")
async def get_upload_session_status(
    sid: str,
    user: dict = Depends(get_current_user),
):
    """查询会话进度与每文件已收分块位图（断点续传对位），同时续期。"""
    return await service.upload_session_status(user, sid)


@router.post("/upload-sessions/{sid}/complete", status_code=201)
async def complete_upload_session(
    sid: str,
    user: dict = Depends(get_current_user),
):
    """完成会话：校验齐全 → 拼装 → 配额二次复核 → 创建项目。"""
    return await service.upload_session_complete(user, sid)


@router.delete("/upload-sessions/{sid}")
async def abort_upload_session(
    sid: str,
    user: dict = Depends(get_current_user),
):
    """中止会话并清理会话行与 staging 目录。"""
    return await service.upload_session_abort(user, sid)


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


# ── P4 权限协作 ───────────────────────────────────────────────────────
@router.get("/{project_id}/permissions")
async def list_permissions(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """协作者列表（owner 专属管理界面）。"""
    return await service.list_permissions(user["id"], project_id)


@router.post("/{project_id}/permissions", status_code=201)
async def add_collaborator(
    project_id: str,
    req: AddCollaboratorRequest,
    user: dict = Depends(get_current_user),
):
    """按用户名添加协作者（已存在则更新角色），owner 专属。"""
    return await service.add_collaborator(
        user["id"], project_id, req.username, req.role
    )


@router.put("/{project_id}/permissions/{collab_user_id}")
async def update_collaborator(
    project_id: str,
    collab_user_id: str,
    req: UpdateCollaboratorRequest,
    user: dict = Depends(get_current_user),
):
    """修改协作者角色，owner 专属。"""
    return await service.update_collaborator(
        user["id"], project_id, collab_user_id, req.role
    )


@router.delete("/{project_id}/permissions/{collab_user_id}")
async def remove_collaborator(
    project_id: str,
    collab_user_id: str,
    user: dict = Depends(get_current_user),
):
    """移除协作者，owner 专属。"""
    return await service.remove_collaborator(user["id"], project_id, collab_user_id)


@router.post("/{project_id}/transfer")
async def transfer_project(
    project_id: str,
    req: TransferProjectRequest,
    user: dict = Depends(get_current_user),
):
    """转交项目 owner 给其他用户（原 owner 转后 viewer 或退出，由 keep_access 决定）。"""
    return await service.transfer_project(
        user["id"], project_id, req.username, req.keep_access
    )


def _cleanup(temp_root: Path) -> None:
    import shutil

    shutil.rmtree(temp_root, ignore_errors=True)

"""Git 版本管理 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core import access_control
from backend.core.logger import get_logger
from backend.modules.git_manage import service
from backend.modules.git_manage.models import GitCommitRequest, GitRestoreRequest
from backend.utils.sandbox import to_http_exception

router = APIRouter(prefix="/api/git-manage", tags=["git-manage"])
logger = get_logger(__name__)


def _map_runtime_error(e: RuntimeError) -> HTTPException:
    """RuntimeError → HTTP：沙箱基础设施错误（503/429）优先，其余 400。"""
    sandbox_http = to_http_exception(e)
    if sandbox_http is not None:
        return sandbox_http
    return HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def get_status(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取版本控制状态（初始化标记 / 变更列表 / .git 用量 / 最近提交）。"""
    try:
        return (await service.get_status(workspace_id)).model_dump()
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.post("/init")
async def init_repository(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """初始化版本控制（git init + .gitignore + 初始提交）；已初始化 409、预估超上限 413。"""
    try:
        return (await service.init_repository(workspace_id)).model_dump()
    except HTTPException:
        raise
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.post("/commit")
async def commit_all(
    request: GitCommitRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """一键全部提交（add -A + commit）；无变更 400、message 空 422、超上限 413。"""
    message = request.message.strip()
    if not message:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "git_message_required",
                "message": "Commit message must not be empty",
            },
        )
    try:
        return (await service.commit_all(workspace_id, message)).model_dump()
    except HTTPException:
        raise
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.get("/log")
async def get_log(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: dict = Depends(access_control.require_workspace_read),
):
    """提交历史（limit/offset 分页）。"""
    try:
        entries = await service.get_log(workspace_id, limit=limit, offset=offset)
        return {"entries": [e.model_dump() for e in entries]}
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.get("/file-diff")
async def get_file_diff(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    path: str = Query(..., description="项目内相对路径"),
    commit: str = Query("", description="hex 提交哈希（缺省=工作区 vs HEAD）"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """单文件源码级 diff（结构化 hunks；commit 模式只读，worktree 模式可恢复）。"""
    try:
        return (
            await service.get_file_diff(workspace_id, path=path, commit=commit)
        ).model_dump()
    except HTTPException:
        raise
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.get("/commit-files")
async def get_commit_files(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    commit: str = Query(..., description="hex 提交哈希"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """单个提交变更的文件清单（抽屉内两级导航第一级）。"""
    try:
        return (
            await service.get_commit_files(workspace_id, commit=commit)
        ).model_dump()
    except HTTPException:
        raise
    except RuntimeError as e:
        raise _map_runtime_error(e) from e


@router.post("/restore")
async def restore_changes(
    request: GitRestoreRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """恢复工作区变更（mode=file 整文件 / mode=hunks 按勾选 hunk）；过期切片 409。"""
    try:
        return (
            await service.restore_changes(workspace_id, request=request)
        ).model_dump()
    except HTTPException:
        raise
    except RuntimeError as e:
        raise _map_runtime_error(e) from e

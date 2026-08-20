"""
项目管理 API 路由。

提供项目打开、文件树查询、段落提取、原地修改、撤销/重做等接口。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core import access_control
from backend.core.config import get_config
from backend.core.multiuser.dependencies import (
    get_current_user_optional,
    require_admin_if_multiuser,
)
from backend.core.project_manager import FileConflictError, ProjectInfo
from backend.core.workspace_manager import (
    get_project_manager_for_workspace,
    get_workspace_manager,
)
from backend.utils.path_guard import resolve_path_case_insensitive

router = APIRouter(prefix="/api/project", tags=["project"])


class OpenProjectRequest(BaseModel):
    # 单用户模式：本地路径（保持既有行为）
    path: str | None = None
    # 多用户模式：仅接受 project_id（拒绝 path）
    project_id: str | None = None


class ApplyChangeRequest(BaseModel):
    file_path: str
    paragraph_index: int
    new_text: str
    expected_md5: str | None = None


class FileActionRequest(BaseModel):
    file_path: str


@router.post("/open")
async def open_project(
    request: OpenProjectRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    user: dict | None = Depends(get_current_user_optional),
):
    """
    打开一个 LaTeX 项目。

    单用户模式：接收本地目录 path（保持既有行为）。
    多用户模式：任意路径打开被禁用，仅接受 project_id；
    系统根据当前用户实时校验项目访问权限，并将工作区绑定到该项目。
    """
    if get_config().multi_user.enabled:
        return await _open_project_multiuser(request, workspace_id, user)

    # ── 单用户模式：保持现状 ────────────────────────────────────────
    if not request.path:
        raise HTTPException(status_code=400, detail="path is required")
    project = get_project_manager_for_workspace(workspace_id)
    try:
        real_path = resolve_path_case_insensitive(request.path)
        if real_path is None:
            raise FileNotFoundError(f"Project path not found: {request.path}")
        info = project.open(str(real_path))
        return _open_response(info)
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _open_project_multiuser(
    request: OpenProjectRequest, workspace_id: str, user: dict | None
) -> dict:
    """多用户模式打开：project_id 派生用户项目根目录 + workspace 绑定。"""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Not logged in"},
        )
    if request.path:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "path_disabled",
                "message": "Arbitrary path open is disabled in multi-user mode; "
                "use project_id instead",
            },
        )
    if not request.project_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "message": "project_id is required in multi-user mode",
            },
        )

    access = await access_control.require_project_access_async(
        user["id"], request.project_id
    )
    project = get_project_manager_for_workspace(workspace_id)
    try:
        # 递归目录扫描 + 主文档识别 + 依赖图解析为 CPU/IO 密集同步操作，
        # 必须 to_thread 调度，避免大项目打开时阻塞事件循环
        info = await asyncio.to_thread(project.open, access["root_path"])
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 绑定 workspace → (user_id, project_id, root_path)，后续请求实时查库校验权限
    get_workspace_manager().bind(
        workspace_id,
        user_id=user["id"],
        project_id=request.project_id,
        owner_id=access["owner_id"],
        root_path=access["root_path"],
    )
    return _open_response(info)


def _open_response(info: ProjectInfo) -> dict:
    return {
        "root_path": info.root_path,
        "main_file": info.main_file,
        "file_tree": _serialize_file_tree(info.file_tree),
        "dependency_graph": info.dependency_graph,
    }


@router.get("/browse")
async def browse_directory(
    path: str = Query(default="", description="目录路径，默认为磁盘根目录")
):
    """
    浏览指定路径下的子目录列表。
    供前端文件夹选择器使用，返回子目录名称和完整路径。

    多用户模式下任意磁盘浏览被禁用，直接返回 403。
    """
    if get_config().multi_user.enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "no_permission",
                "message": "Directory browsing is disabled in multi-user mode",
            },
        )
    try:
        if path:
            target = resolve_path_case_insensitive(path)
            if target is None:
                raise HTTPException(
                    status_code=400, detail=f"Path does not exist: {path}"
                )
        else:
            target = Path.cwd()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
        entries = []
        for entry in sorted(target.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry.resolve()),
                    }
                )
        return {
            "current": str(target.resolve()),
            "parent": str(target.parent.resolve()) if target.parent != target else None,
            "directories": entries,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file-tree")
async def get_file_tree(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取当前打开项目的文件树。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        tree = project.get_file_tree()
        return {"file_tree": _serialize_file_tree(tree)}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/set-main-file")
async def set_main_file(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """手动设置项目的主 LaTeX 文件。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        project.set_main_file(request.file_path)
        return {"status": "ok", "main_file": request.file_path}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/paragraphs")
async def get_paragraphs(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取指定 .tex 文件的段落列表。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        paragraphs = project.get_paragraphs(file_path)
        # 同时返回文件 MD5，供前端乐观锁使用
        file_md5 = project.get_file_md5(file_path)
        return {
            "paragraphs": [
                {
                    "index": p.index,
                    "text": p.text,
                    "file_path": p.file_path,
                    "line_start": p.line_start,
                    "line_end": p.line_end,
                }
                for p in paragraphs
            ],
            "file_path": file_path,
            "total": len(paragraphs),
            "file_md5": file_md5,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/file-md5")
async def get_file_md5(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """获取指定文件的当前 MD5 哈希值（用于乐观锁版本校验）。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        md5 = project.get_file_md5(file_path)
        return {"file_path": file_path, "md5": md5}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/apply-change")
async def apply_change(
    request: ApplyChangeRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_write),
):
    """原地修改指定段落的文本。"""
    project = get_project_manager_for_workspace(workspace_id)
    try:
        project.apply_change(
            request.file_path,
            request.paragraph_index,
            request.new_text,
            expected_md5=request.expected_md5,
        )
        return {"status": "ok"}
    except FileConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_conflict",
                "message": str(e),
                "file_path": e.file_path,
                "expected_md5": e.expected_md5,
                "current_md5": e.current_md5,
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/undo")
async def undo_change(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_write),
):
    """撤销对指定文件最近一次的修改（共享项目多用户模式禁用）。"""
    await access_control.reject_undo_redo_if_shared(binding)
    project = get_project_manager_for_workspace(workspace_id)
    try:
        result = project.undo(request.file_path)
        return {"status": "ok", "undone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/redo")
async def redo_change(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_write),
):
    """重做对指定文件最近一次撤销的操作（共享项目多用户模式禁用）。"""
    await access_control.reject_undo_redo_if_shared(binding)
    project = get_project_manager_for_workspace(workspace_id)
    try:
        result = project.redo(request.file_path)
        return {"status": "ok", "redone": result}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/can-undo")
async def can_undo(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_read),
):
    """检查指定文件是否有可撤销的操作（共享项目多用户模式恒为 False）。"""
    project = get_project_manager_for_workspace(workspace_id)
    if (
        binding.get("multi_user")
        and binding.get("project_id")
        and await _is_shared(binding)
    ):
        return {"can_undo": False}
    return {"can_undo": project.can_undo(file_path)}


@router.get("/can-redo")
async def can_redo(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_read),
):
    """检查指定文件是否有可重做的操作（共享项目多用户模式恒为 False）。"""
    project = get_project_manager_for_workspace(workspace_id)
    if binding.get("multi_user") and binding.get("project_id"):
        if await _is_shared(binding):
            return {"can_redo": False}
    return {"can_redo": project.can_redo(file_path)}


@router.get("/undo-status")
async def undo_status(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_read),
):
    """一次返回指定文件的可撤销/可重做状态，供前端轮询减半请求。"""
    project = get_project_manager_for_workspace(workspace_id)
    if (
        binding.get("multi_user")
        and binding.get("project_id")
        and await _is_shared(binding)
    ):
        return {"can_undo": False, "can_redo": False}
    return {
        "can_undo": project.can_undo(file_path),
        "can_redo": project.can_redo(file_path),
    }


async def _is_shared(binding: dict) -> bool:
    """按绑定 project_id 判断项目是否共享（仅多用户模式调用）。"""
    if not (binding.get("multi_user") and binding.get("project_id")):
        return False
    return await access_control.async_is_shared_project(binding["project_id"])


@router.delete("/workspace/{workspace_id}")
async def close_workspace(
    workspace_id: str,
    _: None = Depends(require_admin_if_multiuser),
):
    """关闭指定工作区，释放相关资源。

    多用户模式下仅管理员可见（调试用途）；单用户模式保持原行为。
    """
    wm = get_workspace_manager()
    removed = wm.remove(workspace_id)
    return {"status": "ok", "removed": removed}


@router.get("/workspaces")
async def list_workspaces(
    _: None = Depends(require_admin_if_multiuser),
):
    """列出所有活跃的工作区。

    多用户模式下仅管理员可见（调试用途）；单用户模式保持原行为。
    """
    wm = get_workspace_manager()
    return {"workspaces": wm.list_workspaces()}


def _serialize_file_tree(nodes):
    """将 FileNode 列表序列化为 JSON 兼容格式。"""
    result = []
    for node in nodes:
        item = {
            "name": node.name,
            "path": node.path,
            "is_dir": node.is_dir,
        }
        if node.children:
            item["children"] = _serialize_file_tree(node.children)
        result.append(item)
    return result

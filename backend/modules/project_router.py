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
from backend.core.logger import get_logger
from backend.core.multiuser.dependencies import (
    get_current_user_optional,
    require_admin_if_multiuser,
)
from backend.core.project_manager import FileConflictError, ProjectInfo
from backend.core.workspace_manager import (
    get_project_manager_for_workspace,
    get_workspace_manager,
)
from backend.modules.editor import service as editor_service
from backend.modules.projects import service as projects_service
from backend.utils.path_guard import resolve_path_case_insensitive

router = APIRouter(prefix="/api/project", tags=["project"])
logger = get_logger(__name__)


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


class SaveFileRequest(BaseModel):
    file_path: str
    content: str
    # 可缺省：权限依赖优先读 body，缺省时已回退读 query（binding 里携带），
    # 端点再以 binding.workspace_id 兜底，兼容两种携带方式
    workspace_id: str | None = None
    base_md5: str | None = None


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
        # 错误消息不得携带服务器上的项目绝对路径（多用户路径约束）
        logger.warning(
            f"Multi-user project open failed: project={request.project_id} "
            f"user={user['id']}: {e}"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "project_unavailable",
                "message": "Project directory is missing or invalid",
            },
        ) from e

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
    # 多用户模式：服务器上的项目绝对路径不向用户暴露（root_path 置空，
    # 前端按需展示主文档等相对信息）；单用户模式保持本地真实路径
    root_path = None if get_config().multi_user.enabled else info.root_path
    return {
        "root_path": root_path,
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


@router.get("/latex-symbols")
async def get_latex_symbols(
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    r"""全项目只读符号扫描（\ref/\label/\cite 补全数据源，阶段四）。

    零 LLM、零写盘：一次返回全项目 \label（含行号）与 cite key（.bib
    条目优先，.tex 已引用 key 补尾）；响应只含相对路径（多用户脱敏）。
    """
    project = get_project_manager_for_workspace(workspace_id)
    return await editor_service.scan_project_symbols(project)


@router.get("/file-content")
async def get_file_content(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """编辑器打开文件：返回文件全文与当前 MD5（editor-plan §5.1）。"""
    project = get_project_manager_for_workspace(workspace_id)
    return await editor_service.read_file_content(project, file_path)


@router.get("/raw-file")
async def get_raw_file(
    file_path: str = Query(..., description="文件路径（相对于项目根目录）"),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    _: dict = Depends(access_control.require_workspace_read),
):
    """编辑器二进制预览（PDF 与常见位图）：inline 返回原始字节。

    供编辑器页文件树点击 PDF / 图片时内嵌只读预览；不进入缓冲区与
    撤销历史。
    """
    project = get_project_manager_for_workspace(workspace_id)
    return await editor_service.read_raw_file(project, file_path)


@router.post("/save-file")
async def save_file(
    request: SaveFileRequest,
    binding: dict = Depends(access_control.require_workspace_write_json),
    user: dict | None = Depends(get_current_user_optional),
):
    """编辑器整文件保存：MD5 乐观锁 + 统一撤销记账 + 用量记账刷新。

    单用户模式推入 BackupManager 内存撤销栈；多用户模式记入反向补丁
    编辑日志并与 AI 改动共享同一套 undo/redo 历史（editor-plan §5.1，
    决策 Q11）。
    """
    project = get_project_manager_for_workspace(
        request.workspace_id or binding["workspace_id"]
    )
    return await editor_service.save_file_content(
        project,
        binding,
        file_path=request.file_path,
        content=request.content,
        base_md5=request.base_md5,
        actor_user_id=(
            _binding_actor(binding, user) if _is_multiuser_binding(binding) else None
        ),
    )


@router.post("/apply-change")
async def apply_change(
    request: ApplyChangeRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_write),
    user: dict | None = Depends(get_current_user_optional),
):
    """原地修改指定段落的文本。

    多用户模式下写回后刷新项目配额记账；若该写操作导致项目空间超限，
    则以写前内容直接恢复（不经撤销通道、不产生编辑日志 op，方案 §8.4）
    并返回 413 quota_exceeded（不允许部分写入）。
    """
    project = get_project_manager_for_workspace(workspace_id)
    # 多用户模式：写前捕获全文，供配额超限时直接恢复（单用户走内存撤销栈）
    before_content: str | None = None
    actor_id: str | None = None
    if _is_multiuser_binding(binding):
        before_content = await asyncio.to_thread(
            project.read_project_file, request.file_path
        )
        actor_id = _binding_actor(binding, user)
    try:
        op_id = project.apply_change(
            request.file_path,
            request.paragraph_index,
            request.new_text,
            expected_md5=request.expected_md5,
        )
        await projects_service.enforce_write_quota(
            binding,
            project,
            request.file_path,
            before_content=before_content,
            edit_op_id=op_id,
            actor_user_id=actor_id,
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


def _binding_actor(binding: dict, user: dict | None) -> str:
    """多用户模式下解析当前操作者 id；缺失即未认证（防御，正常由中间件注入）。"""
    if user is not None and user.get("id"):
        return user["id"]
    from backend.core.multiuser import userspace

    actor = userspace.get_current_user_id()
    if not actor:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Not logged in"},
        )
    return actor


def _is_multiuser_binding(binding: dict) -> bool:
    return bool(binding.get("multi_user") and binding.get("project_id"))


@router.post("/undo")
async def undo_change(
    request: FileActionRequest,
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_write),
    user: dict | None = Depends(get_current_user_optional),
):
    """撤销最近一次修改。

    单用户模式：按工作区 BackupManager 内存栈回退指定文件（行为不变）。
    多用户模式（v2 P3 反向补丁）：只回退当前用户在该项目的最新一组修改
    （LIFO，共享项目已解禁）；组内任一文件当前 MD5 != after_md5 时
    409 file_conflict（绝不静默覆盖他人修改）；恢复超配额 413
    quota_exceeded_recover（op 保持可重试）。
    """
    project = get_project_manager_for_workspace(workspace_id)
    if _is_multiuser_binding(binding):
        from backend.core.multiuser import edit_log

        try:
            result = await asyncio.to_thread(
                edit_log.perform_undo,
                project,
                project_id=binding["project_id"],
                actor_user_id=_binding_actor(binding, user),
                root_path=binding["root_path"],
            )
        except FileConflictError as e:
            # TOCTOU：MD5 校验通过后、事务落盘前被他人写入——按冲突语义
            # 上报（而非 500），绝不静默覆盖
            raise HTTPException(
                status_code=409,
                detail={"error": "file_conflict", "message": str(e)},
            ) from e
        return {
            "status": "ok",
            "undone": result["undone"],
            **({"files": result["files"]} if result.get("undone") else {}),
        }
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
    user: dict | None = Depends(get_current_user_optional),
):
    """重做最近一次撤销的操作。

    单用户模式：BackupManager 内存栈（行为不变）。
    多用户模式（v2 P3）：对称重放当前用户最新一组被撤销的修改；
    校验当前 MD5 == before_md5，冲突 409；有更新的修改时视为已被取代。
    """
    project = get_project_manager_for_workspace(workspace_id)
    if _is_multiuser_binding(binding):
        from backend.core.multiuser import edit_log

        try:
            result = await asyncio.to_thread(
                edit_log.perform_redo,
                project,
                project_id=binding["project_id"],
                actor_user_id=_binding_actor(binding, user),
                root_path=binding["root_path"],
            )
        except FileConflictError as e:
            # 同 undo：校验与落盘之间的窗口被他人写入 → 409 冲突语义
            raise HTTPException(
                status_code=409,
                detail={"error": "file_conflict", "message": str(e)},
            ) from e
        return {
            "status": "ok",
            "redone": result["redone"],
            **({"files": result["files"]} if result.get("redone") else {}),
        }
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
    user: dict | None = Depends(get_current_user_optional),
):
    """检查是否有可撤销的操作。

    多用户模式（v2 P3）：按当前用户的反向补丁栈实时计算（MD5 校验失败
    视为不可撤销但保留 op）；file_path 参数仅为兼容保留，不再参与判定。
    """
    if _is_multiuser_binding(binding):
        from backend.core.multiuser import edit_log

        status = await asyncio.to_thread(
            edit_log.compute_status,
            binding["project_id"],
            _binding_actor(binding, user),
            binding["root_path"],
        )
        return {"can_undo": status["can_undo"]}
    project = get_project_manager_for_workspace(workspace_id)
    return {"can_undo": project.can_undo(file_path)}


@router.get("/can-redo")
async def can_redo(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_read),
    user: dict | None = Depends(get_current_user_optional),
):
    """检查是否有可重做的操作（多用户模式按反向补丁栈实时计算）。"""
    if _is_multiuser_binding(binding):
        from backend.core.multiuser import edit_log

        status = await asyncio.to_thread(
            edit_log.compute_status,
            binding["project_id"],
            _binding_actor(binding, user),
            binding["root_path"],
        )
        return {"can_redo": status["can_redo"]}
    project = get_project_manager_for_workspace(workspace_id)
    return {"can_redo": project.can_redo(file_path)}


@router.get("/undo-status")
async def undo_status(
    file_path: str = Query(...),
    workspace_id: str = Query(..., description="工作区唯一标识符"),
    binding: dict = Depends(access_control.require_workspace_read),
    user: dict | None = Depends(get_current_user_optional),
):
    """一次返回可撤销/可重做状态，供前端轮询减半请求。

    多用户模式（v2 P3）：基于当前用户的反向补丁栈实时计算，附带阻塞
    原因（conflict / too_large / superseded，诊断用）。
    """
    if _is_multiuser_binding(binding):
        from backend.core.multiuser import edit_log

        status = await asyncio.to_thread(
            edit_log.compute_status,
            binding["project_id"],
            _binding_actor(binding, user),
            binding["root_path"],
        )
        return {
            "can_undo": status["can_undo"],
            "can_redo": status["can_redo"],
            "undo_block_reason": status["undo_block_reason"],
            "redo_block_reason": status["redo_block_reason"],
            "failed_ops": status["failed_ops"],
        }
    project = get_project_manager_for_workspace(workspace_id)
    return {
        "can_undo": project.can_undo(file_path),
        "can_redo": project.can_redo(file_path),
    }


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

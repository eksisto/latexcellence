"""
统一访问控制模块（多用户模式 P2）。

职责：
    1. 项目访问判定：owner / editor / viewer 三档权限矩阵
    2. 提供 require_project_access() / get_projects_for_user() / get_user_role_for_project()
    3. 提供基于 workspace 绑定的请求级访问依赖 require_workspace()
    4. 所有权限实时查库（不缓存权限结果，权限变更立即生效）
    5. 单用户模式：全部校验放行（保持既有行为）；多用户模式强制校验

约定：
    - 本模块为同步核心 + FastAPI 请求依赖的薄封装；FastAPI 路由层经
      asyncio.to_thread 调度压库操作，不阻塞事件循环
    - 不缓存任何权限结果；不在此处拼接文件路径（路径拼接统一走 path_guard）
"""

import asyncio

from fastapi import Depends, HTTPException, Request

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import userspace
from backend.core.multiuser.db import db_count, db_query_all, db_query_one
from backend.core.multiuser.dependencies import (
    forbidden,
    get_current_user_optional,
    unauthorized,
)
from backend.core.multiuser.quota import refresh_owner_usage_after_write
from backend.core.workspace_manager import get_workspace_manager

logger = get_logger("access_control")


# ── 权限查询（同步、实时查库） ─────────────────────────────────────────────
def get_project_row(project_id: str) -> dict | None:
    """按 id 查询项目行（含已删除项目，供恢复等操作判断）。"""
    return db_query_one("SELECT * FROM projects WHERE id = ?", (project_id,))


def get_user_role_for_project(user_id: str, project_id: str) -> str | None:
    """
    实时查询用户对指定项目的角色。

    返回 "owner" / "editor" / "viewer"；项目不存在、已删除或不是协作者时返回 None。
    找不到项目或项目非 active 视为无权限（不向调用方泄露项目是否存在）。
    """
    row = get_project_row(project_id)
    if row is None or row["status"] != "active":
        return None
    if row["owner_id"] == user_id:
        return "owner"
    coll = db_query_one(
        "SELECT role FROM project_collaborators WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    )
    return coll["role"] if coll else None


def get_project_access(user_id: str, project_id: str) -> dict | None:
    """
    返回用户对项目的访问信息；无权限时返回 None。
    dict 包含 project_id / owner_id / role / root_path / is_shared。
    """
    role = get_user_role_for_project(user_id, project_id)
    if role is None:
        return None
    row = get_project_row(project_id)
    if row is None:
        return None
    return {
        "project_id": project_id,
        "owner_id": row["owner_id"],
        "name": row["name"],
        "role": role,
        "status": row["status"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "root_path": str(userspace.project_dir(row["owner_id"], project_id)),
        "is_shared": is_shared_project(project_id),
    }


def is_shared_project(project_id: str) -> bool:
    """项目是否存在任意协作者（共享项目标识；v2 P3 起共享项目可撤销自己的修改）。"""
    return (
        db_count(
            "SELECT COUNT(*) FROM project_collaborators WHERE project_id = ?",
            (project_id,),
        )
        > 0
    )


async def async_is_shared_project(project_id: str) -> bool:
    """is_shared_project 的 asyncio.to_thread 版本。"""
    return await asyncio.to_thread(is_shared_project, project_id)


def require_project_access(
    user_id: str, project_id: str, *, write: bool = False
) -> dict | None:
    """
    多用户模式项目访问判定（同步版）。

    参数：
        user_id: 当前用户 ID
        project_id: 项目 ID
        write: 是否为写操作（False=只读；True=要求 editor/owner 权限）

    返回：
        项目访问信息 dict（含 root_path 等，供上层直接使用）

    异常：
        HTTPException 404 not_found（项目不存在/已删除）
        HTTPException 403 no_permission（无权限 / viewer 执行写操作）
    单用户模式：直接返回 None 表示放行（由调用方判断）。
    拒绝时记录审计日志（user/project/reason），授权失败可追溯。
    """
    if not get_config().multi_user.enabled:
        return None
    row = get_project_row(project_id)
    if row is None or row["status"] != "active":
        logger.warning(
            "DENY project access: user=%s project=%s reason=not_found write=%s",
            user_id,
            project_id,
            write,
        )
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Project not found"},
        )
    role = get_user_role_for_project(user_id, project_id)
    if role is None:
        logger.warning(
            "DENY project access: user=%s project=%s reason=no_role write=%s",
            user_id,
            project_id,
            write,
        )
        raise forbidden("no_permission", "No access to this project")
    if write and role == "viewer":
        logger.warning(
            "DENY project access: user=%s project=%s role=viewer "
            "reason=write_forbidden",
            user_id,
            project_id,
        )
        raise forbidden("no_permission", "Viewer cannot modify project files")
    return get_project_access(user_id, project_id)


async def require_project_access_async(
    user_id: str, project_id: str, *, write: bool = False
) -> dict | None:
    """require_project_access 的 asyncio.to_thread 版本（用于 FastAPI 路由）。"""
    return await asyncio.to_thread(
        require_project_access, user_id, project_id, write=write
    )


def get_projects_for_user(user_id: str) -> list[dict]:
    """返回用户可见的活动项目（拥有 + 被共享），按名称排序。"""
    owned = db_query_all(
        "SELECT * FROM projects WHERE owner_id = ? AND status = 'active' "
        "ORDER BY created_at, id",
        (user_id,),
    )
    shared = db_query_all(
        "SELECT p.id, p.owner_id, p.name, p.size_bytes, p.status, p.deleted_at, "
        "p.created_at, p.updated_at, pc.role AS role "
        "FROM projects p JOIN project_collaborators pc ON pc.project_id = p.id "
        "WHERE pc.user_id = ? AND p.status = 'active' ORDER BY p.created_at, p.id",
        (user_id,),
    )
    result = []
    for row in owned:
        result.append(_build_project_view(row, "owner"))
    for row in shared:
        result.append(_build_project_view(row, row["role"]))
    return sorted(result, key=lambda p: (p["name"].lower(), p["name"]))


async def get_projects_for_user_async(user_id: str) -> list[dict]:
    """get_projects_for_user 的 asyncio.to_thread 版本。"""
    return await asyncio.to_thread(get_projects_for_user, user_id)


def _build_project_view(row: dict, role: str) -> dict:
    owner = db_query_one(
        "SELECT username, email FROM users WHERE id = ?", (row["owner_id"],)
    )
    return {
        "project_id": row["id"],
        "name": row["name"],
        "role": role,
        "owner_id": row["owner_id"],
        "owner_username": owner["username"] if owner else "",
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_shared": role != "owner" or is_shared_project(row["id"]),
    }


def is_multiuser() -> bool:
    return bool(get_config().multi_user.enabled)


# ── 基于 workspace 绑定的请求级访问依赖 ────────────────────────────────────
def _enforce_workspace_access(
    workspace_id: str | None,
    user: dict | None,
    write: bool,
    *,
    required: bool,
) -> dict:
    """
    workspace 访问判定核心（单用户放行；多用户实时校验）。

    required=True 时：未登录 → 401；缺 workspace_id 或未绑定 → 400 invalid_workspace；
    无项目权限 → 403 no_permission；write=True 且 viewer → 403。
    required=False 时：多用户模式下缺 workspace_id 直接放行（供可选的
    workspace 参数端点使用，如 session-only 的写作助手），其余判定同 required=True。
    """
    if not is_multiuser():
        return {
            "workspace_id": workspace_id or "",
            "project_id": None,
            "owner_id": None,
            "role": None,
            "root_path": None,
            "multi_user": False,
        }
    if user is None:
        logger.warning(
            "DENY workspace access: workspace=%s reason=unauthenticated",
            workspace_id,
        )
        raise unauthorized("Not logged in")
    if not workspace_id:
        if not required:
            return {
                "workspace_id": "",
                "project_id": None,
                "owner_id": None,
                "role": None,
                "root_path": None,
                "multi_user": True,
            }
        logger.warning(
            "DENY workspace access: user=%s workspace=<empty> reason=missing",
            user["id"],
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_workspace",
                "message": "Workspace is not open or not bound to a project",
            },
        )
    binding = get_workspace_manager().get_binding(workspace_id)
    if binding is None:
        logger.warning(
            "DENY workspace access: user=%s workspace=%s reason=not_bound",
            user["id"],
            workspace_id,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_workspace",
                "message": "Workspace is not open or not bound to a project",
            },
        )
    project_id = binding.get("project_id")
    role = get_user_role_for_project(user["id"], project_id) if project_id else None
    if role is None:
        logger.warning(
            "DENY workspace access: user=%s workspace=%s project=%s "
            "reason=no_role write=%s",
            user["id"],
            workspace_id,
            project_id,
            write,
        )
        raise forbidden("no_permission", "No access to this project")
    if write and role == "viewer":
        logger.warning(
            "DENY workspace access: user=%s workspace=%s project=%s "
            "role=viewer reason=write_forbidden",
            user["id"],
            workspace_id,
            project_id,
        )
        raise forbidden("no_permission", "Viewer cannot modify project files")
    return {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "owner_id": binding.get("owner_id"),
        "role": role,
        "root_path": binding.get("root_path"),
        "multi_user": True,
    }


def require_workspace(write: bool = False, *, required: bool = True):
    """
    生成一个 FastAPI 依赖，按当前登录用户实时校验其对该工作区所绑定项目的访问。

    workspace_id 取自查询参数或路径参数（query 优先，其次 path）。
    单用户模式：直接放行（返回空绑定，保持既有行为不变）。
    多用户模式：
        - 未登录 → 401 unauthorized
        - 工作区未绑定项目（required=True）→ 400 invalid_workspace
        - 无项目权限 → 403 no_permission
        - write=True 且角色为 viewer → 403 no_permission

    返回绑定 dict：{workspace_id, project_id, owner_id, role, root_path, multi_user}
    """

    async def _dependency(
        request: Request,
        user: dict | None = Depends(get_current_user_optional),
    ) -> dict:
        # 从 query 或 path 提取 workspace_id（不在依赖中声明 Query 参数：
        # workspace_id 同时作为路径参数的端点（如 /stream/{workspace_id}）
        # 会与 Query 声明冲突，且 Path 参数无法用 Query 表达）
        ws = request.query_params.get("workspace_id")
        if not ws:
            ws = request.path_params.get("workspace_id")
        # 权限判定含 SQLite 查库（WAL busy_timeout 最坏 5s），整体入线程池
        # 执行，避免阻塞事件循环
        return await asyncio.to_thread(
            _enforce_workspace_access, ws, user, write, required=required
        )

    return _dependency


def require_workspace_json(write: bool = False, *, required: bool = True):
    """
    与 require_workspace 相同，但 workspace_id 取自 JSON 请求体字段
    （用于 style_modify 等把 workspace_id 放在 body 的路由）。"""

    async def _dependency(
        request: Request,
        user: dict | None = Depends(get_current_user_optional),
    ) -> dict:
        data = None
        try:
            data = await request.json()
        except Exception:
            data = None
        ws = None
        if isinstance(data, dict):
            ws = data.get("workspace_id")
        # 同 require_workspace：压库判定入线程池
        return await asyncio.to_thread(
            _enforce_workspace_access, ws, user, write, required=required
        )

    return _dependency


require_workspace_read = require_workspace(write=False)
require_workspace_write = require_workspace(write=True)
require_workspace_read_json = require_workspace_json(write=False)
require_workspace_write_json = require_workspace_json(write=True)


# ── 写盘后配额记账刷新 ────────────────────────────────────────────────────
def get_binding_project_id(workspace_id: str) -> str | None:
    """返回工作区绑定的多用户项目 id；单用户模式 / 未绑定返回 None。

    注意：workspace_manager 存储的绑定只含 user_id/project_id/owner_id/
    root_path（"multi_user" 是 require_workspace 依赖返回值的增强字段），
    因此这里以实时配置判定模式，而非读取绑定字典。"""
    if not is_multiuser():
        return None
    binding = get_workspace_manager().get_binding(workspace_id)
    if not binding:
        return None
    return binding.get("project_id") or None


async def refresh_workspace_project_usage(workspace_id: str) -> None:
    """多用户模式写盘后按工作区绑定刷新项目用量记账（size_bytes）。

    供各异步写盘服务（写作助手编辑、style_modify 提案应用等）在成功写回后
    调用，保证多用户项目用量缓存与磁盘一致（方案 §9.1）；单用户模式或
    未绑定项目时静默跳过。压库操作经 asyncio.to_thread 调度。
    """
    project_id = get_binding_project_id(workspace_id)
    if project_id:
        await asyncio.to_thread(refresh_owner_usage_after_write, project_id)


def refresh_workspace_project_usage_sync(workspace_id: str) -> None:
    """refresh_workspace_project_usage 的同步版。

    供同步写路径直接调用（base_paragraph_check / code_review 的
    apply_changes 为同步方法，路由层本就以同步方式执行）；使用同步查库
    避免为一条索引 UPDATE 再起线程。单用户模式或未绑定项目时静默跳过。
    """
    project_id = get_binding_project_id(workspace_id)
    if project_id:
        refresh_owner_usage_after_write(project_id)

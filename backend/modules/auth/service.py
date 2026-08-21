"""
多用户认证服务层（/api/auth 与 /api/admin 的业务逻辑）。

- 本层调用 backend.core.multiuser 的同步核心函数（auth_core），
  并通过 asyncio.to_thread 调度 SQLite 压库操作，避免阻塞事件循环
- 业务失败统一抛 HTTPException（结构化 detail：{"error": code, "message": ...}），
  router 层只做参数绑定与 HTTP 细节（Cookie 等）
- 方法内实时调用 get_config()，不缓存配置
"""

import asyncio
import secrets

from fastapi import HTTPException

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser.dependencies import (
    forbidden,
)


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _not_found() -> HTTPException:
    return _http(404, "not_found", "Not found")


# ── 注册 / 登录 / 登出 ──────────────────────────────────────────
async def register(username: str, password: str, email: str = "") -> dict:
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _not_found()
    if not cfg.multi_user.registration_enabled:
        raise _http(400, "registration_disabled", "Registration is disabled")
    # 先做参数级校验（错误分类清晰），再交给核心创建（剩余失败即重名）
    uerr = auth_core.validate_username(username)
    if uerr:
        raise _http(400, "invalid_username", uerr)
    perr = auth_core.validate_password(password)
    if perr:
        raise _http(400, "invalid_password", perr)
    try:
        user = await asyncio.to_thread(
            auth_core.create_user,
            username,
            password,
            email,
            role="user",
            status="pending",
        )
    except ValueError as e:
        # create_user 已通过前置校验，唯一剩余 ValueError 为用户名重复
        raise _http(409, "username_taken", str(e))
    return {
        "user": user,
        "message": "Registration submitted; waiting for admin approval",
    }


async def login(username: str, password: str, remember: bool, ip: str) -> dict:
    """校验凭据并创建会话，返回 {"token": ..., "user": ...}。"""
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _not_found()
    if auth_core.login_rate_limited(username, ip):
        raise _http(429, "rate_limited", "Too many login attempts; please retry later")

    user = await asyncio.to_thread(auth_core.get_user_by_username, username)
    hashed = user["password_hash"] if user is not None else None
    # 恒定时长校验：用户不存在与密码错误耗时一致，避免时序侧信道枚举用户名
    ok = await asyncio.to_thread(auth_core.check_credentials, hashed, password)
    if not ok:
        # 独立错误码：凭据错误 ≠ 会话过期（unauthorized），前端据此展示
        # 「用户名或密码错误」且不触发全局登出回调
        raise _http(401, "invalid_credentials", "Invalid username or password")
    if user["status"] == "pending":
        raise forbidden("account_pending", "Account is pending admin approval")
    if user["status"] == "disabled":
        raise forbidden("account_disabled", "Account is disabled")
    if user["status"] != "active":
        raise _http(401, "invalid_credentials", "Invalid username or password")

    token = await asyncio.to_thread(auth_core.create_session, user["id"], remember)
    return {"token": token, "user": auth_core.to_public_user(user)}


async def logout(token: str) -> None:
    if token:
        await asyncio.to_thread(auth_core.delete_session, token)


async def build_me(user: dict) -> dict:
    """组装 /api/auth/me 响应（用户信息 + 配额与用量）。"""
    from backend.core.multiuser.quota import user_usage

    cfg = get_config().multi_user
    project_quota = (
        user["project_quota_bytes"]
        if user["project_quota_bytes"] is not None
        else cfg.default_project_quota_mb * 1024 * 1024
    )
    derived_quota = (
        user["derived_quota_bytes"]
        if user["derived_quota_bytes"] is not None
        else cfg.default_derived_quota_mb * 1024 * 1024
    )
    usage = await asyncio.to_thread(user_usage, user["id"])
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "status": user["status"],
        },
        "quota": {
            "project_quota_bytes": project_quota,
            "derived_quota_bytes": derived_quota,
        },
        "usage": usage,
    }


# ── 管理员：用户管理 ────────────────────────────────────────────
async def admin_list_users() -> list[dict]:
    return await asyncio.to_thread(auth_core.list_users)


async def admin_set_status(user_id: str, status: str, action: str) -> dict:
    if await asyncio.to_thread(auth_core.get_user_by_id, user_id) is None:
        raise _not_found()
    user = await asyncio.to_thread(auth_core.set_user_status, user_id, status)
    return {"action": action, "user": user}


async def admin_preview_reset_password(user_id: str) -> dict:
    """生成候选临时密码（仅预览，不修改用户密码）。"""
    if await asyncio.to_thread(auth_core.get_user_by_id, user_id) is None:
        raise _not_found()
    temp = secrets.token_urlsafe(12)  # 16+ 字符
    return {"temp_password": temp}


async def admin_confirm_reset_password(user_id: str, temp_password: str) -> dict:
    """确认重置：将密码写入为预览的临时密码，并吊销该用户全部会话。"""
    try:
        user = await asyncio.to_thread(auth_core.reset_password, user_id, temp_password)
    except ValueError as e:
        raise _http(400, "invalid_password", str(e))
    return {"user": user}


async def admin_set_quota(
    user_id: str, project_quota_bytes: int | None, derived_quota_bytes: int | None
) -> dict:
    if await asyncio.to_thread(auth_core.get_user_by_id, user_id) is None:
        raise _not_found()
    try:
        user = await asyncio.to_thread(
            auth_core.set_user_quota, user_id, project_quota_bytes, derived_quota_bytes
        )
    except ValueError as e:
        raise _http(400, "invalid_quota", str(e))
    return {"user": user}


async def admin_delete_user(user_id: str, acting_user: dict) -> dict:
    """删除用户：项目转交管理员、衍生数据归档、会话吊销（仅 admin）。"""
    if await asyncio.to_thread(auth_core.get_user_by_id, user_id) is None:
        raise _not_found()
    try:
        return await asyncio.to_thread(
            auth_core.delete_user, user_id, acting_user["id"]
        )
    except ValueError as e:
        raise _http(400, "invalid_user", str(e))

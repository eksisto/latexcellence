"""
FastAPI 认证依赖（多用户模式）。

提供 get_current_user / require_admin 依赖与统一的 401/403 错误构造。
所有压库操作通过 asyncio.to_thread 调度，不阻塞事件循环。
"""

import asyncio

from fastapi import Cookie, Depends, HTTPException

from backend.core.config import get_config
from . import auth as auth_core

SESSION_COOKIE_NAME = auth_core.SESSION_COOKIE_NAME


def unauthorized(message: str = "Not logged in") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": "unauthorized", "message": message},
    )


def forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"error": code, "message": message})


def require_multiuser_mode() -> None:
    """多用户模式未开启时对认证/管理接口返回 404（模式内才可用）。"""
    if not get_config().multi_user.enabled:
        raise HTTPException(status_code=404, detail="Not found")


async def get_current_user(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    """解析会话并返回当前用户（dict，不依赖具体请求对象，便于测试）。

    未登录 / 会话过期 / 账号被禁用均返回 401。
    """
    require_multiuser_mode()
    if not session:
        raise unauthorized("Not logged in")
    user = await asyncio.to_thread(auth_core.validate_session, session)
    if user is None:
        raise unauthorized("Session expired or invalid")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """仅 admin 角色可访问；否则 403 no_permission。"""
    if user["role"] != "admin":
        raise forbidden("no_permission", "Admin privileges required")
    return user


async def get_current_user_optional(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict | None:
    """会话解析的可选版本：单用户模式或未登录时返回 None，不抛错。

    供"单用户保持现状、多用户收紧"的双模式端点使用（如 /api/config、
    多用户模式下仅管理员可访问）。
    """
    if not get_config().multi_user.enabled:
        return None
    if not session:
        return None
    return await asyncio.to_thread(auth_core.validate_session, session)


async def require_admin_if_multiuser(
    user: dict | None = Depends(get_current_user_optional),
) -> None:
    """多用户模式下要求管理员；单用户模式直接放行（保持既有行为）。"""
    if not get_config().multi_user.enabled:
        return
    if user is None:
        raise unauthorized("Not logged in")
    if user["role"] != "admin":
        raise forbidden("no_permission", "Admin privileges required")

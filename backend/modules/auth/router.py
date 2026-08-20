"""
多用户认证路由（薄路由层）。

- /api/auth：注册 / 登录 / 登出 / 当前用户
- /api/admin：用户列表、审批、禁用/启用、重置密码、配额调整（仅 admin）

业务逻辑见 service.py；Cookie / 请求头等 HTTP 细节在此层处理。
"""

from fastapi import APIRouter, Cookie, Depends, Request, Response

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser.dependencies import get_current_user, require_admin
from backend.modules.auth import service
from backend.modules.auth.models import (
    LoginRequest,
    QuotaUpdateRequest,
    RegisterRequest,
)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

SESSION_COOKIE_NAME = auth_core.SESSION_COOKIE_NAME
CSRF_COOKIE_NAME = auth_core.CSRF_COOKIE_NAME


def _session_max_age_seconds(remember: bool) -> int:
    """Cookie 有效期（秒）：记住我 30 天，否则 7 天（与会话过期一致）。"""
    mu = get_config().multi_user
    days = mu.remember_ttl_days if remember else mu.session_ttl_days
    return days * 86400


def _cookie_secure(request: Request) -> bool:
    """HTTPS 请求下为 Cookie 设置 Secure 标志。

    反向代理终止 TLS 时 uvicorn 看到 http，此时依据标准的
    X-Forwarded-Proto 头判定（反代会设置/覆盖该头；客户端伪造该头
    只影响其自身会话 Cookie 的 Secure 标志，无跨用户风险）。"""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").strip().lower() == "https"


@auth_router.post("/register")
async def register(req: RegisterRequest):
    """开放注册（registration_enabled=true 时），写入 pending，等待审批。"""
    result = await service.register(req.username, req.password, req.email)
    return result


@auth_router.post("/login")
async def login(req: LoginRequest, response: Response, request: Request):
    """登录：校验凭据并设置 HttpOnly 会话 Cookie 与 CSRF Cookie。"""
    ip = request.client.host if request.client else ""
    result = await service.login(req.username, req.password, req.remember, ip)
    max_age = _session_max_age_seconds(req.remember)
    secure = _cookie_secure(request)

    # 会话 Cookie：HttpOnly + SameSite=Lax。仅此 Cookie 携带认证状态。
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=result["token"],
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=max_age,
    )
    # CSRF Cookie（双提交模式）：不设 HttpOnly，SPA 读取其值放入请求头；
    # 中间件校验 X-Latexcellence-CSRF 头与该 Cookie 值一致。
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=auth_core.new_token(),
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=max_age,
    )
    return {"user": result["user"]}


@auth_router.post("/logout")
async def logout(
    response: Response,
    user: dict = Depends(get_current_user),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    """登出：吊销服务端会话并清除会话/Cookie 与 CSRF Cookie。"""
    await service.logout(session)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return {"status": "ok"}


@auth_router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """当前用户信息、角色、配额与用量。"""
    return service.build_me(user)


# ── 管理员端点 ──────────────────────────────────────────────────
@admin_router.get("/users")
async def admin_users(_: dict = Depends(require_admin)):
    """用户列表（含待审批、配额覆盖）。"""
    return {"users": await service.admin_list_users()}


@admin_router.post("/users/{user_id}/approve")
async def admin_approve(user_id: str, _: dict = Depends(require_admin)):
    return await service.admin_set_status(user_id, "active", "approve")


@admin_router.post("/users/{user_id}/reject")
async def admin_reject(user_id: str, _: dict = Depends(require_admin)):
    """拒绝注册：账号转为 disabled（可随时重新启用）。"""
    return await service.admin_set_status(user_id, "disabled", "reject")


@admin_router.post("/users/{user_id}/disable")
async def admin_disable(user_id: str, _: dict = Depends(require_admin)):
    return await service.admin_set_status(user_id, "disabled", "disable")


@admin_router.post("/users/{user_id}/enable")
async def admin_enable(user_id: str, _: dict = Depends(require_admin)):
    return await service.admin_set_status(user_id, "active", "enable")


@admin_router.post("/users/{user_id}/reset-password")
async def admin_reset_password(user_id: str, _: dict = Depends(require_admin)):
    """重置为临时密码（一次性返回明文），并吊销该用户全部会话。"""
    return await service.admin_reset_password(user_id)


@admin_router.put("/users/{user_id}/quota")
async def admin_update_quota(
    user_id: str, req: QuotaUpdateRequest, _: dict = Depends(require_admin)
):
    """调整用户配额覆盖（不传/传 null 表示跟随全局默认）。"""
    return await service.admin_set_quota(
        user_id, req.project_quota_bytes, req.derived_quota_bytes
    )

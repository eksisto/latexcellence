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
from backend.core.logger import get_logger
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import mail, registration_policy
from backend.core.multiuser.dependencies import (
    forbidden,
)

logger = get_logger("multiuser.auth.service")


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _not_found() -> HTTPException:
    return _http(404, "not_found", "Not found")


# ── 注册 / 登录 / 登出 ──────────────────────────────────────────
# 注册限流窗口（小时级）：与 register_rate_limit_per_hour 配套
_REGISTER_WINDOW_SECONDS = 3600


async def register(username: str, password: str, email: str, ip: str = "") -> dict:
    """注册：写入用户并按 IP 策略决定自动通过或转人工审批。

    - 注册 IP 经可信代理解析（router 层），始终记录到 users.registered_ip；
    - 每 IP 滑动窗口限流（register_rate_limit_per_hour），防灌库；
    - 自动审批判定见 multiuser/registration_policy.py：无恶意即 active，
      否则 pending——只降级为人工，绝不直接拒绝。
    """
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _not_found()
    mu = cfg.multi_user
    if not mu.registration_enabled:
        raise _http(400, "registration_disabled", "Registration is disabled")
    # 先做参数级校验（错误分类清晰），再交给核心创建（剩余失败即重名）
    uerr = auth_core.validate_username(username)
    if uerr:
        raise _http(400, "invalid_username", uerr)
    perr = auth_core.validate_password(password)
    if perr:
        raise _http(400, "invalid_password", perr)
    clean_ip = (ip or "").strip()
    # 每 IP 注册限流：被拒绝的请求不落行、不创建用户（同登录限流口径）
    if await asyncio.to_thread(
        auth_core.sliding_window_rate_limited,
        "register_ip",
        f"ip:{clean_ip}",
        mu.register_rate_limit_per_hour,
        _REGISTER_WINDOW_SECONDS,
    ):
        raise _http(
            429,
            "register_rate_limited",
            "Too many registration attempts; please retry later",
        )
    # IP 策略判定：存量账号数与决策均为同步压库/mmdb 操作，调度到线程池
    existing = await asyncio.to_thread(
        registration_policy.count_registered_accounts, clean_ip
    )
    decision = await asyncio.to_thread(
        registration_policy.evaluate_registration_ip, clean_ip, existing, mu
    )
    status = "active" if decision.approved else "pending"
    try:
        user = await asyncio.to_thread(
            auth_core.create_user,
            username,
            password,
            email,
            role="user",
            status=status,
            registered_ip=clean_ip,
            auto_approved=decision.approved,
        )
    except ValueError as e:
        if "Email already taken" in str(e):
            raise _http(409, "email_taken", str(e))
        # create_user 已通过前置校验，唯一剩余 ValueError 为用户名重复
        raise _http(409, "username_taken", str(e))
    logger.info(
        "Registration of %s from %s -> %s (%s)",
        username,
        clean_ip or "unknown-ip",
        status,
        decision.reason,
    )
    # 通知分支：待审批 → 管理员聚合摘要；自动通过 → 用户欢迎/开通邮件
    try:
        if decision.approved:
            await asyncio.to_thread(mail.notify_account_status, user, "approve")
        else:
            await asyncio.to_thread(mail.notify_pending_registrations)
    except Exception as e:  # noqa: BLE001
        logger.warning("Registration notification failed: %s", e)
    return {
        "user": user,
        # 前端据此区分「已自动开通，去登录」与「等待管理员审批」两种结果页
        "status": status,
        "message": (
            "Registration approved; you can sign in now"
            if decision.approved
            else "Registration submitted; waiting for admin approval"
        ),
    }


async def login(username: str, password: str, remember: bool, ip: str) -> dict:
    """校验凭据并创建会话，返回 {"token": ..., "user": ...}。"""
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _not_found()
    # v2 P1b 起限流计数落 SQLite（rate_limit_events 表）：写事务必须调度到
    # 线程池，禁止在事件循环内同步压库
    if await asyncio.to_thread(auth_core.login_rate_limited, username, ip):
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

    token = await asyncio.to_thread(auth_core.create_session, user["id"], remember, ip)
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


# ── 自助清理（v2 P4）────────────────────────────────────────────
async def cleanup_derived_data(user: dict, category: str) -> dict:
    """删除当前用户某分类衍生目录下的全部内容并即时刷新用量缓存。

    仅作用于本人数据（user 来自会话，无跨用户参数面）；未知分类返回
    400 invalid_category。返回刷新后的分类用量明细供前端即时更新。
    """
    from backend.core.multiuser import quota, userspace

    if category not in userspace.DERIVED_SUBDIRS:
        raise _http(
            400,
            "invalid_category",
            f"Unknown derived data category: {category}",
        )
    derived_usage = await asyncio.to_thread(
        quota.cleanup_derived_category, user["id"], category
    )
    return {"status": "ok", "category": category, "derived_usage": derived_usage}


# ── 自助找回密码与邮箱绑定（v2 P6）──────────────────────────────
#
# 安全口径：
# - forgot-password 响应恒定时长：无论命中与否都执行一次 argon2 占位校验
#   （与 login 同一防时序枚举手段），响应语义统一——只有「邮件已投递」
#   与「无法投递」两种结果，后者不区分账号不存在/未绑邮箱/重复邮箱/SMTP
#   未启用，不泄露账号存在性。与 login 同口径：IP 限流先于 argon2 校验，
#   被限流的请求不执行占位校验（未限流请求每 IP 每分钟至多
#   FORGOT_IP_LIMIT_PER_MINUTE 次 argon2 开销）。
# - IP 维度限流复用 rate_limit_events 表（kind='forgot_ip' / 'reset_ip'），
#   目标维度去抖由 mail.reset_target_debounced（每邮箱 5 分钟 1 次）承担。
_FORGOT_TIMING_GUARD_PASSWORD = "forgot-password-timing-equalizer"


async def forgot_password(identifier: str, ip: str) -> dict:
    """自助找回入口：按用户名查邮箱或按邮箱查用户，命中且唯一则投递令牌。

    始终返回 200 + {"delivery": "sent"|"unavailable"}；unavailable 时前端
    展示「请联系管理员」文案（对应错误码 email_not_available 的引导语义）。
    """
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _not_found()
    identity = (identifier or "").strip().lower()
    if not identity:
        raise _http(400, "invalid_request", "Identifier is required")
    # 先限流（与 login 同序）：被拒绝的请求不落行、不执行下方 argon2 占位校验
    if await asyncio.to_thread(
        auth_core.sliding_window_rate_limited,
        "forgot_ip",
        f"ip:{ip or ''}",
        mail.FORGOT_IP_LIMIT_PER_MINUTE,
    ):
        raise _http(429, "rate_limited", "Too many requests; please retry later")
    # 恒定时长防护：命中路径的 DB 查询 + 令牌生成开销极小，统一叠加一次
    # argon2 校验拉平耗时（占位哈希校验必然失败，仅消耗等量 CPU）
    await asyncio.to_thread(
        auth_core.check_credentials, None, _FORGOT_TIMING_GUARD_PASSWORD
    )

    target = None
    user = await asyncio.to_thread(auth_core.get_user_by_username, identity)
    if user is not None:
        target = user
    else:
        matches = await asyncio.to_thread(auth_core.get_users_by_email, identity)
        if len(matches) == 1:  # 多个同邮箱用户 → 无法唯一定位，走降级提示
            target = matches[0]

    deliverable = (
        target is not None
        and bool((target.get("email") or "").strip())
        and mail.smtp_ready()
    )
    if not deliverable:
        return {"status": "ok", "delivery": "unavailable"}
    # 每邮箱 5 分钟去抖：窗口内重复请求不新建令牌、不重复投递，
    # 对外语义保持一致（delivery=sent，即"邮件已发出过"）
    debounced = await asyncio.to_thread(mail.reset_target_debounced, target["email"])
    if debounced:
        logger.debug("Reset token delivery debounced for %s", target["username"])
        return {"status": "ok", "delivery": "sent"}
    token, _expires = await asyncio.to_thread(
        auth_core.create_password_reset_token, target["id"]
    )
    await asyncio.to_thread(mail.notify_password_reset_token, target, token)
    return {"status": "ok", "delivery": "sent"}


async def reset_password_with_token(token: str, new_password: str, ip: str) -> dict:
    """凭邮件令牌自助重置密码：argon2id 写入 + 吊销全部会话 + 令牌标记 used。"""
    if not get_config().multi_user.enabled:
        raise _not_found()
    if await asyncio.to_thread(
        auth_core.sliding_window_rate_limited,
        "reset_ip",
        f"ip:{ip or ''}",
        mail.RESET_IP_LIMIT_PER_MINUTE,
    ):
        raise _http(429, "rate_limited", "Too many requests; please retry later")
    err = auth_core.validate_password(new_password)
    if err:
        raise _http(400, "invalid_password", err)
    user_id = await asyncio.to_thread(auth_core.consume_password_reset_token, token)
    if user_id is None:
        raise _http(400, "reset_token_invalid", "Reset link is invalid or expired")
    try:
        await asyncio.to_thread(auth_core.reset_password, user_id, new_password)
    except ValueError as e:
        raise _http(400, "invalid_password", str(e))
    return {"status": "ok"}


async def update_profile_email(user: dict, current_password: str, email: str) -> dict:
    """绑定/修改邮箱：当前密码确认 + 格式与唯一性校验（空串 = 解绑）。"""
    fresh = await asyncio.to_thread(auth_core.get_user_by_id, user["id"])
    if fresh is None:
        raise _not_found()
    ok = await asyncio.to_thread(
        auth_core.check_credentials, fresh["password_hash"], current_password
    )
    if not ok:
        raise _http(401, "invalid_credentials", "Invalid username or password")
    err = auth_core.validate_email(email)
    if err:
        raise _http(400, "invalid_email", err)
    try:
        updated = await asyncio.to_thread(auth_core.set_user_email, user["id"], email)
    except ValueError as e:
        if "Email already taken" in str(e):
            raise _http(409, "email_taken", str(e))
        raise _http(400, "invalid_email", str(e))
    return {"user": updated}


# ── 管理员：用户管理 ────────────────────────────────────────────
async def admin_list_users() -> list[dict]:
    return await asyncio.to_thread(auth_core.list_users)


async def admin_set_status(user_id: str, status: str, action: str) -> dict:
    if await asyncio.to_thread(auth_core.get_user_by_id, user_id) is None:
        raise _not_found()
    user = await asyncio.to_thread(auth_core.set_user_status, user_id, status)
    # 审批通过/拒绝/禁用即时通知该用户（v2 P6；邮件失败不影响管理操作）
    try:
        await asyncio.to_thread(mail.notify_account_status, user, action)
    except Exception as e:  # noqa: BLE001
        logger.warning("Account status notification failed: %s", e)
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
    # 通知该用户新临时密码与改密提醒（v2 P6；邮件失败不影响重置操作）
    try:
        await asyncio.to_thread(mail.notify_admin_password_reset, user, temp_password)
    except Exception as e:  # noqa: BLE001
        logger.warning("Admin reset password notification failed: %s", e)
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

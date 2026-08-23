"""
多用户认证核心（纯同步函数层）。

职责：
    1. 密码哈希（argon2id）与校验
    2. 会话令牌创建 / 校验 / 吊销（数据库只存 SHA-256 哈希）
    3. 用户管理：注册（pending）、状态流转、管理员引导、重置密码、配额覆盖
    4. 登录限流（每用户名 + 每 IP 分钟级）

约定：
    - 本模块全部为同步函数；FastAPI 层 / 中间件通过 asyncio.to_thread 调用，
      禁止阻塞事件循环
    - 所有会引发业务语义失败的操作以 ValueError / 标准异常表达，
      router 层负责映射为 HTTPError
    - 方法内实时调用 get_config()（限流阈值、会话 TTL 等不缓存）
"""

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.core.config import get_config
from backend.core.logger import get_logger
from . import userspace
from .db import (
    db_count,
    db_execute,
    db_query_all,
    db_query_one,
    is_initialized,
    transaction,
    utcnow,
)

logger = get_logger("multiuser.auth")

# ── 常量 ────────────────────────────────────────────────────────
USER_STATUSES = ("pending", "active", "disabled")
ROLES = ("admin", "user")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@\-\u4e00-\u9fff]{2,32}$")
MIN_PASSWORD_LENGTH = 8
# 邮箱格式校验（宽松实用型：local@domain.tld；拒绝空白与 C0/C1 控制字符，
# 防止经 To 头 / SMTP 信封注入；大小写不敏感唯一性由 ux_users_email_unique
# 部分唯一索引保证）
EMAIL_RE = re.compile(
    r"^[^\x00-\x20@\x7f-\x9f]+@[^\x00-\x20@\x7f-\x9f]+\.[A-Za-z]{2,}$"
)
SESSION_COOKIE_NAME = "latexcellence_session"
# 双提交 CSRF：登录时下发可被 JS 读取的 CSRF Cookie，中间件校验请求头与其值一致。
# 该 Cookie 不设 HttpOnly，SPA 读取其值放入 X-Latexcellence-CSRF 头。
CSRF_COOKIE_NAME = "latexcellence_csrf"
CSRF_HEADER_NAME = "X-Latexcellence-CSRF"
# 自助找回令牌 TTL（v2 P6）：1 小时、单次有效（只存 SHA-256）
PASSWORD_RESET_TTL_SECONDS = 3600

_hasher = PasswordHasher()

# 恒定时长占位哈希（防时序侧信道枚举用户名），懒加载缓存
_dummy_hash: str | None = None


# ── 密码 ────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _ensure_dummy_hash() -> str:
    """生成/取用恒定时长占位哈希（argon2 情景与真实用户一致）。"""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("timing-equalizer-dummy-password")
    return _dummy_hash


def check_credentials(hashed: str | None, password: str) -> bool:
    """对（可能不存在的）用户做恒定时长的密码校验。

    用户名不存在时也执行一次 argon2 验证（针对占位哈希），使"用户不存在"
    与"密码错误"的响应耗时一致，封堵时序侧信道枚举有效用户名。
    hashed 为 None 表示用户不存在。
    """
    if hashed is None:
        verify_password(password, _ensure_dummy_hash())
        return False
    return verify_password(password, hashed)


# ── 校验 ────────────────────────────────────────────────────────
def validate_username(username: str) -> str | None:
    """校验用户名，返回错误消息（合法返回 None）。2–32 字符，允许中文。"""
    if not username or not username.strip():
        return "Username must be 2-32 characters"
    if "\x00" in username or "/" in username or "\\" in username:
        return "Username contains illegal characters"
    if not USERNAME_RE.match(username):
        return "Username must be 2-32 characters and contain only letters, digits, '_', '-', '.', '@' or Chinese"
    return None


def validate_password(password: str) -> str | None:
    """校验密码强度，返回错误消息（合法返回 None）。"""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    return None


def validate_email(email: str) -> str | None:
    """校验邮箱格式，返回错误消息（合法返回 None）。空值合法（邮箱可选）。"""
    email = (email or "").strip()
    if not email:
        return None
    if len(email) > 254 or not EMAIL_RE.match(email):
        return "Invalid email address"
    return None


# ── Token / 会话 ────────────────────────────────────────────────
def new_token() -> str:
    """生成 32 字节随机会话令牌（base64url，~43 字符）。"""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """计算会话令牌的 SHA-256 哈希（数据库只存哈希）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_ttl(remember: bool) -> timedelta:
    cfg = get_config().multi_user
    days = cfg.remember_ttl_days if remember else cfg.session_ttl_days
    return timedelta(days=days)


def create_session(user_id: str, remember: bool = False) -> str:
    """创建会话并返回明文令牌；过期时间由配置决定（默认 7 天 / 记住我 30 天）。"""
    token = new_token()
    expires_at = datetime.now(timezone.utc) + _session_ttl(remember)
    db_execute(
        "INSERT INTO sessions (id, user_id, token_hash, expires_at, remember, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex,
            user_id,
            token_hash(token),
            expires_at.replace(microsecond=0).isoformat(),
            1 if remember else 0,
            utcnow(),
        ),
    )
    return token


def validate_session(token: str) -> dict | None:
    """
    校验会话令牌，返回对应用户（已脱敏：不含 password_hash）。
    会话过期、用户不存在、账号非 active 均视为无效。
    """
    if not token:
        return None
    row = db_query_one(
        "SELECT u.id, u.username, u.email, u.role, u.status, "
        "u.project_quota_bytes, u.derived_quota_bytes "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?",
        (token_hash(token), utcnow()),
    )
    if row is None or row["status"] != "active":
        return None
    return row


def delete_session(token: str) -> bool:
    if not token:
        return False
    return (
        db_execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
        > 0
    )


def revoke_user_sessions(user_id: str) -> int:
    """吊销指定用户全部会话（禁用/删除账号时调用）。"""
    return db_execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def revoke_password_reset_tokens(user_id: str) -> int:
    """吊销（删除）指定用户全部仍有效的找回令牌。

    密码重置 / 邮箱改绑 / 账号禁用等安全状态变更时必须调用：变更前经
    forgot 签发的令牌若不回收，持有者可在 TTL（1 小时）窗口内凭旧令牌
    再次重置密码夺回控制权。按表结构语义只删未消费且未过期的行——
    已消费行由 consume_password_reset_token 原子标记 used_at、过期行由
    维护循环 purge_expired_reset_tokens 回收。返回删除的行数。
    """
    removed = db_execute(
        "DELETE FROM password_reset_tokens "
        "WHERE user_id = ? AND used_at IS NULL AND expires_at > ?",
        (user_id, utcnow()),
    )
    if removed:
        logger.info("Revoked %d pending password reset token(s)", removed)
    return removed


def purge_expired_sessions() -> int:
    """删除已过期的会话行（v2 P4：并入维护循环，修复 sessions 表无限增长）。

    validate_session 本身按 expires_at 过滤，过期行不影响鉴权正确性；
    本函数只负责回收存储。返回删除的行数。"""
    removed = db_execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))
    if removed:
        logger.info("Purged %d expired session(s)", removed)
    return removed


# ── 用户管理 ────────────────────────────────────────────────────
def _row_to_public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row.get("email", ""),
        "role": row["role"],
        "status": row["status"],
        "project_quota_bytes": row.get("project_quota_bytes"),
        "derived_quota_bytes": row.get("derived_quota_bytes"),
        "created_at": row["created_at"],
    }


to_public_user = _row_to_public_user


def create_user(
    username: str,
    password: str,
    email: str = "",
    *,
    role: str = "user",
    status: str = "pending",
) -> dict:
    """创建用户（默认 pending，待管理员审批）。用户名重复抛 ValueError。"""
    uname = username.strip()
    err = validate_username(uname)
    if err:
        raise ValueError(err)
    err = validate_password(password)
    if err:
        raise ValueError(err)
    clean_email = (email or "").strip()
    err = validate_email(clean_email)
    if err:
        raise ValueError(err)
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    if status not in USER_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    # 邮箱唯一性前置校验（v2 P6）：给出精确错误；并发窗口由部分唯一索引兜底
    if clean_email and get_users_by_email(clean_email):
        raise ValueError(f"Email already taken: {clean_email}")

    user_id = uuid.uuid4().hex
    now = utcnow()
    try:
        db_execute(
            "INSERT INTO users (id, username, password_hash, email, role, status, "
            "project_quota_bytes, derived_quota_bytes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            (
                user_id,
                uname,
                hash_password(password),
                clean_email,
                role,
                status,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as e:  # 唯一索引冲突：区分用户名 / 邮箱
        msg = str(e)
        if "ux_users_email_unique" in msg or "users.email" in msg:
            raise ValueError(f"Email already taken: {clean_email}")
        raise ValueError(f"Username already taken: {uname}")
    userspace.ensure_user_dirs(user_id)
    logger.info("User created: %s (role=%s, status=%s)", uname, role, status)
    # 返回脱敏后的公开用户信息，严禁携带 password_hash
    return to_public_user(get_user_by_id(user_id))


def get_user_by_username(username: str) -> dict | None:
    return db_query_one(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
    )


def get_users_by_email(email: str) -> list[dict]:
    """按邮箱查用户（大小写不敏感，可能多条——存量重复邮箱场景）。"""
    clean = (email or "").strip()
    if not clean:
        return []
    return db_query_all("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (clean,))


def set_user_email(user_id: str, email: str) -> dict:
    """绑定/修改用户邮箱（空字符串 = 解绑）。唯一冲突抛 ValueError。"""
    clean = (email or "").strip()
    err = validate_email(clean)
    if err:
        raise ValueError(err)
    existing = get_user_by_id(user_id)
    if existing is None:
        raise ValueError("User not found")
    if clean:
        for row in get_users_by_email(clean):
            if row["id"] != user_id:
                raise ValueError(f"Email already taken: {clean}")
    try:
        db_execute(
            "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
            (clean, utcnow(), user_id),
        )
    except sqlite3.IntegrityError:  # 部分唯一索引兜底（并发窗口）
        raise ValueError(f"Email already taken: {clean}")
    # 邮箱改绑属安全状态变更：旧邮箱持有者改绑前经 forgot 申请的令牌必须
    # 立即失效，防止其在 TTL 窗口内仍可重置本账号密码
    revoke_password_reset_tokens(user_id)
    logger.info("Email updated for user %s", existing["username"])
    return to_public_user(get_user_by_id(user_id))


def get_user_by_id(user_id: str) -> dict | None:
    return db_query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def list_users() -> list[dict]:
    rows = db_query_all(
        "SELECT id, username, email, role, status, project_quota_bytes, "
        "derived_quota_bytes, created_at FROM users ORDER BY created_at, id"
    )
    return [_row_to_public_user(r) for r in rows]


def set_user_status(user_id: str, status: str) -> dict:
    if status not in USER_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    existing = get_user_by_id(user_id)
    if existing is None:
        raise ValueError("User not found")
    db_execute(
        "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow(), user_id),
    )
    # 禁用账号即时吊销全部会话
    if status in ("disabled",):
        revoke_user_sessions(user_id)
        # 禁用属止损操作，且 forgot 入口不校验账号状态（pending/disabled
        # 账号同样可被签发令牌）：禁用前签发的未消费找回令牌必须一并吊销，
        # 防止复启用后旧令牌在 TTL 内重置密码
        revoke_password_reset_tokens(user_id)
    logger.info("User %s status -> %s", existing["username"], status)
    return to_public_user(get_user_by_id(user_id))


def set_user_role(user_id: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    db_execute(
        "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
        (role, utcnow(), user_id),
    )
    return to_public_user(get_user_by_id(user_id))


def _move_tree(src: Path, dst: Path) -> None:
    """把用户空间内的目录迁移到目标（同分区 rename，跨分区复制兜底）。"""
    if src == dst:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists() or src.is_symlink():
        shutil.move(str(src), str(dst))
    else:
        dst.mkdir(parents=True, exist_ok=True)


def delete_user(user_id: str, acting_admin_id: str) -> dict:
    """删除用户（对应方案 §6.3）。

    1. 用户拥有的项目全部转交给定管理员（活动项目同名去重为 `xx (2)`）；
    2. 移除该用户的协作者关系；
    3. 立即吊销其全部会话；
    4. 用户空间（剩余衍生数据）归档到 <users_root 父目录>/archive/<user_id>_<时间戳>，
       由维护任务在保留期后清理；
    5. 删除 users 行。

    业务失败抛 ValueError（由 service 层映射为 HTTP 错误）。
    """
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")
    if user_id == acting_admin_id:
        raise ValueError("Cannot delete the current account")
    admin = get_user_by_id(acting_admin_id)
    if admin is None or admin["role"] != "admin":
        raise ValueError("Only an admin can delete users")

    from .quota import next_available_name

    now = utcnow()
    # 项目转交管理员（活动 + 回收站项目；回收站项目保留原删除状态）
    for row in db_query_all(
        "SELECT id, name, status FROM projects WHERE owner_id = ?", (user_id,)
    ):
        new_name = row["name"]
        if row["status"] == "active":
            new_name = next_available_name(acting_admin_id, row["name"])
        _move_tree(
            userspace.project_dir(user_id, row["id"]),
            userspace.project_dir(acting_admin_id, row["id"]),
        )
        db_execute(
            "UPDATE projects SET owner_id = ?, name = ?, updated_at = ? WHERE id = ?",
            (acting_admin_id, new_name, now, row["id"]),
        )
    db_execute("DELETE FROM project_collaborators WHERE user_id = ?", (user_id,))
    revoke_user_sessions(user_id)

    # 归档剩余用户空间（衍生数据；projects 已迁走）
    user_root = userspace.user_root(user_id)
    if user_root.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        archive_dir = userspace.get_archive_root() / f"{user_id}_{stamp}"
        _move_tree(user_root, archive_dir)
        # rename 保留旧 mtime（≈账号开通时间），而清理任务按 mtime 判保留期；
        # 必须重置为删除时刻，否则老账号的归档会立即被当作过期清除
        os.utime(archive_dir, None)
        logger.info(
            "Archived deactivated user data of %s to %s",
            user["username"],
            archive_dir,
        )

    db_execute("DELETE FROM users WHERE id = ?", (user_id,))
    logger.info(
        "User deleted: %s (id=%s) by admin %s",
        user["username"],
        user_id,
        acting_admin_id,
    )
    return {"status": "ok", "user_id": user_id, "username": user["username"]}


def reset_password(user_id: str, new_password: str) -> dict:
    """重置密码并吊销该用户全部会话（密码明文只在返回时一次性可见）。"""
    err = validate_password(new_password)
    if err:
        raise ValueError(err)
    existing = get_user_by_id(user_id)
    if existing is None:
        raise ValueError("User not found")
    db_execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(new_password), utcnow(), user_id),
    )
    revoke_user_sessions(user_id)
    # 凭据变更：一并吊销未消费的找回令牌，防止此前经 forgot 签发的令牌
    # 在 TTL（1 小时）窗口内再次重置密码夺回控制权
    revoke_password_reset_tokens(user_id)
    logger.info("Password reset for user %s", existing["username"])
    return to_public_user(get_user_by_id(user_id))


def set_user_quota(
    user_id: str, project_quota_bytes: int | None, derived_quota_bytes: int | None
) -> dict:
    """调整用户配额覆盖（NULL 表示跟随全局默认）。"""
    if project_quota_bytes is not None and project_quota_bytes < 0:
        raise ValueError("project_quota_bytes must be >= 0 or null")
    if derived_quota_bytes is not None and derived_quota_bytes < 0:
        raise ValueError("derived_quota_bytes must be >= 0 or null")
    existing = get_user_by_id(user_id)
    if existing is None:
        raise ValueError("User not found")
    db_execute(
        "UPDATE users SET project_quota_bytes = ?, derived_quota_bytes = ?, updated_at = ? "
        "WHERE id = ?",
        (project_quota_bytes, derived_quota_bytes, utcnow(), user_id),
    )
    return to_public_user(get_user_by_id(user_id))


def ensure_bootstrap_admin() -> dict | None:
    """首次启动引导：用户表为空时从环境变量创建管理员账号。

    未配置环境变量时仅记录警告，等待 CLI（python -m backend.manage create-admin）。
    """
    cfg = get_config()
    if not cfg.multi_user.enabled:
        return None
    if db_count("SELECT COUNT(*) FROM users") > 0:
        return None
    uname = os.environ.get(cfg.multi_user.admin_username_env, "")
    pwd = os.environ.get(cfg.multi_user.admin_password_env, "")
    if not uname or not pwd:
        logger.warning(
            "No users exist and %s/%s are not set; "
            "create the bootstrap admin with 'python -m backend.manage create-admin'",
            cfg.multi_user.admin_username_env,
            cfg.multi_user.admin_password_env,
        )
        return None
    user = create_user(uname, pwd, email="", role="admin", status="active")
    logger.info("Bootstrap admin created from environment: %s", uname)
    return user


# ── 登录限流 ────────────────────────────────────────────────────
#
# v2 P1b：进程内字典计数器迁移至 rate_limit_events 表（kind='login'），
# 修复多实例部署下阈值被副本数放大的问题（P7 起跨副本天然生效）。
# 同一行登录尝试落两条事件：pair 维度（用户名小写+IP）与 user 全局维度
# （用户名小写），任一维度窗口计数达到阈值即拒绝；被拒绝的尝试不落行。
_LOGIN_WINDOW_SECONDS = 60


def login_rate_limited(username: str, ip: str) -> bool:
    """
    记录一次登录尝试；在窗口（60s）内超过配置上限返回 True。

    双维度独立计数：(username 小写, IP) 与 username 全局，任一超限即拒绝。
    用户名维度为全局计数：防止持有多个出口 IP 的攻击者对单账号线性放大
    在线爆破。须先 init_db()（多用户模式 lifespan 保证）。
    """
    cfg = get_config().multi_user
    limit = cfg.login_rate_limit_per_minute
    uname = username.strip().lower()
    pair_identity = f"pair:{uname}|{ip or ''}"
    user_identity = f"user:{uname}"
    now = datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat()
    window_start = (
        (now - timedelta(seconds=_LOGIN_WINDOW_SECONDS))
        .replace(microsecond=0)
        .isoformat()
    )
    with transaction() as conn:
        pair_count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_events "
            "WHERE kind = 'login' AND identity = ? AND ts >= ?",
            (pair_identity, window_start),
        ).fetchone()[0]
        user_count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_events "
            "WHERE kind = 'login' AND identity = ? AND ts >= ?",
            (user_identity, window_start),
        ).fetchone()[0]
        if pair_count >= limit or user_count >= limit:
            return True
        conn.executemany(
            "INSERT INTO rate_limit_events (kind, identity, ts) VALUES ('login', ?, ?)",
            [(pair_identity, now_iso), (user_identity, now_iso)],
        )
        return False


def reset_login_rate_limits() -> None:
    """清空登录限流记录（测试隔离 / 运维使用）；库未初始化时静默跳过。"""
    if not is_initialized():
        return
    db_execute("DELETE FROM rate_limit_events WHERE kind = 'login'")


def sliding_window_rate_limited(
    kind: str, identity: str, limit: int, window_seconds: int = _LOGIN_WINDOW_SECONDS
) -> bool:
    """通用滑动窗口限流（v2 P6：自助找回 forgot/reset 的 IP 维度复用）。

    窗口内该 (kind, identity) 计数达到上限返回 True（本次不落行，同登录
    限流口径）；否则落一行事件并放行。"""
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    window_start = (
        (datetime.now(timezone.utc) - timedelta(seconds=window_seconds))
        .replace(microsecond=0)
        .isoformat()
    )
    with transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_events "
            "WHERE kind = ? AND identity = ? AND ts >= ?",
            (kind, identity, window_start),
        ).fetchone()[0]
        if count >= limit:
            return True
        conn.execute(
            "INSERT INTO rate_limit_events (kind, identity, ts) VALUES (?, ?, ?)",
            (kind, identity, now_iso),
        )
        return False


# ── 自助找回令牌（v2 P6）────────────────────────────────────────
#
# 随机令牌明文只在投递邮件时可见，数据库只存 SHA-256（与会话同口径）；
# TTL 1 小时、单次有效（消费走条件 UPDATE 原子标记，天然防重放）。


def create_password_reset_token(user_id: str) -> tuple[str, str]:
    """为用户创建找回令牌，返回（明文令牌, 过期时间 ISO）。"""
    token = new_token()
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS))
        .replace(microsecond=0)
        .isoformat()
    )
    db_execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, "
        "used_at, created_at) VALUES (?, ?, ?, NULL, ?)",
        (user_id, token_hash(token), expires_at, utcnow()),
    )
    return token, expires_at


def consume_password_reset_token(token: str) -> str | None:
    """校验并消费找回令牌：有效（未过期且未使用）则原子标记 used 并返回
    user_id；无效/过期/重放返回 None。"""
    if not token:
        return None
    now = utcnow()
    with transaction() as conn:
        row = conn.execute(
            "SELECT user_id FROM password_reset_tokens "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (token_hash(token), now),
        ).fetchone()
        if row is None:
            return None
        updated = conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? "
            "WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash(token)),
        ).rowcount
        if updated != 1:  # 并发竞争：另一请求已消费
            return None
        return row["user_id"]


def purge_expired_reset_tokens() -> int:
    """清理已过期/已消费的找回令牌行（并入维护循环，修复无限增长）。"""
    if not is_initialized():
        return 0
    removed = db_execute(
        "DELETE FROM password_reset_tokens WHERE expires_at < ? OR used_at IS NOT NULL",
        (utcnow(),),
    )
    if removed:
        logger.info("Purged %d password reset token(s)", removed)
    return removed

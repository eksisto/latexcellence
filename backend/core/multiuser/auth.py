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
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from backend.core.config import get_config
from backend.core.logger import get_logger
from . import userspace
from .db import db_count, db_execute, db_query_all, db_query_one, utcnow

logger = get_logger("multiuser.auth")

# ── 常量 ────────────────────────────────────────────────────────
USER_STATUSES = ("pending", "active", "disabled")
ROLES = ("admin", "user")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@\-\u4e00-\u9fff]{2,32}$")
MIN_PASSWORD_LENGTH = 8
SESSION_COOKIE_NAME = "latexcellence_session"
# 双提交 CSRF：登录时下发可被 JS 读取的 CSRF Cookie，中间件校验请求头与其值一致。
# 该 Cookie 不设 HttpOnly，SPA 读取其值放入 X-Latexcellence-CSRF 头。
CSRF_COOKIE_NAME = "latexcellence_csrf"
CSRF_HEADER_NAME = "X-Latexcellence-CSRF"

_hasher = PasswordHasher()

# 登录限流：{(username_lower, ip): [timestamp, ...]} 与
# {username_lower: [timestamp, ...]}（进程内，窗口 60s）。
# 用户名维度为全局计数：防止持有多个出口 IP 的攻击者对单账号线性放大在线爆破
_rate_lock = threading.Lock()
_login_records: dict[tuple[str, str], list[float]] = {}
_username_records: dict[str, list[float]] = {}
_last_rate_sweep = 0.0

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
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    if status not in USER_STATUSES:
        raise ValueError(f"Invalid status: {status}")

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
                email.strip(),
                role,
                status,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:  # 唯一索引冲突 -> 用户名重复（大小写不敏感）
        raise ValueError(f"Username already taken: {uname}")
    userspace.ensure_user_dirs(user_id)
    logger.info("User created: %s (role=%s, status=%s)", uname, role, status)
    # 返回脱敏后的公开用户信息，严禁携带 password_hash
    return to_public_user(get_user_by_id(user_id))


def get_user_by_username(username: str) -> dict | None:
    return db_query_one(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
    )


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
def login_rate_limited(username: str, ip: str) -> bool:
    """
    记录一次登录尝试；在窗口（60s）内超过配置上限返回 True。

    双维度独立计数：(username 小写, IP) 与 username 全局，任一超限即拒绝。
    定期裁剪过期条目，避免字典无界增长。
    """
    global _last_rate_sweep
    cfg = get_config().multi_user
    limit = cfg.login_rate_limit_per_minute
    uname = username.strip().lower()
    pair_key = (uname, ip or "")
    now = time.time()
    with _rate_lock:
        # 周期清理：删除 60s 内无新的键，保证内存有界
        if now - _last_rate_sweep > 60.0:
            cutoff = now - 60.0
            for k in list(_login_records):
                stamps = _login_records[k]
                if not stamps or stamps[-1] < cutoff:
                    del _login_records[k]
            for k in list(_username_records):
                stamps = _username_records[k]
                if not stamps or stamps[-1] < cutoff:
                    del _username_records[k]
            _last_rate_sweep = now
        alive_pair = [t for t in _login_records.get(pair_key, []) if t >= now - 60.0]
        alive_user = [t for t in _username_records.get(uname, []) if t >= now - 60.0]
        if len(alive_pair) >= limit or len(alive_user) >= limit:
            _login_records[pair_key] = alive_pair
            _username_records[uname] = alive_user
            return True
        alive_pair.append(now)
        alive_user.append(now)
        _login_records[pair_key] = alive_pair
        _username_records[uname] = alive_user
        return False


def reset_login_rate_limits() -> None:
    """清空登录限流记录（测试隔离 / 运维使用）。"""
    with _rate_lock:
        _login_records.clear()
        _username_records.clear()

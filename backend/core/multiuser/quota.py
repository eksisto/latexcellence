"""
多用户模式配额记账与项目维护核心（纯同步函数层，P3）。

职责：
    1. 配额解析：项目空间 / 衍生数据空间的有效额度（用户覆盖优先于全局默认）
    2. 用量统计：项目空间按 projects.size_bytes（缓存）汇总；衍生数据按磁盘实时扫描
    3. 上传 / 写回 / 恢复前置校验（超限抛 HTTPException 413 quota_exceeded）
    4. 项目尺寸对账（reconcile_project_sizes，后台周期修正缓存漂移）
    5. 回收站清理（clean_expired_recycle_bin，按 recycle_bin_days 永久删除）

约定：
    - 本模块为同步核心；FastAPI 层通过 asyncio.to_thread 调度，禁止阻塞事件循环
    - 方法内实时调用 get_config()，不在模块级/实例初始化时缓存路径与额度
    - 业务级失败以 HTTPException 表达（与 access_control 一致），便于路由直接透传；
      纯计算函数不抛 HTTP，只返回数值
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from backend.core.config import get_config
from backend.core.logger import get_logger
from . import userspace
from .db import db_execute, db_query_all, db_query_one, utcnow

logger = get_logger("multiuser.quota")


def _quota_exceeded(message: str) -> HTTPException:
    return HTTPException(
        status_code=413, detail={"error": "quota_exceeded", "message": message}
    )


# ── 有效额度解析 ──────────────────────────────────────────────────
def effective_project_quota(user_id: str) -> int:
    """返回用户有效项目空间配额（字节）。NULL 覆盖跟随全局默认。"""
    cfg = get_config().multi_user
    row = db_query_one("SELECT project_quota_bytes FROM users WHERE id = ?", (user_id,))
    override = row["project_quota_bytes"] if row else None
    if override is not None:
        return int(override)
    return int(cfg.default_project_quota_mb) * 1024 * 1024


def effective_derived_quota(user_id: str) -> int:
    """返回用户有效衍生数据配额（字节）。NULL 覆盖跟随全局默认。"""
    cfg = get_config().multi_user
    row = db_query_one("SELECT derived_quota_bytes FROM users WHERE id = ?", (user_id,))
    override = row["derived_quota_bytes"] if row else None
    if override is not None:
        return int(override)
    return int(cfg.default_derived_quota_mb) * 1024 * 1024


# ── 用量统计 ─────────────────────────────────────────────────────
def project_usage_for_user(user_id: str) -> int:
    """返回用户所有活动项目的 size_bytes 之和（回收站不计入，删除即腾出额度）。"""
    return int(
        db_query_one(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM projects "
            "WHERE owner_id = ? AND status = 'active'",
            (user_id,),
        )["total"]
    )


def derived_usage_for_user(user_id: str) -> int:
    """实时统计用户四个衍生数据目录的总大小（writing_chat / style_modify /
    templates / plots）。目录不存在返回 0。"""
    total = 0
    root = userspace.user_root(user_id)
    if not root.is_dir():
        return 0
    for sub in userspace.DERIVED_SUBDIRS:
        d = root / sub
        if d.is_dir():
            total += dir_size(d)
    return total


def user_usage(user_id: str) -> dict:
    """一次返回用户项目/衍生数据用量（用于 /api/auth/me 等）。"""
    return {
        "project_usage_bytes": project_usage_for_user(user_id),
        "derived_usage_bytes": derived_usage_for_user(user_id),
    }


def dir_size(path: Path) -> int:
    """递归统计目录内所有文件大小之和（符号链接不计入，异常容错）。"""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def project_size_on_disk(project_id: str) -> int:
    """按项目 id 从 DB 取 owner 后计算磁盘实际大小（供对账）。"""
    row = db_query_one("SELECT owner_id FROM projects WHERE id = ?", (project_id,))
    if row is None:
        return 0
    root = userspace.project_dir(row["owner_id"], project_id)
    if not root.is_dir():
        return 0
    return dir_size(root)


def reconcile_project_sizes() -> int:
    """对账：重算所有项目（含回收站）的磁盘大小并写回 size_bytes，返回更新行数。"""
    rows = db_query_all("SELECT id FROM projects")
    updated = 0
    for row in rows:
        size = project_size_on_disk(row["id"])
        cur = db_query_one("SELECT size_bytes FROM projects WHERE id = ?", (row["id"],))
        if cur is None or int(cur["size_bytes"]) != size:
            db_execute(
                "UPDATE projects SET size_bytes = ?, updated_at = ? WHERE id = ?",
                (size, utcnow(), row["id"]),
            )
            updated += 1
    if updated:
        logger.info("Reconciled %d project size entries", updated)
    return updated


# ── 配额前置校验 ──────────────────────────────────────────────────
def enforce_project_quota(user_id: str, extra_bytes: int) -> None:
    """在计入 extra_bytes 后项目空间未超限则通过，否则抛 413 quota_exceeded。

    extra_bytes 为增量（安装新项目 / 恢复回收站项目 / 写回增长）。"""
    if extra_bytes < 0:
        extra_bytes = 0
    used = project_usage_for_user(user_id)
    quota = effective_project_quota(user_id)
    if used + extra_bytes > quota:
        raise _quota_exceeded(
            "Project space quota exceeded: "
            f"need {used + extra_bytes} bytes but limit is {quota} bytes"
        )


def enforce_derived_quota(user_id: str, extra_bytes: int) -> None:
    """在计入 extra_bytes 后衍生数据未超限则通过，否则抛 413 quota_exceeded。"""
    if extra_bytes < 0:
        extra_bytes = 0
    used = derived_usage_for_user(user_id)
    quota = effective_derived_quota(user_id)
    if used + extra_bytes > quota:
        raise _quota_exceeded(
            "Derived data quota exceeded: "
            f"need {used + extra_bytes} bytes but limit is {quota} bytes"
        )


def update_project_size(project_id: str) -> int:
    """重算单个项目磁盘大小并写回 size_bytes，返回尺寸。"""
    size = project_size_on_disk(project_id)
    db_execute(
        "UPDATE projects SET size_bytes = ?, updated_at = ? WHERE id = ?",
        (size, utcnow(), project_id),
    )
    return size


def refresh_owner_usage_after_write(project_id: str) -> int:
    """某个项目写操作落盘后刷新其 owner 的项目用量缓存（size_bytes）。

    供 apply-change / Agent 编辑等写路径在成功后调用；返回本次写回后的
    该项目磁盘大小。"""
    row = db_query_one("SELECT owner_id FROM projects WHERE id = ?", (project_id,))
    if row is None:
        return 0
    return update_project_size(project_id)


# ── 回收站维护 ───────────────────────────────────────────────────
def clean_expired_recycle_bin() -> int:
    """永久删除超过 recycle_bin_days 的回收站项目（目录 + 数据行 + 协作者级联）。

    返回本次清理的项目数。幂等：可重复调用，无副作用。"""
    cfg = get_config().multi_user
    cutoff = (
        datetime.now(timezone.utc).replace(microsecond=0)
        - _days_delta(cfg.recycle_bin_days)
    ).isoformat()
    rows = db_query_all(
        "SELECT id, owner_id FROM projects WHERE status = 'deleted' "
        "AND deleted_at IS NOT NULL AND deleted_at < ?",
        (cutoff,),
    )
    removed = 0
    for row in rows:
        root = userspace.project_dir(row["owner_id"], row["id"])
        try:
            # 先删数据行（若目录删除失败，不再有孤儿记录；目录残留由对账/人工清理）
            db_execute(
                "DELETE FROM projects WHERE id = ? AND status = 'deleted'", (row["id"],)
            )
            if root.is_dir():
                shutil.rmtree(root)
            removed += 1
            logger.info(
                "Recycle bin purge: project %s (owner %s) permanently deleted",
                row["id"],
                row["owner_id"],
            )
        except OSError as e:
            logger.error("Failed to purge project %s: %s", row["id"], e)
    if removed:
        logger.info("Recycle bin purge removed %d expired projects", removed)
    return removed


# 暂存区保留期（秒）：单次上传的暂存目录生命周期 = 暂存拷贝 + 限时解压
# （默认 120s），远小于该值；维护循环只清理超过保留期的目录，
# 避免误删仍在进行中的上传（慢速链路 / 大包解压期间）
STAGING_RETENTION_SECONDS = 3600


def clean_staging() -> int:
    """清理残留暂存区目录（上传中断/失败留下的孤儿），返回清理数。

    只清 mtime 早于保留期的目录：写入压缩包与创建解压子目录都会刷新
    目录 mtime，进行中的上传不会被误删。"""
    import time

    staging = userspace.get_staging_root()
    if not staging.is_dir():
        return 0
    cutoff = time.time() - STAGING_RETENTION_SECONDS
    removed = 0
    for child in staging.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Cleaned %d leftover staging directories", removed)
    return removed


# ── 项目行写入（唯一命名 + 配额 + 落库）──────────────────────────
def next_available_name(owner_id: str, base_name: str, max_tries: int = 100) -> str:
    """为同名冲突生成 `xx (2)`、`xx (3)`…，返回第一个未被使用的活动项目名。"""
    name = base_name
    for i in range(2, max_tries + 1):
        exists = db_query_one(
            "SELECT id FROM projects WHERE owner_id = ? AND name = ? AND status = 'active'",
            (owner_id, name),
        )
        if exists is None:
            return name
        name = f"{base_name} ({i})"
    raise HTTPException(
        status_code=400,
        detail={
            "error": "invalid_archive",
            "message": f"Too many projects share the same name: {base_name}",
        },
    )


def create_project_row(
    owner_id: str,
    base_name: str,
    size_bytes: int,
    *,
    project_id: str | None = None,
    retry_name: bool = True,
) -> dict:
    """创建项目数据行（status='active'）。

    处理同名冲突：
        - 优先使用 next_available_name 预检；
        - 并发下仍可能命中唯一索引（UNIQUE owner_id, name WHERE active），
          捕获 IntegrityError 后重试生成 `xx (n)`（retry_name=True）。

    project_id 可显式传入（必须与磁盘目录名一致）；缺省生成新 UUID。

    返回写入后的 projects 行 dict。业务失败映射为 HTTPException（413 quota /
    400 invalid name）。"""
    import sqlite3
    import uuid

    from .db import db_query_one as _q

    name = next_available_name(owner_id, base_name)
    attempts = name
    project_id = project_id or uuid.uuid4().hex
    now = utcnow()
    try:
        db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
            (project_id, owner_id, attempts, size_bytes, now, now),
        )
    except sqlite3.IntegrityError:
        if not retry_name:
            raise
        # 并发冲突：重新取一个可用名称后重试一次（id 不变，仍与磁盘目录一致）
        name2 = next_available_name(owner_id, base_name)
        try:
            db_execute(
                "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
                "deleted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
                (project_id, owner_id, name2, size_bytes, now, now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_archive",
                    "message": "Failed to allocate a unique project name",
                },
            )
        attempts = name2

    row = _q("SELECT * FROM projects WHERE id = ?", (project_id,))
    logger.info(
        "Project created: id=%s owner=%s name=%s size=%d",
        project_id,
        owner_id,
        attempts,
        size_bytes,
    )
    return row


def _days_delta(days: int):
    from datetime import timedelta

    return timedelta(days=days)

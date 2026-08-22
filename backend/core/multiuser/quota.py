"""
多用户模式配额记账与项目维护核心（纯同步函数层，P3 / P4）。

职责：
    1. 配额解析：项目空间 / 衍生数据空间的有效额度（用户覆盖优先于全局默认）
    2. 用量统计：项目空间按 projects.size_bytes（缓存）汇总；衍生数据按
       user_derived_usage 表缓存读取（v2 P4 起 O(1)，不再每请求全盘扫描）
    3. 衍生数据记账：检查点增量刷新（触达某分类时仅重扫该用户该分类子目录）
       + 每小时对账全量重算校正漂移（reconcile_derived_usage）
    4. 上传 / 写回 / 恢复前置校验（超限抛 HTTPException 413 quota_exceeded）
    5. 项目尺寸对账（reconcile_project_sizes，后台周期修正缓存漂移）
    6. 回收站清理（clean_expired_recycle_bin，按 recycle_bin_days 永久删除）
    7. 用户自助清理（cleanup_derived_category，删除指定分类全部内容并即时刷新缓存）

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


# ── 衍生数据用量缓存（v2 P4）──────────────────────────────────────
def _derived_cache_is_empty() -> bool:
    return db_query_one("SELECT COUNT(*) AS n FROM user_derived_usage")["n"] == 0


def resync_user_derived_usage(user_id: str) -> int:
    """重扫单个用户全部四类衍生目录并刷新缓存行，返回总字节数。

    供自助清理 / 存量迁移 / 缓存引导使用；单分类的增量刷新走
    refresh_derived_usage，全量对账走 reconcile_derived_usage（含逐行
    变更检测，避免无谓写放大）。"""
    total = 0
    for sub in userspace.DERIVED_SUBDIRS:
        total += refresh_derived_usage(user_id, sub)
    return total


def reconcile_derived_usage() -> int:
    """对账：全量重算所有用户的衍生数据用量并校正缓存漂移。

    返回发生变化的缓存行数。由维护循环每小时调用；也承担冷启动引导
    （缓存表全空时 derived_usage_breakdown 首读会触发一次本函数）。"""
    rows = db_query_all("SELECT id FROM users")
    updated = 0
    for row in rows:
        for sub in userspace.DERIVED_SUBDIRS:
            size = _scan_derived_dir(row["id"], sub)
            cur = db_query_one(
                "SELECT size_bytes FROM user_derived_usage "
                "WHERE user_id = ? AND subdir = ?",
                (row["id"], sub),
            )
            if cur is None or int(cur["size_bytes"]) != size:
                _upsert_derived_usage(row["id"], sub, size)
                updated += 1
    if updated:
        logger.info("Reconciled %d derived usage entries", updated)
    return updated


def refresh_derived_usage(user_id: str, subdir: str) -> int:
    """检查点增量刷新：仅扫描该用户该分类子目录并更新缓存行（v2 P4）。

    范围从"全部分类全盘扫描"缩到"被触达的单个目录"；返回刷新后的字节数。
    """
    size = _scan_derived_dir(user_id, subdir)
    _upsert_derived_usage(user_id, subdir, size)
    return size


def checkpoint_derived_usage(user_id: str, subdir: str) -> None:
    """会话/模板/绘图创建、会话删除、自助清理等触达某分类后的记账钩子。

    仅在多用户模式下生效；失败不阻断业务主流程（漂移由对账兜底）。"""
    if not userspace.is_multiuser():
        return
    try:
        refresh_derived_usage(user_id, subdir)
    except Exception as e:  # noqa: BLE001 - 记账失败不影响主流程
        logger.warning(
            "Derived usage checkpoint failed (user=%s subdir=%s): %s",
            user_id,
            subdir,
            e,
        )


def checkpoint_derived_usage_for_current_user(subdir: str) -> None:
    """按请求级当前用户执行检查点刷新（无上下文时静默跳过）。"""
    if not userspace.is_multiuser():
        return
    user_id = userspace.get_current_user_id()
    if not user_id:
        return
    checkpoint_derived_usage(user_id, subdir)


def resync_category_all_users(subdir: str) -> int:
    """某分类被后台 TTL 清理批量删除后，重扫所有用户该分类并刷新缓存。

    返回刷新的用户数；单用户模式返回 0（存量全局路径清理由对账覆盖口径外，
    本函数只服务多用户缓存）。"""
    if not userspace.is_multiuser():
        return 0
    count = 0
    for d in userspace.iterate_user_resource_roots(subdir):
        user_id = d.parent.name
        try:
            refresh_derived_usage(user_id, subdir)
        except ValueError:
            continue  # 目录名非安全 id（异常数据），跳过
        count += 1
    return count


def derived_usage_breakdown(user_id: str) -> dict[str, int]:
    """读取用户各分类衍生用量的缓存明细（category → bytes）。

    缓存表为空时先做一次全量引导（冷启动 / 迁移后首读），保证读数即准确；
    之后一律 O(分类数) 数据库查询，不再触盘。"""
    if _derived_cache_is_empty():
        logger.info("Derived usage cache empty; bootstrapping from disk scan")
        reconcile_derived_usage()
    rows = db_query_all(
        "SELECT subdir, size_bytes FROM user_derived_usage WHERE user_id = ?",
        (user_id,),
    )
    cached = {row["subdir"]: int(row["size_bytes"]) for row in rows}
    return {sub: cached.get(sub, 0) for sub in userspace.DERIVED_SUBDIRS}


def derived_usage_for_user(user_id: str) -> int:
    """返回用户衍生数据总用量（读 DB 缓存，O(1)；口径见 derived_usage_breakdown）。"""
    return sum(derived_usage_breakdown(user_id).values())


def user_usage(user_id: str) -> dict:
    """一次返回用户项目/衍生数据用量（用于 /api/auth/me 等）。

    v2 P4 起 usage 额外携带 derived_usage 分类明细，供前端展示分类用量条；
    明细只算一次，总量由同一份明细求和（避免重复查询与重复引导判定）。"""
    derived = derived_usage_breakdown(user_id)
    return {
        "project_usage_bytes": project_usage_for_user(user_id),
        "derived_usage_bytes": sum(derived.values()),
        "derived_usage": derived,
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


def _scan_derived_dir(user_id: str, subdir: str) -> int:
    """扫描用户单个衍生分类目录的实际磁盘占用（不存在返回 0）。"""
    d = userspace.derived_dir(user_id, subdir)
    if not d.is_dir():
        return 0
    return dir_size(d)


def _upsert_derived_usage(user_id: str, subdir: str, size_bytes: int) -> None:
    db_execute(
        "INSERT INTO user_derived_usage (user_id, subdir, size_bytes, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, subdir) DO UPDATE SET "
        "size_bytes = excluded.size_bytes, updated_at = excluded.updated_at",
        (user_id, subdir, int(size_bytes), utcnow()),
    )


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


def enforce_derived_quota(user_id: str, extra_bytes: int, subdir: str = "") -> None:
    """在计入 extra_bytes 后衍生数据未超限则通过，否则抛 413 quota_exceeded。

    v2 P4 起用量一律读 user_derived_usage 缓存（O(1)）。subdir 传入被触达
    的分类时，先对该用户该分类做一次检查点刷新（仅扫单个目录），保证创建
    前置校验的准确性不因缓存漂移而放松；未传则纯读缓存。"""
    if extra_bytes < 0:
        extra_bytes = 0
    if subdir:
        refresh_derived_usage(user_id, subdir)
    used = derived_usage_for_user(user_id)
    quota = effective_derived_quota(user_id)
    if used + extra_bytes > quota:
        raise _quota_exceeded(
            "Derived data quota exceeded: "
            f"need {used + extra_bytes} bytes but limit is {quota} bytes"
        )


def enforce_derived_quota_for_current_user(
    extra_bytes: int = 0, subdir: str = ""
) -> None:
    """多用户模式下按请求级当前用户执行衍生数据配额检查（超限抛 413）。

    供写作会话 / style_modify 会话 / 模板 / 绘图四类模块在会话或输出创建时
    调用（方案 §9.2“创建时检查”落地）。单用户模式或无当前用户上下文
    （session-only 等）时跳过，不设配额语义。

    extra_bytes 为本次新建产物的已知增量（如绘图数据文件大小）；会话类资源
    创建时无法预知最终体积，按 0 计——即用户衍生空间已满/超限时拒绝新建。
    用量口径与维护任务一致：user_derived_usage 缓存；subdir 传入时先刷新
    该分类缓存行（检查点增量扫描，v2 P4）。
    """
    if not userspace.is_multiuser():
        return
    user_id = userspace.get_current_user_id()
    if not user_id:
        return
    enforce_derived_quota(user_id, extra_bytes, subdir)


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
def cleanup_derived_category(user_id: str, category: str) -> dict[str, int]:
    """用户自助清理：删除该用户某分类衍生目录下的全部内容并即时刷新缓存。

    category 必须属于 DERIVED_SUBDIRS（未知分类抛 ValueError，由服务层映射
    400 invalid_category）；目录本身保留（后续创建仍落此处）。清理属低频
    用户动作，返回前对该用户全部分类做一次重扫，保证返回的用量明细整体
    准确（其余分类可能存在自上次对账以来的漂移）。"""
    if category not in userspace.DERIVED_SUBDIRS:
        raise ValueError(f"Unknown derived data category: {category}")
    d = userspace.derived_dir(user_id, category)
    if d.is_dir():
        for child in d.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as e:
                logger.warning("Self-service cleanup skipped %s: %s", child, e)
    resync_user_derived_usage(user_id)
    logger.info("Derived data self-cleanup: user=%s category=%s", user_id, category)
    return derived_usage_breakdown(user_id)


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
            # 级联清理反向补丁编辑日志（v2 P3：行 + .edit_logs 快照目录）
            from . import edit_log

            edit_log.purge_project_ops(row["id"])
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
    目录 mtime，进行中的上传不会被误删。
    分片上传会话目录（v2 P5，带 .upload_session 标记）不在本函数口径内：
    其生命周期由 upload_sessions 的 TTL 清理与孤儿清扫接管（TTL 可长于
    本保留期，mtime 兜底误删会破坏断点续传），此处延迟导入避免循环依赖。"""
    import time

    from . import upload_sessions as _upload_sessions

    staging = userspace.get_staging_root()
    if not staging.is_dir():
        return 0
    cutoff = time.time() - STAGING_RETENTION_SECONDS
    removed = 0
    for child in staging.iterdir():
        try:
            if not child.is_dir():
                continue
            if _upload_sessions.is_session_staging_dir(child):
                continue  # 分片会话目录：由专用 TTL 清理接管
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Cleaned %d leftover staging directories", removed)
    return removed


# 用户删除后的衍生数据归档保留天数（方案 §6.3：归档 30 天后清理）
ARCHIVE_RETENTION_DAYS = 30


def clean_expired_archives() -> int:
    """清理超过 ARCHIVE_RETENTION_DAYS 的用户归档目录（删除用户后的衍生数据）。"""
    import time

    archive = userspace.get_archive_root()
    if not archive.is_dir():
        return 0
    cutoff = time.time() - ARCHIVE_RETENTION_DAYS * 86400
    removed = 0
    for child in archive.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Removed %d expired user archives", removed)
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

    project_id = project_id or uuid.uuid4().hex
    now = utcnow()
    attempts = base_name
    # 预检与插入非原子（db 锁按语句粒度）：并发下预检结果可能过期而命中
    # 唯一索引。每次冲突后重取"当前最低可用名"再试——提交序保证最终名称
    # 连续（Same、Same (2)…），有界重试耗尽才判定失败。
    for _ in range(5):
        attempts = next_available_name(owner_id, base_name)
        try:
            db_execute(
                "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
                "deleted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
                (project_id, owner_id, attempts, size_bytes, now, now),
            )
            break
        except sqlite3.IntegrityError:
            if not retry_name:
                raise
            continue  # 并发冲突：重取名称重试（id 不变，仍与磁盘目录一致）
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_archive",
                "message": "Failed to allocate a unique project name",
            },
        )

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

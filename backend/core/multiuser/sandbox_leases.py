"""沙箱并发池租约（sandbox_slots 表，多用户模式，v2 方案 §5.4）。

事务内校验"全局活跃数 < max_concurrent_global 且该用户活跃数 <
max_concurrent_per_user"，成功插入带 TTL 的槽租约；执行结束删除。
崩溃残留租约由 TTL 过滤 + 维护循环 purge_expired() 兜底清理。

获取不到租约抛 RuntimeError(sandbox_code=sandbox_busy)，由路由层
映射为 429。P7 多实例阶段在同一共享卷上天然跨副本生效（BEGIN
IMMEDIATE 写锁串行化），无需修改本模块。
"""

import uuid

from backend.core.logger import get_logger
from backend.core.multiuser import db
from backend.utils.sandbox.errors import sandbox_busy

logger = get_logger("multiuser.sandbox_leases")

# 租约 TTL = 执行超时 + 该缓冲（进程崩溃后残留租约的最长滞留窗口）
LEASE_TTL_BUFFER_SECONDS = 120
# 租约最短 TTL（防止极短任务的租约立刻过期导致误超卖）
MIN_LEASE_TTL_SECONDS = 120


def purge_expired(now: str | None = None) -> int:
    """删除已过期租约，返回删除行数（维护循环 / 获取前惰性调用）。"""
    return db.db_execute(
        "DELETE FROM sandbox_slots WHERE expires_at <= ?",
        (now or db.utcnow(),),
    )


# 维护循环统一入口名（与 quota.clean_* 系列命名对齐）
purge_expired_sandbox_slots = purge_expired


def active_counts(user_id: str) -> dict[str, int]:
    """返回当前活跃租约数（全局 / 指定用户），供测试与运维观测。"""
    now = db.utcnow()
    global_active = db.db_count(
        "SELECT COUNT(*) FROM sandbox_slots WHERE expires_at > ?", (now,)
    )
    user_active = db.db_count(
        "SELECT COUNT(*) FROM sandbox_slots WHERE user_id = ? AND expires_at > ?",
        (user_id, now),
    )
    return {"global": global_active, "user": user_active}


def acquire(
    user_id: str,
    timeout_seconds: float,
    *,
    max_global: int,
    max_per_user: int,
) -> str:
    """原子获取一个并发槽，返回 slot_id；池满抛 sandbox_busy。

    BEGIN IMMEDIATE 在读-校验-写全程持有写锁：同机多副本（P7）下
    校验与插入也是原子的，不会超卖。
    """
    # 先做一次廉价清扫，避免过期行拉高 COUNT 造成误判 busy
    purge_expired()
    slot_id = uuid.uuid4().hex
    now = db.utcnow()
    ttl = max(int(timeout_seconds) + LEASE_TTL_BUFFER_SECONDS, MIN_LEASE_TTL_SECONDS)
    expires_at = db.utcnow_after(ttl)
    with db.transaction() as conn:
        global_active = conn.execute(
            "SELECT COUNT(*) FROM sandbox_slots WHERE expires_at > ?", (now,)
        ).fetchone()[0]
        user_active = conn.execute(
            "SELECT COUNT(*) FROM sandbox_slots WHERE user_id = ? AND expires_at > ?",
            (user_id, now),
        ).fetchone()[0]
        if global_active >= max_global or user_active >= max_per_user:
            raise sandbox_busy(
                f"sandbox concurrency pool full "
                f"(global {global_active}/{max_global}, "
                f"user {user_active}/{max_per_user})"
            )
        conn.execute(
            "INSERT INTO sandbox_slots (slot_id, user_id, acquired_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (slot_id, user_id, now, expires_at),
        )
    logger.debug(
        "sandbox slot acquired: user=%s slot=%s (global=%d/%d, user=%d/%d)",
        user_id,
        slot_id,
        global_active + 1,
        max_global,
        user_active + 1,
        max_per_user,
    )
    return slot_id


def release(slot_id: str) -> None:
    """释放租约（执行结束 / 异常路径 finally 调用；幂等）。"""
    removed = db.db_execute("DELETE FROM sandbox_slots WHERE slot_id = ?", (slot_id,))
    if removed == 0:
        logger.debug("sandbox slot already gone (expired?): %s", slot_id)

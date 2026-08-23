"""维护循环选主租约（maintenance_lease 表，多用户模式，v2 方案 §12.1）。

多副本部署时各副本的维护循环内容（配额对账、各类 TTL 清理、配额预警
检查等）本身幂等，但重复扫描浪费且拉高 SQLite 写锁竞争。本模块以
``key`` 粒度的租约选主：**持租约者才执行维护循环体**，租约到期自动易主，
副本崩溃后残留租约由过期时间兜底失效。

设计要点：
- 获取/续约是同一条条件 UPDATE（``status`` 无关，仅比较 expires_at），
  BEGIN IMMEDIATE 写锁保证跨副本原子，不会出现双主；
- 每 key 一行 upsert，表不增长，无需清理任务；
- 模块各自的 cleanup_loop（template/style_modify/plot 等）保持独立运行，
  不纳入选主（幂等无害，见方案 §12.1）。
"""

import socket
import uuid

from backend.core.logger import get_logger
from backend.core.multiuser import db

logger = get_logger("multiuser.maintenance_lease")

# 维护循环体使用的租约 key（当前仅此一个；未来其他独占任务可复用本表）
LEASE_KEY_MAINTENANCE = "maintenance_loop"


def make_holder() -> str:
    """构造租约持有者标识：hostname:pid:random——同机多进程与多副本均可区分。"""
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


def try_acquire(key: str, holder: str, ttl_seconds: int) -> bool:
    """尝试获取（或续约）租约：空闲或已过期时成功，否则 False。

    INSERT ... ON CONFLICT 仅在"现持有人就是自己（续约）"或"租约已过期
    （易主）"时更新，跨副本原子。
    """
    now = db.utcnow()
    expires_at = db.utcnow_after(int(ttl_seconds))
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT holder, expires_at FROM maintenance_lease WHERE key = ?", (key,)
        ).fetchone()
        if row is not None and row["holder"] != holder and row["expires_at"] > now:
            return False
        conn.execute(
            "INSERT INTO maintenance_lease (key, holder, expires_at) "
            "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "holder = excluded.holder, expires_at = excluded.expires_at",
            (key, holder, expires_at),
        )
    return True


def release(key: str, holder: str) -> bool:
    """释放租约（仅持有人可释放；幂等）。返回是否实际删除。"""
    removed = db.db_execute(
        "DELETE FROM maintenance_lease WHERE key = ? AND holder = ?", (key, holder)
    )
    return removed == 1


def current_holder(key: str) -> str | None:
    """返回租约当前持有人（未过期）；无有效租约返回 None。测试与观测用。"""
    row = db.db_query_one(
        "SELECT holder, expires_at FROM maintenance_lease WHERE key = ?", (key,)
    )
    if row is None or row["expires_at"] <= db.utcnow():
        return None
    return row["holder"]

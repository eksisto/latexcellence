"""逐段检查操作上下文外置（operation_contexts 表，v2 方案 §12.1）。

preview/check 阶段保存的 CheckOperationContext（marker 映射 + 文件 MD5）
原先存进程 dict，apply 请求打到另一副本时找不到上下文。本模块把上下文
payload（JSON）落库，使 apply 跨副本可取；进程内 dict 降级为无害的
L1 读缓存（命中免去一次查询，未命中回源重建）。

TTL 沿用原实现口径（1 小时），由维护循环 :func:`purge_expired` 清理；
读取路径对过期行惰性忽略。
"""

import json

from backend.core.logger import get_logger
from backend.core.multiuser import db

logger = get_logger("multiuser.operation_contexts")

# 上下文 TTL（秒）：与 base_paragraph_check 原进程内清理口径一致
OPERATION_CONTEXT_TTL_SECONDS = 3600


def store_context(operation_id: str, payload: dict) -> None:
    """写入/覆盖一条操作上下文（created_at 取当前时间）。"""
    db.db_execute(
        "INSERT INTO operation_contexts (operation_id, payload, created_at) "
        "VALUES (?, ?, ?) ON CONFLICT(operation_id) DO UPDATE SET "
        "payload = excluded.payload, created_at = excluded.created_at",
        (operation_id, db.json_dumps(payload), db.utcnow()),
    )


def load_context(operation_id: str) -> dict | None:
    """按 operation_id 读取上下文 payload；不存在或已过期返回 None。

    过期行在此惰性删除（维护循环之间的自愈路径）。
    """
    row = db.db_query_one(
        "SELECT payload, created_at FROM operation_contexts WHERE operation_id = ?",
        (operation_id,),
    )
    if row is None:
        return None
    if row["created_at"] <= db.utcnow_after(-OPERATION_CONTEXT_TTL_SECONDS):
        db.db_execute(
            "DELETE FROM operation_contexts WHERE operation_id = ?", (operation_id,)
        )
        return None
    return json.loads(row["payload"])


def delete_context(operation_id: str) -> None:
    """删除指定上下文（预留：当前 apply 不删，允许重复 apply 至 TTL 到期）。"""
    db.db_execute(
        "DELETE FROM operation_contexts WHERE operation_id = ?", (operation_id,)
    )


def purge_expired(
    ttl_seconds: int = OPERATION_CONTEXT_TTL_SECONDS,
    now: str | None = None,
) -> int:
    """删除超过 TTL 的上下文行，返回删除行数（维护循环入口）。"""
    cutoff = now or db.utcnow_after(-int(ttl_seconds))
    removed = db.db_execute(
        "DELETE FROM operation_contexts WHERE created_at <= ?", (cutoff,)
    )
    if removed:
        logger.debug("Purged %d expired operation contexts", removed)
    return removed

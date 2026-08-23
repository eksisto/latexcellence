"""跨副本待定交互存储（pending_interactions 表，多用户模式，v2 方案 §12.1）。

承载三类"请求 A 发起、请求 B（可能在另一副本）应答"的短生命周期状态：

- kind='confirm'：写作助手工具调用确认（edit_paragraph / undo_last_edit）；
- kind='clarify'：写作助手澄清请求；
- kind='ddg_challenge'：DDG 验证码数据（TTL 内可跨副本提交解答）。

单用户模式不建库：``available()`` 为 False 时调用方回落进程内
dict + asyncio.Event 的既有实现（行为不变）。多用户模式下 SSE 等待侧以
``PENDING_POLL_INTERVAL_SECONDS`` 短间隔轮询替代 Event；应答侧经
:func:`try_resolve` 做原子状态迁移（条件 UPDATE，先到先得），两副本竞争
同一待定项时只有一方成功。

status 取值：pending → confirmed / cancelled（confirm）、answered /
cancelled（clarify）；challenge 不使用 status（读时按 expires_at 过滤）。
过期行由 :func:`purge_expired`（维护循环）与各读取路径的惰性删除清理。
"""

import json

from backend.core.logger import get_logger
from backend.core.multiuser import db

logger = get_logger("multiuser.pending_interactions")

KIND_CONFIRM = "confirm"
KIND_CLARIFY = "clarify"
KIND_DDG_CHALLENGE = "ddg_challenge"

STATUS_PENDING = "pending"

# SSE 等待侧轮询间隔：人机交互（确认/澄清）亚秒级延迟无感，0.5s 在
# 延迟与 SQLite 查询开销之间取平衡（等待上限 120s ⇒ 至多 240 次轻量查询）
PENDING_POLL_INTERVAL_SECONDS = 0.5


def available() -> bool:
    """pending_interactions 表是否可用（多用户模式且库已初始化）。"""
    return db.is_initialized()


def create(
    interaction_id: str,
    kind: str,
    payload: dict,
    ttl_seconds: float,
) -> None:
    """登记一条待定交互（幂等覆盖同 id 旧记录）。"""
    db.db_execute(
        "INSERT INTO pending_interactions (id, kind, status, payload, expires_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, status='pending', "
        "payload=excluded.payload, expires_at=excluded.expires_at, "
        "created_at=excluded.created_at",
        (
            interaction_id,
            kind,
            STATUS_PENDING,
            db.json_dumps(payload),
            db.utcnow_after(int(ttl_seconds)),
            db.utcnow(),
        ),
    )


def try_resolve(
    interaction_id: str,
    kind: str,
    status: str,
    payload_update: dict | None = None,
) -> bool:
    """原子应答：仅当仍在 pending 且未过期时迁移状态，返回是否成功。

    条件 UPDATE 保证跨副本先到先得——两个副本同时 confirm 同一待定项时
    只有一个 rowcount=1，后到方返回 False（与单进程 Event.is_set 语义一致）。
    """
    sets = "status = ?"
    params: list = [status]
    if payload_update is not None:
        sets += ", payload = ?"
        params.append(db.json_dumps(payload_update))
    params.extend([interaction_id, kind, STATUS_PENDING, db.utcnow()])
    updated = db.db_execute(
        f"UPDATE pending_interactions SET {sets} "
        "WHERE id = ? AND kind = ? AND status = ? AND expires_at > ?",
        params,
    )
    return updated == 1


def fetch_resolved(interaction_id: str, kind: str) -> dict | None:
    """取走已决议的交互；仍 pending 返回 None，过期/不存在也返回 None。

    返回的 dict 含 payload 全字段与 ``status``（confirmed/cancelled/answered/
    expired）。已决议或已过期的行取走即删（一次性消费，防重复应答）。
    """
    row = db.db_query_one(
        "SELECT * FROM pending_interactions WHERE id = ? AND kind = ?",
        (interaction_id, kind),
    )
    if row is None:
        return None
    payload = json.loads(row["payload"])
    if row["status"] != STATUS_PENDING:
        db.db_execute("DELETE FROM pending_interactions WHERE id = ?", (row["id"],))
        payload["status"] = row["status"]
        return payload
    # 仍 pending：过期视为超时决议，惰性删除
    if row["expires_at"] <= db.utcnow():
        db.db_execute("DELETE FROM pending_interactions WHERE id = ?", (row["id"],))
        payload["status"] = "expired"
        return payload
    return None


def get(interaction_id: str, kind: str) -> dict | None:
    """只读窥探（challenge 校验用）：未过期返回 payload dict，否则 None。"""
    row = db.db_query_one(
        "SELECT payload FROM pending_interactions "
        "WHERE id = ? AND kind = ? AND status = ? AND expires_at > ?",
        (interaction_id, kind, STATUS_PENDING, db.utcnow()),
    )
    if row is None:
        return None
    return json.loads(row["payload"])


def delete(interaction_id: str, kind: str) -> None:
    """删除指定待定交互（challenge 提交完成 / 等待侧超时清理）。"""
    db.db_execute(
        "DELETE FROM pending_interactions WHERE id = ? AND kind = ?",
        (interaction_id, kind),
    )


def purge_expired(now: str | None = None) -> int:
    """删除全部过期行（维护循环入口；kind 无关）。"""
    removed = db.db_execute(
        "DELETE FROM pending_interactions WHERE expires_at <= ?",
        (now or db.utcnow(),),
    )
    if removed:
        logger.debug("Purged %d expired pending interactions", removed)
    return removed

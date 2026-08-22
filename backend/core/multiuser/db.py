"""
多用户模式 SQLite 数据库基础设施。

职责：
    1. 负责数据库文件的初始化（表结构、索引、WAL、外键约束）
    2. 提供进程内线程安全的连接管理与统一的同步查询入口
    3. 版本迁移机制（PRAGMA user_version + 有序幂等迁移步骤，v2 P0 引入）
    4. 供服务层 / 中间件 / CLI 调用；事件循环线程内调用方须用
       asyncio.to_thread 调度（禁止阻塞事件循环）

约束（与方案 P1 一致）：
    - v1 固定单进程（uvicorn workers=1），SQLite WAL 模式足以支撑
    - 数据库路径、用户空间根、暂存区根均在 init_db() 时从 get_config()
      实时读取，不在模块级缓存路径
    - 所有"按用户/项目拼接路径"的代码走 userspace.py 的统一入口
"""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger("multiuser.db")

# 数据库模式：用户、会话、项目、协作者。
# projects / project_collaborators 为后续阶段（P2/P4）预留，P1 一并建表。
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    email               TEXT NOT NULL DEFAULT '',
    role                TEXT NOT NULL DEFAULT 'user',
    status              TEXT NOT NULL DEFAULT 'pending',
    project_quota_bytes INTEGER,
    derived_quota_bytes INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users (username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    remember   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires_at ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'active',
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- 同名项目约束：同一 owner 的活动项目中名称唯一（回收站不计入）
CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_owner_active
    ON projects (owner_id, name) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS project_collaborators (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
"""


def _migration_v1_baseline(conn: sqlite3.Connection) -> None:
    """v1 基线：业务主表。全部 CREATE IF NOT EXISTS，对存量库幂等。"""
    conn.executescript(SCHEMA)


def _migration_v2_sandbox_slots(conn: sqlite3.Connection) -> None:
    """v2 P1a：沙箱并发池租约表（崩溃残留租约由 TTL 清理兜底）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sandbox_slots (
            slot_id     TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_sandbox_slots_expires
            ON sandbox_slots (expires_at);
        CREATE INDEX IF NOT EXISTS ix_sandbox_slots_user
            ON sandbox_slots (user_id);
        """)


def _migration_v3_llm_governance(conn: sqlite3.Connection) -> None:
    """v2 P1b：LLM 每用户治理表（滑动窗口事件 + 并发租约，方案 §6）。

    rate_limit_events 为登录限流（kind='login'）与 LLM 限流（kind='llm'）
    共用的滑动窗口事件表；崩溃残留租约由 TTL 过滤 + 维护循环清理兜底。
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rate_limit_events (
            kind     TEXT NOT NULL,
            identity TEXT NOT NULL,
            ts       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rate_limit_events_window
            ON rate_limit_events (kind, identity, ts);
        CREATE TABLE IF NOT EXISTS llm_concurrency_leases (
            task_id     TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_llm_concurrency_leases_expires
            ON llm_concurrency_leases (expires_at);
        CREATE INDEX IF NOT EXISTS ix_llm_concurrency_leases_user
            ON llm_concurrency_leases (user_id);
        """)


def _migration_v4_workspace_edit_ops(conn: sqlite3.Connection) -> None:
    """v2 P3：反向补丁操作索引表（方案 §8.1）。

    一次 apply_change / apply_whole_files 成功写入记一条 op；同一事务内的
    多文件写共享同一 op_group_id（一次撤销回滚整组）。before/after 全文快照
    存磁盘（<users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/），
    本表只做操作索引；owner_id 冗余存储用于定位快照目录（转交后归属不变）。
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workspace_edit_ops (
            seq           INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id         TEXT NOT NULL UNIQUE,
            op_group_id   TEXT NOT NULL,
            project_id    TEXT NOT NULL,
            owner_id      TEXT NOT NULL DEFAULT '',
            actor_user_id TEXT NOT NULL,
            workspace_id  TEXT NOT NULL DEFAULT '',
            source_module TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'active',
            non_undoable  INTEGER NOT NULL DEFAULT 0,
            file_count    INTEGER NOT NULL DEFAULT 0,
            total_bytes   INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            undone_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_edit_ops_stack
            ON workspace_edit_ops (project_id, actor_user_id, status, seq);
        CREATE INDEX IF NOT EXISTS ix_edit_ops_group
            ON workspace_edit_ops (op_group_id);
        """)


# 当前 schema 最新版本号。新增迁移时：追加实现函数并在列表末尾登记
# (target_version, description, fn)，同时递增此常量；每个步骤必须幂等可重入。
SCHEMA_VERSION = 4

# 有序迁移步骤：(目标版本, 描述, 执行函数)。版本从 2 起（1 为 v1 基线，
# 对老库存量数据无破坏性变更，仅补齐 IF NOT EXISTS 建表）。
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (
        1,
        "v1 baseline tables (users/sessions/projects/project_collaborators)",
        _migration_v1_baseline,
    ),
    (2, "v2 P1a sandbox concurrency lease table", _migration_v2_sandbox_slots),
    (
        3,
        "v2 P1b LLM governance tables (rate limit events + concurrency leases)",
        _migration_v3_llm_governance,
    ),
    (
        4,
        "v2 P3 reverse-patch edit log index table",
        _migration_v4_workspace_edit_ops,
    ),
]


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def read_user_version(conn: sqlite3.Connection) -> int:
    """读取当前 schema 版本（PRAGMA user_version）。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """把数据库按序推进到 SCHEMA_VERSION，返回本次实际执行的版本列表。

    每个步骤在独立事务中执行并立即推进版本号，保证任一步骤失败后
    重启可以从中断处继续（步骤自身幂等）。
    """
    current = read_user_version(conn)
    applied: list[int] = []
    for target, description, fn in MIGRATIONS:
        if current >= target:
            continue
        logger.info("Applying DB migration v%d: %s", target, description)
        fn(conn)
        _set_user_version(conn, target)
        conn.commit()
        current = target
        applied.append(target)
    return applied


# 进程内共享连接。单进程 + WAL + RLock 串行化事务即可保证线程安全；
# 数据库文件较小，查询均很短，无需逐请求开新连接。
_conn: sqlite3.Connection | None = None
_db_path: Path | None = None
_lock = threading.RLock()
_initialized = False


def utcnow() -> str:
    """当前 UTC 时间（ISO 8601，秒级）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utcnow_after(seconds: int) -> str:
    """当前 UTC 时间加 seconds 秒（ISO 8601，秒级），用于租约/TTL 过期时间。"""
    return (
        datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)
    ).isoformat()


def _resolve_db_path() -> Path:
    """实时读取配置，返回数据库路径（相对路径相对于当前工作目录解析）。"""
    cfg = get_config()
    return Path(cfg.multi_user.db_path)


def init_db() -> None:
    """
    初始化数据库：创建父目录、连接并执行版本迁移、开启 WAL，并确保
    用户空间 / 暂存区根目录存在。可重复调用（幂等）。
    """
    global _conn, _db_path, _initialized
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise RuntimeError("init_db() requires multi_user.enabled = true")

    db_path = _resolve_db_path().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 同时确保多用户目录布局就绪（暂存区；用户子目录在注册时创建）
    Path(cfg.multi_user.users_root).mkdir(parents=True, exist_ok=True)
    Path(cfg.multi_user.staging_root).mkdir(parents=True, exist_ok=True)

    with _lock:
        # 已连接且指向其他路径（例如测试切换配置）时重开连接
        if _conn is not None and _db_path != db_path:
            _close_conn_unlocked()
        if _conn is None:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            _conn = conn
            _db_path = db_path
        applied = apply_migrations(_conn)
        if applied:
            logger.info(
                "Multi-user database migrated to schema v%s (applied: %s)",
                read_user_version(_conn),
                applied,
            )
        _initialized = True
    logger.info("Multi-user database initialized at %s", db_path)


def is_initialized() -> bool:
    """数据库是否已在本进程完成初始化（供沙箱租约等模块前置判断）。"""
    return _initialized


def schema_status() -> dict:
    """返回 schema 迁移状态（db-status CLI 使用）。须先 init_db()。"""
    with _lock:
        conn = _require_conn()
        current = read_user_version(conn)
        return {
            "user_version": current,
            "latest_version": SCHEMA_VERSION,
            "up_to_date": current >= SCHEMA_VERSION,
            "pending": [
                {"version": target, "description": description}
                for target, description, _fn in MIGRATIONS
                if current < target
            ],
        }


def _close_conn_unlocked() -> None:
    """关闭连接并重置进程内状态（调用方必须已持有 _lock）。"""
    global _conn, _db_path, _initialized
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
    _conn = None
    _db_path = None
    _initialized = False


def close_db() -> None:
    """关闭数据库连接并重置进程内状态（测试 / 关闭时调用）。"""
    with _lock:
        _close_conn_unlocked()


def _require_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _conn


def db_execute(sql: str, params: tuple | list = ()) -> int:
    """执行写语句并提交，返回受影响行数。线程安全（RLock 串行化）。"""
    with _lock:
        conn = _require_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def db_executemany(sql: str, seq_of_params: list[tuple]) -> int:
    """批量执行写语句并提交。"""
    with _lock:
        conn = _require_conn()
        cur = conn.executemany(sql, seq_of_params)
        conn.commit()
        return cur.rowcount


def db_query_one(sql: str, params: tuple | list = ()) -> dict | None:
    """查询单行，返回 dict，无结果返回 None。"""
    with _lock:
        conn = _require_conn()
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


def db_query_all(sql: str, params: tuple | list = ()) -> list[dict]:
    """查询多行，返回 dict 列表。"""
    with _lock:
        conn = _require_conn()
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def db_count(sql: str, params: tuple | list = ()) -> int:
    """执行 COUNT/标量查询，返回首个标量。"""
    with _lock:
        conn = _require_conn()
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return int(row[0]) if row is not None else 0


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """显式事务上下文：持有全局锁并 BEGIN IMMEDIATE。

    成功提交、异常回滚。供"读-校验-写"必须原子完成的多语句场景
    （如沙箱并发租约）使用；单条写语句直接用 db_execute 即可。
    """
    with _lock:
        conn = _require_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

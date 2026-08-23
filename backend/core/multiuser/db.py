"""
多用户模式 SQLite 数据库基础设施。

职责：
    1. 负责数据库文件的初始化（表结构、索引、WAL、外键约束）
    2. 提供进程内线程安全的连接管理与统一的同步查询入口
    3. 版本迁移机制（PRAGMA user_version + 有序幂等迁移步骤，v2 P0 引入；
       v2 P7 起以 <db>.migration.lock 的 flock 做跨进程串行化，
       每个步骤包在独立事务中并在事务内重读前置条件）
    4. 供服务层 / 中间件 / CLI 调用；事件循环线程内调用方须用
       asyncio.to_thread 调度（禁止阻塞事件循环）

约束（与方案 P1 一致）：
    - v1 固定单进程（uvicorn workers=1），SQLite WAL 模式足以支撑
    - 数据库路径、用户空间根、暂存区根均在 init_db() 时从 get_config()
      实时读取，不在模块级缓存路径
    - 所有"按用户/项目拼接路径"的代码走 userspace.py 的统一入口
"""

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger("multiuser.db")

try:  # 平台分叉：flock 为 POSIX 能力，Windows 缺失 fcntl 时迁移锁降级直通
    import fcntl

    _FLOCK_AVAILABLE = True
except ImportError:  # pragma: no cover - 仅 Windows 触发
    _FLOCK_AVAILABLE = False

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


def _execute_schema_script(conn: sqlite3.Connection, script: str) -> None:
    """在调用方当前事务内逐条执行受控 schema 脚本（勿用 conn.executescript）。

    executescript 会先隐式提交挂起事务，把 apply_migrations 的"单步骤单
    事务"拦腰截断；本函数改用 sqlite3.complete_statement() 按语句边界拆分
    （正确处理引号、``--`` 注释与触发器 BEGIN...END），再逐条
    conn.execute，使语句全部加入调用方已开启的事务。仅限本项目受控 DDL
    脚本（语句均以分号结尾）使用。
    """
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statement = buf.strip()
            if statement:
                conn.execute(statement)
            buf = ""
    if buf.strip():
        raise ValueError("schema script ends with an incomplete statement")


def _migration_v1_baseline(conn: sqlite3.Connection) -> None:
    """v1 基线：业务主表。全部 CREATE IF NOT EXISTS，对存量库幂等。"""
    _execute_schema_script(conn, SCHEMA)


def _migration_v2_sandbox_slots(conn: sqlite3.Connection) -> None:
    """v2 P1a：沙箱并发池租约表（崩溃残留租约由 TTL 清理兜底）。"""
    _execute_schema_script(
        conn,
        """
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
        """,
    )


def _migration_v3_llm_governance(conn: sqlite3.Connection) -> None:
    """v2 P1b：LLM 每用户治理表（滑动窗口事件 + 并发租约，方案 §6）。

    rate_limit_events 为登录限流（kind='login'）与 LLM 限流（kind='llm'）
    共用的滑动窗口事件表；崩溃残留租约由 TTL 过滤 + 维护循环清理兜底。
    """
    _execute_schema_script(
        conn,
        """
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
        """,
    )


def _migration_v4_workspace_edit_ops(conn: sqlite3.Connection) -> None:
    """v2 P3：反向补丁操作索引表（方案 §8.1）。

    一次 apply_change / apply_whole_files 成功写入记一条 op；同一事务内的
    多文件写共享同一 op_group_id（一次撤销回滚整组）。before/after 全文快照
    存磁盘（<users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/），
    本表只做操作索引；owner_id 冗余存储用于定位快照目录（转交后归属不变）。
    """
    _execute_schema_script(
        conn,
        """
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
        """,
    )


def _migration_v5_user_derived_usage(conn: sqlite3.Connection) -> None:
    """v2 P4：衍生数据用量缓存表（方案 §9.1）。

    按 (user_id, subdir) 缓存四类衍生目录（writing_chat_sessions /
    style_modify_sessions / templates / plots）的磁盘占用，使配额校验与
    /api/auth/me 用量读取为 O(1) 数据库查询；准确性由"检查点增量刷新
    （触达某分类时仅重扫该目录）+ 每小时对账全量重算"两级机制维护。
    行随用户删除级联清理。
    """
    _execute_schema_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS user_derived_usage (
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subdir     TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, subdir)
        );
        CREATE INDEX IF NOT EXISTS ix_user_derived_usage_user
            ON user_derived_usage (user_id);
        """,
    )


def _migration_v6_upload_sessions(conn: sqlite3.Connection) -> None:
    """v2 P5：大文件分片上传会话表（方案 §10）。

    manifest 列存 JSON（chunk_size + 逐文件 path/size/md5/chunks 清单）；
    status 取值 uploading / assembling（complete 期间的条件态，防止并发
    拼装）；TTL 以 last_activity_at（每次分片/状态查询续期）为基准，由
    维护循环清理过期行与对应 staging 目录。
    """
    _execute_schema_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS upload_sessions (
            id               TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name             TEXT NOT NULL,
            kind             TEXT NOT NULL,
            total_bytes      INTEGER NOT NULL DEFAULT 0,
            file_count       INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'uploading',
            manifest         TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            last_activity_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_upload_sessions_user
            ON upload_sessions (user_id);
        CREATE INDEX IF NOT EXISTS ix_upload_sessions_activity
            ON upload_sessions (last_activity_at);
        """,
    )


def _migration_v7_mail_and_alerts(conn: sqlite3.Connection) -> None:
    """v2 P6：邮件队列 + 自助找回令牌 + 配额预警迟滞状态表。

    email_outbox 为异步发送队列（业务方投递即返回，发送协程消费）；
    password_reset_tokens 只存令牌 SHA-256（与会话同口径），TTL 1 小时单次有效；
    user_alert_state 以 (user_id, kind) 记录配额预警迟滞标记与聚合去抖时间戳。
    users.email 部分唯一索引不在本迁移内创建——存量重复邮箱时不自动改数据，
    由 ensure_email_unique_index() 在每次启动时检测并补建（见其 docstring）。
    """
    _execute_schema_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS email_outbox (
            id          TEXT PRIMARY KEY,
            recipient   TEXT NOT NULL,
            subject     TEXT NOT NULL,
            body_text   TEXT NOT NULL,
            body_html   TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'queued',
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT,
            created_at  TEXT NOT NULL,
            sent_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_email_outbox_status
            ON email_outbox (status, created_at);
        CREATE INDEX IF NOT EXISTS ix_email_outbox_sent_at
            ON email_outbox (sent_at);
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at    TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user
            ON password_reset_tokens (user_id);
        CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_expires
            ON password_reset_tokens (expires_at);
        CREATE TABLE IF NOT EXISTS user_alert_state (
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind       TEXT NOT NULL,
            sent_at    TEXT NOT NULL,
            PRIMARY KEY (user_id, kind)
        );
        """,
    )


def _migration_v8_instance_state(conn: sqlite3.Connection) -> None:
    """v2 P7：多实例状态外置表（方案 §12.1）。

    - workspace_bindings：工作区 → (user, project, root) 绑定的权威存储，
      任一副本可按绑定重建进程内 LatexProject 缓存；
    - operation_contexts：逐段检查 CheckOperationContext 外置（payload JSON），
      preview 与 apply 可落在不同副本；TTL 1h 由维护循环清理；
    - pending_interactions：写作助手工具确认 / 澄清与 DDG challenge 的跨副本
      待定交互（status 列承载原子状态迁移，避免依赖 JSON1 表达式）；短 TTL，
      由维护循环清理；
    - maintenance_lease：维护循环选主租约（key 粒度，持租约者执行循环体，
      到期自动易主；每 key 一行 upsert，无需额外清理）；
    - email_outbox.claimed_at：发送协程按消息原子认领（queued→sending）的
      认领时间戳，供维护循环回收"副本崩溃残留的 sending 行"（多实例下
      防止双副本重复投递同一封邮件）。ALTER 无 IF NOT EXISTS，按
      pragma table_info 幂等补列；列检查与 ALTER 同处 apply_migrations
      的步骤事务（BEGIN IMMEDIATE）内，"查列→补列"整体原子。
    """
    _execute_schema_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS workspace_bindings (
            workspace_id   TEXT PRIMARY KEY,
            user_id        TEXT,
            project_id     TEXT,
            owner_id       TEXT,
            root_path      TEXT NOT NULL DEFAULT '',
            last_active_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_workspace_bindings_project
            ON workspace_bindings (project_id);
        CREATE TABLE IF NOT EXISTS operation_contexts (
            operation_id TEXT PRIMARY KEY,
            payload      TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_operation_contexts_created
            ON operation_contexts (created_at);
        CREATE TABLE IF NOT EXISTS pending_interactions (
            id         TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'pending',
            payload    TEXT NOT NULL DEFAULT '{}',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_pending_interactions_expires
            ON pending_interactions (expires_at);
        CREATE TABLE IF NOT EXISTS maintenance_lease (
            key        TEXT PRIMARY KEY,
            holder     TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """,
    )
    # claimed_at 补列（历史缺陷点）：本段由 apply_migrations 包在
    # BEGIN IMMEDIATE 内执行——写锁持有期间重读 table_info 再决定是否
    # ALTER，"查列→补列"原子化，即使外部跨进程锁失效也不会因并发迁移
    # 撞出 duplicate column；直接调用（绕过迁移框架）时各语句自提交，
    # 该前置检查仍保证单连接视角的幂等。
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(email_outbox)").fetchall()
    }
    if "claimed_at" not in cols:
        conn.execute("ALTER TABLE email_outbox ADD COLUMN claimed_at TEXT")


# 邮箱部分唯一索引名（ensure_email_unique_index 创建）。
USERS_EMAIL_UNIQUE_INDEX = "ux_users_email_unique"


def find_duplicate_emails(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """返回 users 表中重复（大小写不敏感）的非空邮箱分组及冲突用户名列表。"""
    return conn.execute(
        "SELECT LOWER(email) AS email, COUNT(*) AS n, "
        "GROUP_CONCAT(username, ', ') AS usernames "
        "FROM users WHERE email != '' "
        "GROUP BY LOWER(email) HAVING COUNT(*) > 1 ORDER BY email"
    ).fetchall()


def ensure_email_unique_index(conn: sqlite3.Connection) -> bool:
    """确保 users.email 部分唯一索引存在（v2 P6，每次 init_db 调用）。

    存量库存在重复邮箱时：告警并列出冲突行后跳过建索引（不自动改数据，
    由管理员人工处理），下次重启自动重试；无冲突则幂等补建索引。
    返回本次调用后索引是否就位。"""
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (USERS_EMAIL_UNIQUE_INDEX,),
    ).fetchone()
    if existing:
        return True
    dups = find_duplicate_emails(conn)
    if dups:
        details = "; ".join(
            f"{row['email']} x{row['n']} ({row['usernames']})" for row in dups
        )
        logger.warning(
            "Duplicate user emails detected, skipping partial unique index %s — "
            "resolve manually then restart to retry: %s",
            USERS_EMAIL_UNIQUE_INDEX,
            details,
        )
        return False
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {USERS_EMAIL_UNIQUE_INDEX} "
        "ON users (email COLLATE NOCASE) WHERE email != ''"
    )
    conn.commit()
    logger.info(
        "Partial unique index %s ensured on users.email", USERS_EMAIL_UNIQUE_INDEX
    )
    return True


# 当前 schema 最新版本号。新增迁移时：追加实现函数并在列表末尾登记
# (target_version, description, fn)，同时递增此常量；每个步骤必须幂等可重入。
SCHEMA_VERSION = 8

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
    (
        5,
        "v2 P4 derived data usage cache table",
        _migration_v5_user_derived_usage,
    ),
    (
        6,
        "v2 P5 chunked upload session table",
        _migration_v6_upload_sessions,
    ),
    (
        7,
        "v2 P6 mail outbox + password reset tokens + user alert state tables",
        _migration_v7_mail_and_alerts,
    ),
    (
        8,
        "v2 P7 instance-state externalization tables (workspace bindings / "
        "operation contexts / pending interactions / maintenance lease) "
        "+ email_outbox.claimed_at",
        _migration_v8_instance_state,
    ),
]


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def read_user_version(conn: sqlite3.Connection) -> int:
    """读取当前 schema 版本（PRAGMA user_version）。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


# ── 跨进程迁移串行化（v2 P7 并发初始化缺陷修复） ─────────────────


def _main_db_path(conn: sqlite3.Connection) -> Path | None:
    """返回连接主库的文件路径；内存库等无文件形态返回 None。

    行结构 (seq, name, file)：name='main' 即主库；``file`` 为空或
    ':memory:' 时无磁盘文件可加锁。
    """
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            file_name = str(row[2] or "")
            if not file_name or file_name == ":memory:":
                return None
            return Path(file_name)
    return None


def _migration_lock_path(db_path: Path) -> Path:
    """返回数据库迁移锁文件路径：`<db 文件>.migration.lock`（与库同目录）。"""
    return db_path.parent / f"{db_path.name}.migration.lock"


@contextmanager
def _cross_process_migration_lock(db_path: Path) -> Iterator[None]:
    """跨进程迁移互斥：对 `<db>.migration.lock` 取 flock 独占锁直至退出。

    多副本共享同一 SQLite 库同时冷启动时，"读 user_version → 执行步骤 →
    写回版本号"的序列若不跨进程串行化，会实证性撞出两类崩溃：v8 非原子
    补列的 duplicate column，与 WAL 转换抢独占锁失败的 database is locked。
    采用阻塞式加锁而非轮询：flock 在持有方进程退出/描述符关闭时由内核
    自动释放，不存在残留锁卡死；临界区仅为秒级迁移本身。

    锁文件常驻不删：他人持旧 inode 期间 unlink 后，新进程会创建不同
    inode 的同名文件并成功加锁，反而失去互斥（与
    backend/utils/session_mutex.py 记录的是同一陷阱）；孤儿锁文件无副作用。

    Windows 无 fcntl 时降级为直通并告警：多实例部署目标平台是 Linux
    （deployment.md），单机 Windows 开发场景退回修复前的进程内行为。
    """
    if not _FLOCK_AVAILABLE:  # pragma: no cover - 仅 Windows 触发
        logger.warning(
            "fcntl unavailable on this platform; DB migration lock degrades "
            "to in-process-only serialization"
        )
        yield
        return
    lock_path = _migration_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # 阻塞等待；EINTR 由解释器自动重试
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _is_busy_error(exc: sqlite3.Error) -> bool:
    """是否 SQLITE_BUSY 系错误（扩展结果码低字节即主码 SQLITE_BUSY）。"""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return int(code) & 0xFF == sqlite3.SQLITE_BUSY
    return "database is locked" in str(exc).lower()


# journal_mode=WAL 遇 SQLITE_BUSY 的重试参数：短退避若干次后放行。
WAL_SET_MAX_RETRIES = 5
WAL_SET_RETRY_DELAY_SECONDS = 0.2


def _set_wal_mode_tolerant(conn: sqlite3.Connection, db_path: Path) -> None:
    """开启 WAL；并发初始化抢占独占锁（SQLITE_BUSY）时短重试后放行。

    WAL 转换需要独占锁且 busy_timeout 对该语句无效：两进程同时对同一库
    冷启动、各自尝试 DELETE→WAL 转换时必有一方失败。重试数轮仍忙则放行
    不抛——对方大概率已完成转换（存量运行库通常早已是 WAL），告警留痕供
    排查长期未生效的异常情形；不得让 init_db 因该语句崩溃。
    """
    for attempt in range(1, WAL_SET_MAX_RETRIES + 1):
        try:
            conn.execute("PRAGMA journal_mode=WAL").fetchone()
            return
        except sqlite3.OperationalError as exc:
            if not _is_busy_error(exc):
                raise
            logger.debug(
                "journal_mode=WAL busy on %s (attempt %d/%d): %s",
                db_path,
                attempt,
                WAL_SET_MAX_RETRIES,
                exc,
            )
            time.sleep(WAL_SET_RETRY_DELAY_SECONDS)
    logger.warning(
        "Could not set journal_mode=WAL on %s after %d attempts (database "
        "busy); continuing without WAL — the concurrent initializer likely "
        "enabled it already",
        db_path,
        WAL_SET_MAX_RETRIES,
    )


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """把数据库按序推进到 SCHEMA_VERSION，返回本次实际执行的版本列表。

    跨进程串行化：推进全程持有 `<db 文件>.migration.lock` 的 flock 独占
    锁，多副本共享同一库文件并发冷启动时防止迁移交错（内存库等无文件
    形态跳过加锁）。

    每个步骤在独立事务（BEGIN IMMEDIATE）中执行并立即推进版本号，保证
    任一步骤失败后重启可以从中断处继续（步骤自身幂等）；进入事务后首先
    重读 user_version 作为纵深防御——即使外部锁失效（如降级平台），也不
    会重复执行已完成的步骤。注意迁移函数内部不得使用 conn.executescript()
    （其隐式提交会截断步骤事务），统一经 _execute_schema_script() 执行。
    """
    db_path = _main_db_path(conn)
    lock_ctx = (
        _cross_process_migration_lock(db_path) if db_path is not None else nullcontext()
    )
    with lock_ctx:
        current = read_user_version(conn)
        applied: list[int] = []
        for target, description, fn in MIGRATIONS:
            if current >= target:
                continue
            logger.info("Applying DB migration v%d: %s", target, description)
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 纵深防御：写锁内重读版本号，锁失效也不重复执行已完成步骤
                if read_user_version(conn) >= target:
                    conn.rollback()
                    current = read_user_version(conn)
                    continue
                fn(conn)
                _set_user_version(conn, target)
            except BaseException:
                conn.rollback()
                raise
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


def json_dumps(payload: dict) -> str:
    """dict → 紧凑 JSON 文本（payload 列统一序列化口径；禁用 ASCII 转义）。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
            # busy_timeout 先设：覆盖后续普通语句的锁等待；对 WAL 转换本身
            # 无效（需独占锁），由 _set_wal_mode_tolerant 单独容忍 BUSY
            conn.execute("PRAGMA busy_timeout=5000")
            _set_wal_mode_tolerant(conn, db_path)
            conn.execute("PRAGMA foreign_keys=ON")
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
        # 邮箱部分唯一索引（v2 P6）：每次启动幂等补建——存量重复邮箱时跳过
        # 并告警，管理员人工处理后下次重启自动就位（不阻塞启动与迁移）
        ensure_email_unique_index(_conn)
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

"""多用户模式 P1：SQLite 初始化与 schema 完整性测试。"""

import logging
import sqlite3
import threading

import pytest

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module


def _table_names():
    rows = db_module.db_query_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [r["name"] for r in rows]


class TestInitDb:
    def test_tables_created(self, mu_env):
        tables = _table_names()
        for name in (
            "users",
            "sessions",
            "projects",
            "project_collaborators",
        ):
            assert name in tables, f"table {name} missing"

    def test_wal_mode_enabled(self, mu_env):
        row = db_module.db_query_one("PRAGMA journal_mode")
        assert row["journal_mode"] == "wal"

    def test_foreign_keys_enabled(self, mu_env):
        row = db_module.db_query_one("PRAGMA foreign_keys")
        assert row["foreign_keys"] == 1

    def test_init_idempotent(self, mu_env):
        db_module.init_db()  # 二次调用不应报错
        tables = _table_names()
        assert "users" in tables

    def test_dirs_created(self, mu_env):
        assert mu_env["users_root"].is_dir()
        assert mu_env["staging_root"].is_dir()

    def test_user_dirs_created_on_register(self, mu_env):
        user = auth_core.create_user("alice", "password-123")
        root = mu_env["users_root"] / user["id"]
        assert (root / "projects").is_dir()
        for sub in (
            "writing_chat_sessions",
            "style_modify_sessions",
            "templates",
            "plots",
        ):
            assert (root / sub).is_dir()


class TestConstraints:
    def test_username_unique_case_insensitive(self, mu_env):
        auth_core.create_user("Alice", "password-123")
        with pytest.raises(ValueError):
            auth_core.create_user("alice", "other-pass-1")

    def test_username_unique_chinese(self, mu_env):
        auth_core.create_user("小明", "password-123")
        with pytest.raises(ValueError):
            auth_core.create_user("小明", "other-pass-1")

    def test_foreign_key_cascade_session(self, mu_env):
        user = auth_core.create_user("bob", "password-123")
        token = auth_core.create_session(user["id"])
        # 删除用户应级联删除会话
        db_module.db_execute("DELETE FROM users WHERE id = ?", (user["id"],))
        assert auth_core.validate_session(token) is None

    def test_projects_unique_index_exists(self, mu_env):
        indexes = db_module.db_query_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("ux_projects_owner_active",),
        )
        assert len(indexes) == 1


class TestSchemaMigrations:
    """v2 P0 版本迁移框架：版本推进、幂等性与存量库升级。"""

    def test_fresh_db_reaches_latest_version(self, mu_env):
        status = db_module.schema_status()
        assert status["user_version"] == db_module.SCHEMA_VERSION
        assert status["up_to_date"] is True
        assert status["pending"] == []

    def test_sandbox_slots_table_exists(self, mu_env):
        tables = _table_names()
        assert "sandbox_slots" in tables

    def test_users_last_login_at_column_exists(self, mu_env):
        """v10：users.last_login_at 审计列就位（新库与存量库升级后一致）。"""
        cols = {
            row["name"] for row in db_module.db_query_all("PRAGMA table_info(users)")
        }
        assert "last_login_at" in cols

    def test_init_idempotent_keeps_version(self, mu_env):
        db_module.init_db()
        db_module.init_db()
        assert db_module.schema_status()["user_version"] == (db_module.SCHEMA_VERSION)

    def test_legacy_v1_db_upgrades_without_data_loss(self, tmp_path):
        """存量 v1 库（有表但 user_version=0）升级到最新版且数据保留。"""
        import sqlite3
        import json as _json
        import os as _os
        from unittest import mock as _mock

        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import (
            MINIMAL_CONFIG,
            _reset_config_state,
        )

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(db_module.SCHEMA)
        conn.execute(
            "INSERT INTO users (id, username, password_hash, email, role, "
            "status, created_at, updated_at) VALUES "
            "('u-legacy', 'legacy', 'hash', '', 'admin', 'active', "
            "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        data = dict(MINIMAL_CONFIG)
        data["multi_user"] = {
            "enabled": True,
            "db_path": str(db_path),
            "users_root": str(tmp_path / "users") + "/",
            "staging_root": str(tmp_path / "staging") + "/",
        }
        config_path = tmp_path / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            _json.dump(data, f)
        with _mock.patch.dict(_os.environ, {}, clear=True):
            cfg_module.load_config(str(config_path))
        try:
            db_module.init_db()
            status = db_module.schema_status()
            assert status["user_version"] == db_module.SCHEMA_VERSION
            # 存量数据完好 + 新表就位
            user = db_module.db_query_one("SELECT * FROM users WHERE id = 'u-legacy'")
            assert user is not None and user["username"] == "legacy"
            assert "sandbox_slots" in _table_names()
        finally:
            db_module.close_db()
            _reset_config_state()


# ── 并发初始化回归（v2 P7 缺陷修复：跨进程迁移锁 + 步骤事务原子性） ──


def _build_v7_seed_db(db_path) -> None:
    """构造 user_version=7 的存量库：表结构齐备、未做 v8 迁移。

    用于把并发竞争窗口聚焦在最脆弱的 v8 补列上（ALTER 无 IF NOT EXISTS，
    历史上"先查列再 ALTER"非原子，双进程并发约一成概率 duplicate column）。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        for target, _description, fn in db_module.MIGRATIONS:
            if target > 7:
                break
            fn(conn)
            conn.execute(f"PRAGMA user_version = {int(target)}")
        conn.commit()
    finally:
        conn.close()


def _email_outbox_columns(db_path) -> list[str]:
    """独立连接读取 email_outbox 列名（不受模块内共享连接影响）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(email_outbox)")]
    finally:
        conn.close()


def _spawn_migration_threads(db_path, n_threads: int) -> list[BaseException]:
    """n 个线程各持独立 sqlite3 连接、屏障同步后并发执行全量迁移。

    模拟多副本冷启动：线程间无共享连接（各自 connect 同一库文件），
    迁移框架内部的跨进程锁是唯一的串行化手段。返回收集到的异常列表。
    """
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            conn = sqlite3.connect(str(db_path), timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=15000")
            barrier.wait(timeout=15)
            db_module.apply_migrations(conn)
            conn.close()
        except BaseException as exc:  # 统一收集后断言，避免线程异常被吞
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, name=f"migrate-{i}") for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "migration thread did not finish (deadlock?)"
    return errors


class TestConcurrentMigration:
    """并发初始化回归：跨进程迁移锁（flock）+ 单步骤事务原子性。

    原缺陷（双进程实证，约一成概率）：两进程并发对同一库跑 init_db() 时，
    v8 的"PRAGMA table_info 检查 → ALTER 补列"非原子，先后执行即报
    duplicate column name: claimed_at；或双方同抢 WAL 转换独占锁报
    database is locked——副本 lifespan 崩溃进 restart 循环。

    线程级无法逐字节复刻双进程时序，本组用例一方面以"独立连接 + 屏障 +
    多轮次"逼近竞态（任一轮出现异常 / 版本漂移 / 列重复即判负），另一方
    面确定性锁定修复行为本身：迁移锁文件生成且真实互斥、v8 补列幂等重入、
    WAL 设置容忍 SQLITE_BUSY。
    """

    def test_concurrent_full_migration_consistent(self, tmp_path):
        """多轮 × 多线程独立连接并发跑全量迁移：无异常且终态一致。"""
        for round_index in range(4):
            db_path = tmp_path / f"race-{round_index}.db"
            if round_index > 0:
                # 第 1 轮空库走全链路；其余轮预置 v7 存量库，让全部线程
                # 一齐挤向最脆弱的 v8 补列步骤
                _build_v7_seed_db(db_path)
            errors = _spawn_migration_threads(db_path, n_threads=6)
            assert errors == [], f"round {round_index}: {errors!r}"
            conn = sqlite3.connect(str(db_path))
            try:
                assert db_module.read_user_version(conn) == db_module.SCHEMA_VERSION
            finally:
                conn.close()
            assert _email_outbox_columns(db_path).count("claimed_at") == 1
            # 迁移锁文件就位（与库同目录、<db>.migration.lock 命名）
            lock_path = tmp_path / f"race-{round_index}.db.migration.lock"
            assert lock_path.exists(), f"missing migration lock {lock_path}"

    def test_migration_lock_blocks_peer(self, tmp_path):
        """他方持锁期间迁移阻塞等待、释放后方才执行（锁定互斥行为本身）。

        线程级难以稳定复现原竞态，故直接验证新语义：外部进程持有
        migration.lock 时 apply_migrations 不得抢跑。
        """
        import fcntl  # POSIX 能力；多实例部署目标平台即 Linux
        import os

        db_path = tmp_path / "serial.db"
        _build_v7_seed_db(db_path)
        lock_fd = os.open(
            str(tmp_path / "serial.db.migration.lock"),
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # 模拟另一进程正在迁移
            errors: list[BaseException] = []
            finished = threading.Event()

            def worker() -> None:
                try:
                    conn = sqlite3.connect(str(db_path), timeout=15)
                    db_module.apply_migrations(conn)
                    conn.close()
                except BaseException as exc:  # 统一收集后断言
                    errors.append(exc)
                finally:
                    finished.set()

            t = threading.Thread(target=worker, name="blocked-migrator")
            t.start()
            # 持锁期间对方不得完成迁移（flock 未生效则此处立即翻车）
            assert not finished.wait(
                timeout=1.0
            ), f"apply_migrations ignored external migration lock: {errors!r}"
            fcntl.flock(lock_fd, fcntl.LOCK_UN)  # 释放 → 对方放行
            t.join(timeout=15)
            assert not t.is_alive()
            assert errors == []
        finally:
            os.close(lock_fd)
        conn = sqlite3.connect(str(db_path))
        try:
            assert db_module.read_user_version(conn) == db_module.SCHEMA_VERSION
        finally:
            conn.close()

    def test_v8_claimed_at_column_reentry(self, mu_env):
        """v8 补列幂等重入：列已存在而版本未推进的半途态不重复 ALTER。

        复刻旧缺陷崩溃现场——补列成功但版本号未来得及写回（回退
        user_version 至 7）——后重放 v8，断言不抛 duplicate column 且列
        不重复；即使跨进程锁整体失效，事务内的 table_info 重读也能兜底。
        """
        conn = db_module._require_conn()
        conn.execute("PRAGMA user_version = 7")  # 版本回退，制造半途态
        conn.commit()
        # 直接重放 v8 步骤函数：建表部分 IF NOT EXISTS 幂等，
        # 补列检查命中已有列后跳过 ALTER
        db_module._migration_v8_instance_state(conn)
        cols = [
            row[1] for row in conn.execute("PRAGMA table_info(email_outbox)").fetchall()
        ]
        assert cols.count("claimed_at") == 1
        # 迁移框架视角：版本回退后重放 v8 起的所有待执行步骤推进到最新版，
        # 且列不重复
        assert db_module.apply_migrations(conn) == list(
            range(8, db_module.SCHEMA_VERSION + 1)
        )
        assert db_module.read_user_version(conn) == db_module.SCHEMA_VERSION

    def test_init_db_threaded_repeat_no_deadlock(self, mu_env):
        """多线程反复完整 init_db：flock 循环获取/释放不卡死、版本稳定。"""
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(5):
                    db_module.init_db()
            except BaseException as exc:  # 统一收集后断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, name=f"init-{i}") for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive(), "init_db thread did not finish (deadlock?)"
        assert errors == []
        assert db_module.schema_status()["up_to_date"] is True

    def test_wal_setting_tolerates_busy(self, tmp_path, caplog):
        """journal_mode=WAL 遇 SQLITE_BUSY：重试耗尽后告警放行，不抛异常。

        另一连接以未提交写事务占据写锁时，WAL 转换必得 BUSY（该语句不受
        busy_timeout 保护）；修复后 init_db 不再因此崩溃。
        """
        busy_path = tmp_path / "busy.db"
        blocker = sqlite3.connect(str(busy_path))
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("CREATE TABLE hold (x INTEGER)")  # 未提交，占据写锁
        victim = sqlite3.connect(str(busy_path), timeout=0.01)  # BUSY 立即暴露
        try:
            with caplog.at_level(logging.WARNING, logger="multiuser.db"):
                # 不应抛 sqlite3.OperationalError: database is locked
                db_module._set_wal_mode_tolerant(victim, busy_path)
        finally:
            blocker.close()
            victim.close()
        assert "journal_mode=WAL" in caplog.text

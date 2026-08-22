"""多用户模式 P1：SQLite 初始化与 schema 完整性测试。"""

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

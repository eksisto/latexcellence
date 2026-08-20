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

"""多用户模式 P3：配额记账核心测试。

覆盖内容：
    - 有效额度解析（用户覆盖优先于全局默认）
    - 项目 / 衍生数据用量统计
    - 配额前置校验（超限抛 413 quota_exceeded）
    - 项目唯一命名（xx (2)）与建行
    - 对账（reconcile_project_sizes 修正 size 缓存漂移）
    - 回收站过期清理
"""

import uuid

import pytest
from fastapi import HTTPException

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import quota
from backend.core.multiuser import userspace
from tests.test_multiuser.conftest import build_mu_env, _reset_config_state


@pytest.fixture
def env(tmp_path):
    env = build_mu_env(tmp_path)
    env["user"] = auth_core.create_user(
        "u1", "password-123", role="user", status="active"
    )
    yield env
    db_module.close_db()
    _reset_config_state()


def _add_project(env, user_id: str, name: str, files: dict[str, str]) -> str:
    project_id = uuid.uuid4().hex
    root = userspace.project_dir(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    size = sum((root / r).stat().st_size for r in files)
    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (project_id, user_id, name, size, now, now),
    )
    return project_id


class TestEffectiveQuota:
    def test_uses_global_default(self, env):
        assert quota.effective_project_quota(env["user"]["id"]) == 500 * 1024 * 1024
        assert quota.effective_derived_quota(env["user"]["id"]) == 100 * 1024 * 1024

    def test_user_override_wins(self, env):
        auth_core.set_user_quota(env["user"]["id"], 2048, 4096)
        assert quota.effective_project_quota(env["user"]["id"]) == 2048
        assert quota.effective_derived_quota(env["user"]["id"]) == 4096


class TestUsage:
    def test_project_usage_sums_active_only(self, env):
        _add_project(env, env["user"]["id"], "A", {"a.tex": "hello"})  # 5 字节
        _add_project(env, env["user"]["id"], "B", {"b.tex": "two"})  # 3 字节
        # 入回收站的项目不计入用量
        pid = _add_project(env, env["user"]["id"], "C", {"c.tex": "xxxx"})  # 4 字节
        db_module.db_execute(
            "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
            (db_module.utcnow(), pid),
        )
        assert quota.project_usage_for_user(env["user"]["id"]) == 8

    def test_derived_usage_counts_subdirs(self, env):
        (userspace.derived_dir(env["user"]["id"], "plots") / "p.txt").write_text(
            "12345", encoding="utf-8"
        )
        (userspace.derived_dir(env["user"]["id"], "templates") / "t.txt").write_text(
            "ab", encoding="utf-8"
        )
        assert quota.derived_usage_for_user(env["user"]["id"]) == 7

    def test_user_usage_shape(self, env):
        usage = quota.user_usage(env["user"]["id"])
        assert usage == {"project_usage_bytes": 0, "derived_usage_bytes": 0}


class TestEnforce:
    def test_enforce_project_quota_ok(self, env):
        auth_core.set_user_quota(env["user"]["id"], 100, None)
        quota.enforce_project_quota(env["user"]["id"], 50)  # 不抛

    def test_enforce_project_quota_exceeded(self, env):
        auth_core.set_user_quota(env["user"]["id"], 100, None)
        with pytest.raises(HTTPException) as ei:
            quota.enforce_project_quota(env["user"]["id"], 150)
        assert ei.value.status_code == 413
        assert ei.value.detail["error"] == "quota_exceeded"

    def test_enforce_derived_quota_exceeded(self, env):
        auth_core.set_user_quota(env["user"]["id"], None, 10)
        with pytest.raises(HTTPException) as ei:
            quota.enforce_derived_quota(env["user"]["id"], 20)
        assert ei.value.status_code == 413


class TestProjectRow:
    def test_create_project_row_and_unique_name(self, env):
        row1 = quota.create_project_row(env["user"]["id"], "Doc", 10)
        assert row1["name"] == "Doc"
        row2 = quota.create_project_row(env["user"]["id"], "Doc", 10)
        assert row2["name"] == "Doc (2)"
        # 回收站中的同名不占位：把 row1 移入回收站后，新项目可复用 "Doc"
        db_module.db_execute(
            "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
            (db_module.utcnow(), row1["id"]),
        )
        row3 = quota.create_project_row(env["user"]["id"], "Doc", 10)
        assert row3["name"] == "Doc"

    def test_next_available_name(self, env):
        quota.create_project_row(env["user"]["id"], "P", 1)
        assert quota.next_available_name(env["user"]["id"], "P") == "P (2)"


class TestReconcile:
    def test_reconcile_fixes_stale_size(self, env):
        pid = _add_project(env, env["user"]["id"], "A", {"a.tex": "hello"})
        # 手动漂移：把 size_bytes 改成 9999
        db_module.db_execute("UPDATE projects SET size_bytes=9999 WHERE id=?", (pid,))
        updated = quota.reconcile_project_sizes()
        assert updated >= 1
        row = db_module.db_query_one(
            "SELECT size_bytes FROM projects WHERE id=?", (pid,)
        )
        assert row["size_bytes"] == 5


class TestRecycleBinCleanup:
    def test_clean_expired_recycle_bin(self, env):
        pid = _add_project(env, env["user"]["id"], "Old", {"a.tex": "x"})
        # 置于 30 天前
        from datetime import datetime, timedelta, timezone

        old = (
            (datetime.now(timezone.utc) - timedelta(days=30))
            .replace(microsecond=0)
            .isoformat()
        )
        db_module.db_execute(
            "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
            (old, pid),
        )
        removed = quota.clean_expired_recycle_bin()
        assert removed == 1
        assert (
            db_module.db_query_one("SELECT * FROM projects WHERE id=?", (pid,)) is None
        )
        assert not userspace.project_dir(env["user"]["id"], pid).exists()

    def test_recent_recycle_bin_kept(self, env):
        pid = _add_project(env, env["user"]["id"], "Recent", {"a.tex": "x"})
        db_module.db_execute(
            "UPDATE projects SET status='deleted', deleted_at=? WHERE id=?",
            (db_module.utcnow(), pid),
        )
        assert quota.clean_expired_recycle_bin() == 0
        assert (
            db_module.db_query_one("SELECT * FROM projects WHERE id=?", (pid,))
            is not None
        )

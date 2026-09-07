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
from backend.core.multiuser import quota, userspace
from tests.test_multiuser.conftest import _reset_config_state, build_mu_env


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
        # v2 P4 起用量读 DB 缓存：触达分类后由检查点刷新缓存行
        quota.refresh_derived_usage(env["user"]["id"], "plots")
        quota.refresh_derived_usage(env["user"]["id"], "templates")
        assert quota.derived_usage_for_user(env["user"]["id"]) == 7

    def test_user_usage_shape(self, env):
        usage = quota.user_usage(env["user"]["id"])
        assert usage == {
            "project_usage_bytes": 0,
            "derived_usage_bytes": 0,
            # v2 P4：分类明细（category → bytes）
            "derived_usage": {
                "writing_chat_sessions": 0,
                "style_modify_sessions": 0,
                "templates": 0,
                "plots": 0,
            },
        }


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

    # ── R1：derived quota 创建时检查（多用户模式 + 请求级当前用户上下文）──

    def test_enforce_derived_for_current_user_over_quota(self, env):
        """多用户 + 请求级上下文：用量已满时拒绝创建（超限抛 413）。

        v2 P4：传入 subdir 时先对该分类做检查点增量刷新再校验，
        磁盘上的既有占用在检查时即时可见。"""
        auth_core.set_user_quota(env["user"]["id"], None, 10)
        (userspace.derived_dir(env["user"]["id"], "plots") / "p.bin").write_bytes(
            b"x" * 20
        )
        userspace.set_current_user_id(env["user"]["id"])
        try:
            with pytest.raises(HTTPException) as ei:
                quota.enforce_derived_quota_for_current_user(subdir="plots")
            assert ei.value.status_code == 413
            assert ei.value.detail["error"] == "quota_exceeded"
        finally:
            userspace.set_current_user_id(None)

    def test_enforce_derived_for_current_user_within_limit(self, env):
        auth_core.set_user_quota(env["user"]["id"], None, 1000)
        userspace.set_current_user_id(env["user"]["id"])
        try:
            quota.enforce_derived_quota_for_current_user()  # 不抛
        finally:
            userspace.set_current_user_id(None)

    def test_enforce_derived_for_current_user_no_context_skipped(self, env):
        """无当前用户上下文（session-only / 未登录路径）→ 跳过检查。"""
        auth_core.set_user_quota(env["user"]["id"], None, 10)
        (userspace.derived_dir(env["user"]["id"], "plots") / "p.bin").write_bytes(
            b"y" * 20
        )
        userspace.set_current_user_id(None)
        quota.enforce_derived_quota_for_current_user()  # 不抛

    def test_enforce_derived_for_current_user_single_user_mode(self, tmp_path):
        """单用户模式（无配额语义）→ 无条件跳过。"""
        build_mu_env(tmp_path, enabled=False)
        userspace.set_current_user_id("any-id")
        try:
            quota.enforce_derived_quota_for_current_user(10**9)  # 不抛
        finally:
            userspace.set_current_user_id(None)
            _reset_config_state()


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

    # ── R4：xx (2) 命名并发 / 唯一索引重试路径 ─────────────────────────

    def test_concurrent_create_project_row_unique_names(self, env):
        """真并发（多线程）调用 create_project_row：全部成功且名称唯一。"""
        import threading

        owner_id = env["user"]["id"]
        results: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                row = quota.create_project_row(owner_id, "Same", 1)
                with lock:
                    results.append(row["name"])
            except BaseException as e:  # noqa: BLE001
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent creation failed: {errors}"
        assert len(results) == 8
        assert len(set(results)) == 8, f"duplicate names: {sorted(results)}"
        # 名称应为 Same / Same (2) … Same (8)（顺序无关，仅校验集合）
        expected = {"Same", *(f"Same ({i})" for i in range(2, 9))}
        assert set(results) == expected

    def test_create_project_row_integrity_retry_path(self, env, monkeypatch):
        """唯一索引冲突（并发窗口中的过期预检）→ 重试分支生成 xx (2)。"""
        owner_id = env["user"]["id"]
        quota.create_project_row(owner_id, "Doc", 1)  # 已存在 "Doc"
        real_next = quota.next_available_name
        calls = {"n": 0}

        def fake_next(owner: str, base: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return "Doc"  # 模拟过期预检：命中唯一索引
            return real_next(owner, base)

        monkeypatch.setattr(quota, "next_available_name", fake_next)
        row = quota.create_project_row(owner_id, "Doc", 1)
        assert calls["n"] == 2  # 预检冲突 → 重试取新名
        assert row["name"] == "Doc (2)"


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

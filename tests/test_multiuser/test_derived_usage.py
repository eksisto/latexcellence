"""多用户衍生数据记账与细粒度清理测试（v2 P4，方案 §9）。

覆盖内容：
    - 迁移 v5：user_derived_usage 表与 schema 版本
    - 缓存一致性：写/删/清后检查点刷新读数即时正确；冷启动引导
    - 检查点增量范围：只扫描被触达的单个分类目录（mock 断言扫描路径）
    - 对账纠偏：人为制造缓存漂移后 reconcile_derived_usage 修复
    - 并发触达竞态：检查点与对账并发交错无异常、静止后收敛（方案 §9.3）
    - 配额边界行为不变：纯缓存读 O(1)；带 subdir 的前置刷新保持严格性
    - 自助清理 API：只能清自己、未知分类 400、即时刷新缓存、/me 明细
    - 存量迁移触达：migrate-legacy 批量写入后缓存即时刷新 / dry_run 不动
    - sessions 过期会话删除（并入维护循环）
    - writing_chat_retention_days：0 不删（仅统计）、>0 清理过期会话并刷新缓存
    - 7z 解压监视线程采样计数器：与 dir_size 同口径 + 超限短路
"""

import os
import threading
import time
from unittest import mock

import pytest

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import quota, userspace
from backend.modules.writing_chat import cleanup as wc_cleanup
from tests.test_multiuser.conftest import (
    _reset_config_state,
    build_mu_env,
    csrf,
)

PASSWORD = "password-123"


def _retention_ctx(days: int):
    """构造替换 writing_chat_retention_days 的配置上下文管理器。"""
    real = get_config()
    mu = real.multi_user.model_copy(update={"writing_chat_retention_days": days})
    cfg = real.model_copy(update={"multi_user": mu})
    return mock.patch.object(wc_cleanup, "get_config", return_value=cfg)


@pytest.fixture
def env(tmp_path):
    env = build_mu_env(tmp_path)
    env["user"] = auth_core.create_user("u1", PASSWORD, role="user", status="active")
    yield env
    db_module.close_db()
    _reset_config_state()


def _seed(env, user_id: str, subdir: str, files: dict[str, bytes]) -> int:
    """在某分类目录写入文件，返回写入总字节数。"""
    d = userspace.derived_dir(user_id, subdir)
    d.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, content in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        total += len(content)
    return total


# ── 迁移 v5 ──────────────────────────────────────────────────────
class TestMigrationV5:
    def test_schema_version_and_table(self, env):
        assert db_module.schema_status()["user_version"] == 5
        row = db_module.db_query_one(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='user_derived_usage'"
        )
        assert row is not None


# ── 缓存一致性 ───────────────────────────────────────────────────
class TestCacheConsistency:
    def _cache_row_count(self) -> int:
        return db_module.db_count("SELECT COUNT(*) FROM user_derived_usage")

    def test_bootstrap_on_first_read(self, env):
        """缓存表为空时首次读取触发全量引导，读数即准确。"""
        user_id = env["user"]["id"]
        _seed(env, user_id, "plots", {"a.bin": b"12345"})
        # 引导前表为空
        assert self._cache_row_count() == 0
        assert quota.derived_usage_breakdown(user_id)["plots"] == 5
        # 引导后所有用户 × 分类都有缓存行（含零值行）
        assert self._cache_row_count() == len(userspace.DERIVED_SUBDIRS)

    def test_write_then_refresh_reads_correct(self, env):
        user_id = env["user"]["id"]
        assert quota.derived_usage_breakdown(user_id)["templates"] == 0
        _seed(env, user_id, "templates", {"t.tex": b"abcde"})
        # 未刷新前缓存仍是旧值（O(1) 读不触盘）
        assert quota.derived_usage_breakdown(user_id)["templates"] == 0
        # 检查点刷新后即时正确
        assert quota.refresh_derived_usage(user_id, "templates") == 5
        assert quota.derived_usage_breakdown(user_id)["templates"] == 5
        assert quota.derived_usage_for_user(user_id) == 5

    def test_delete_then_refresh_reads_correct(self, env):
        user_id = env["user"]["id"]
        p = userspace.derived_dir(user_id, "templates") / "t.tex"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"xyz")
        quota.refresh_derived_usage(user_id, "templates")
        assert quota.derived_usage_breakdown(user_id)["templates"] == 3
        p.unlink()
        quota.refresh_derived_usage(user_id, "templates")
        assert quota.derived_usage_breakdown(user_id)["templates"] == 0

    def test_checkpoint_scans_only_touched_subdir(self, env, monkeypatch):
        """检查点只扫被触达的单个目录，不做全分类扫描。"""
        user_id = env["user"]["id"]
        for sub in ("writing_chat_sessions", "style_modify_sessions"):
            _seed(env, user_id, sub, {"x.bin": b"data"})
        scanned: list[str] = []
        real_dir_size = quota.dir_size

        def spy_dir_size(path):
            scanned.append(str(path))
            return real_dir_size(path)

        monkeypatch.setattr(quota, "dir_size", spy_dir_size)
        quota.checkpoint_derived_usage(user_id, "templates")
        touched_root = str(userspace.derived_dir(user_id, "templates"))
        assert len(scanned) == 1
        assert scanned[0] == touched_root

    def test_checkpoint_ignores_single_user_mode(self, tmp_path):
        """单用户模式无记账语义：检查点为无害空操作。"""
        build_mu_env(tmp_path, enabled=False)
        try:
            quota.checkpoint_derived_usage("some-user", "templates")
            assert quota.resync_category_all_users("templates") == 0
        finally:
            _reset_config_state()


# ── 对账纠偏 ─────────────────────────────────────────────────────
class TestReconcileDerivedUsage:
    def test_reconcile_fixes_drift(self, env):
        user_id = env["user"]["id"]
        _seed(env, user_id, "plots", {"p.bin": b"123"})
        quota.refresh_derived_usage(user_id, "plots")
        # 人为制造漂移
        db_module.db_execute(
            "UPDATE user_derived_usage SET size_bytes = 9999 "
            "WHERE user_id = ? AND subdir = 'plots'",
            (user_id,),
        )
        updated = quota.reconcile_derived_usage()
        assert updated >= 1
        assert quota.derived_usage_breakdown(user_id)["plots"] == 3

    def test_reconcile_bootstraps_missing_rows(self, env):
        """对账同时承担冷启动引导：无缓存行时补齐全部分类行。"""
        user_id = env["user"]["id"]
        _seed(env, user_id, "plots", {"p.bin": b"12345"})
        assert quota.reconcile_derived_usage() >= 1
        breakdown = quota.derived_usage_breakdown(user_id)
        assert breakdown["plots"] == 5
        assert set(breakdown) == set(userspace.DERIVED_SUBDIRS)

    def test_resync_category_all_users(self, env):
        u2 = auth_core.create_user("u2", PASSWORD, role="user", status="active")
        _seed(env, env["user"]["id"], "templates", {"a": b"x"})
        _seed(env, u2["id"], "templates", {"b": b"yy"})
        n = quota.resync_category_all_users("templates")
        assert n == 2
        assert quota.derived_usage_breakdown(env["user"]["id"])["templates"] == 1
        assert quota.derived_usage_breakdown(u2["id"])["templates"] == 2


# ── 并发触达竞态（方案 §9.3）────────────────────────────────────
class TestConcurrentTouchpoints:
    def test_checkpoint_and_reconcile_interleave_safely(self, env):
        """检查点刷新与全量对账并发交错时不抛错、不丢行，静止后收敛。

        竞态窗口内的瞬时偏移由设计容忍（对账兜底）；本测试锁定两条底线：
        并发 upsert 在进程内 DB 锁串行化下无异常、表结构不被破坏；
        磁盘写入静止后的最终读数与 dir_size 口径一致。"""
        user_id = env["user"]["id"]
        d = userspace.derived_dir(user_id, "plots")
        d.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        errors: list[Exception] = []

        def mutate():
            i = 0
            while not stop.is_set():
                try:
                    (d / f"m{i % 6}.bin").write_bytes(b"x" * ((i % 4) + 1))
                    i += 1
                except OSError:
                    return

        def checkpoint_loop():
            while not stop.is_set():
                try:
                    quota.checkpoint_derived_usage(user_id, "plots")
                except Exception as e:  # noqa: BLE001 - 捕获即用例失败
                    errors.append(e)
                    return

        def reconcile_loop():
            while not stop.is_set():
                try:
                    quota.reconcile_derived_usage()
                except Exception as e:  # noqa: BLE001 - 捕获即用例失败
                    errors.append(e)
                    return

        threads = [
            threading.Thread(target=mutate),
            threading.Thread(target=checkpoint_loop),
            threading.Thread(target=reconcile_loop),
        ]
        for th in threads:
            th.start()
        time.sleep(0.2)
        stop.set()
        for th in threads:
            th.join(timeout=10)
            assert not th.is_alive(), "worker thread did not stop"
        assert errors == []
        # 表未被并发写坏：仍恰为 用户数 × 分类数 行（upsert 不产生重复行）
        n_users = db_module.db_count("SELECT COUNT(*) FROM users")
        assert db_module.db_count("SELECT COUNT(*) FROM user_derived_usage") == (
            n_users * len(userspace.DERIVED_SUBDIRS)
        )
        # 静止后一次检查点刷新即收敛到真实磁盘占用
        quota.checkpoint_derived_usage(user_id, "plots")
        truth = sum(
            quota.dir_size(userspace.derived_dir(user_id, sub))
            for sub in userspace.DERIVED_SUBDIRS
        )
        assert quota.derived_usage_for_user(user_id) == truth


# ── 存量迁移触达 ─────────────────────────────────────────────────
class TestLegacyMigrationCacheRefresh:
    def test_migrate_legacy_refreshes_cache(self, env, monkeypatch):
        """migrate-legacy 向管理员衍生目录批量写入后即时刷新用量缓存。

        回归场景：缓存表已有数据行（首读引导不会触发）时执行迁移，
        迁移量必须立即可见，而不是等下一轮维护循环对账才可见——否则该窗口
        内衍生配额校验读到偏小旧值，可能被静默超出。"""
        from backend.core.multiuser import migrate as migrate_core

        user_id = env["user"]["id"]
        # 预热：让缓存表非空（引导只发生在全空时）
        quota.derived_usage_breakdown(user_id)
        assert db_module.db_count("SELECT COUNT(*) FROM user_derived_usage") > 0

        legacy = env["tmp_path"] / "legacy"
        wc = legacy / "writing_chat_sessions"
        wc.mkdir(parents=True)
        (wc / "a.json").write_text("{}", encoding="utf-8")
        tpl = legacy / "templates"
        tpl.mkdir(parents=True)
        (tpl / "t.tex").write_bytes(b"x" * 7)

        monkeypatch.setattr(
            migrate_core,
            "legacy_source_dirs",
            lambda cfg: {"writing_chat_sessions": wc, "templates": tpl},
        )
        report = migrate_core.migrate_legacy(user_id)
        assert report["total_copied"] == 2
        breakdown = quota.derived_usage_breakdown(user_id)
        assert breakdown["writing_chat_sessions"] == 2
        assert breakdown["templates"] == 7

    def test_migrate_legacy_dry_run_keeps_cache(self, env, monkeypatch):
        """dry_run 只统计：不复制文件，也不改动用量缓存。"""
        from backend.core.multiuser import migrate as migrate_core

        user_id = env["user"]["id"]
        quota.derived_usage_breakdown(user_id)
        src = env["tmp_path"] / "legacy" / "templates"
        src.mkdir(parents=True)
        (src / "t.tex").write_bytes(b"x" * 7)
        monkeypatch.setattr(
            migrate_core,
            "legacy_source_dirs",
            lambda cfg: {"templates": src},
        )
        report = migrate_core.migrate_legacy(user_id, dry_run=True)
        assert report["total_copied"] == 1  # 仅统计
        assert not (userspace.derived_dir(user_id, "templates") / "t.tex").exists()
        assert quota.derived_usage_breakdown(user_id)["templates"] == 0


# ── 配额边界行为不变 ─────────────────────────────────────────────
class TestEnforceWithCache:
    def test_pure_cache_read_is_stale_tolerant(self, env):
        """不带 subdir 的检查纯读缓存（O(1)，引导后磁盘新增占用不可见）。"""
        user_id = env["user"]["id"]
        auth_core.set_user_quota(user_id, None, 10)
        quota.enforce_derived_quota(user_id, 0)  # 首读触发引导（全 0）
        _seed(env, user_id, "plots", {"big.bin": b"x" * 50})
        # 缓存未刷新：检查不触盘，仍通过
        quota.enforce_derived_quota(user_id, 0)

    def test_subdir_refresh_keeps_boundary_strict(self, env):
        """带 subdir 的检查先做检查点刷新：超限照旧抛 413。"""
        user_id = env["user"]["id"]
        auth_core.set_user_quota(user_id, None, 10)
        _seed(env, user_id, "plots", {"big.bin": b"x" * 50})
        with pytest.raises(Exception) as ei:
            quota.enforce_derived_quota(user_id, 0, subdir="plots")
        assert getattr(ei.value, "status_code", None) == 413
        assert ei.value.detail["error"] == "quota_exceeded"

    def test_extra_bytes_on_top_of_cache(self, env):
        user_id = env["user"]["id"]
        auth_core.set_user_quota(user_id, None, 100)
        _seed(env, user_id, "plots", {"p.bin": b"x" * 60})
        quota.refresh_derived_usage(user_id, "plots")
        quota.enforce_derived_quota(user_id, 30)  # 60 + 30 ≤ 100：通过
        with pytest.raises(Exception) as ei:
            quota.enforce_derived_quota(user_id, 50)  # 60 + 50 > 100
        assert getattr(ei.value, "status_code", None) == 413


# ── 自助清理核心 ─────────────────────────────────────────────────
class TestCleanupDerivedCategory:
    def test_cleanup_removes_contents_and_refreshes(self, env):
        user_id = env["user"]["id"]
        _seed(env, user_id, "writing_chat_sessions", {"s.json": b"{}"})
        _seed(env, user_id, "plots", {"p.bin": b"plot-data"})
        result = quota.cleanup_derived_category(user_id, "writing_chat_sessions")
        d = userspace.derived_dir(user_id, "writing_chat_sessions")
        assert d.is_dir() and list(d.iterdir()) == []
        assert result["writing_chat_sessions"] == 0
        assert result["plots"] == 9

    def test_cleanup_unknown_category_raises(self, env):
        with pytest.raises(ValueError):
            quota.cleanup_derived_category(env["user"]["id"], "../projects")

    def test_cleanup_only_own_directory(self, env):
        u2 = auth_core.create_user("u2", PASSWORD, role="user", status="active")
        _seed(env, env["user"]["id"], "templates", {"mine": b"a"})
        _seed(env, u2["id"], "templates", {"theirs": b"b"})
        quota.cleanup_derived_category(env["user"]["id"], "templates")
        assert list(userspace.derived_dir(u2["id"], "templates").iterdir()) != []


# ── 自助清理 API ─────────────────────────────────────────────────
class TestCleanupApi:
    @pytest.fixture
    def api_env(self, mu_env):
        """两个活跃用户 + 各自的衍生数据。"""
        alice = auth_core.create_user("alice", PASSWORD, role="user", status="active")
        bob = auth_core.create_user("bob", PASSWORD, role="user", status="active")
        _seed(mu_env, alice["id"], "templates", {"t.tex": b"abc"})
        _seed(mu_env, alice["id"], "plots", {"p.bin": b"1234"})
        _seed(mu_env, bob["id"], "templates", {"b.tex": b"xyz"})
        return {"alice": alice, "bob": bob}

    @staticmethod
    def _login(client, username: str) -> None:
        r = client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text

    def test_cleanup_requires_login(self, mu_client):
        r = mu_client.post(
            "/api/auth/cleanup-derived-data", json={"category": "templates"}
        )
        assert r.status_code == 401

    def test_cleanup_own_category(self, mu_client, api_env):
        self._login(mu_client, "alice")
        r = mu_client.post(
            "/api/auth/cleanup-derived-data",
            json={"category": "templates"},
            headers=csrf(mu_client),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["category"] == "templates"
        assert body["derived_usage"]["templates"] == 0
        assert body["derived_usage"]["plots"] == 4

    def test_cleanup_keeps_other_users(self, mu_client, api_env):
        self._login(mu_client, "alice")
        r = mu_client.post(
            "/api/auth/cleanup-derived-data",
            json={"category": "templates"},
            headers=csrf(mu_client),
        )
        assert r.status_code == 200
        bob_templates = userspace.derived_dir(api_env["bob"]["id"], "templates")
        assert [p.name for p in bob_templates.iterdir()] == ["b.tex"]

    def test_cleanup_unknown_category_400(self, mu_client, api_env):
        self._login(mu_client, "alice")
        r = mu_client.post(
            "/api/auth/cleanup-derived-data",
            json={"category": "projects"},
            headers=csrf(mu_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_category"

    def test_me_includes_derived_usage_breakdown(self, mu_client, api_env):
        self._login(mu_client, "alice")
        r = mu_client.get("/api/auth/me")
        assert r.status_code == 200
        usage = r.json()["usage"]
        assert usage["derived_usage_bytes"] == 7  # 3 + 4
        assert usage["derived_usage"]["templates"] == 3
        assert usage["derived_usage"]["plots"] == 4


# ── sessions 过期删除 ────────────────────────────────────────────
class TestPurgeExpiredSessions:
    def test_expired_removed_active_kept(self, env):
        live_token = auth_core.create_session(env["user"]["id"])
        dead_token = auth_core.create_session(env["user"]["id"])
        dead_row = db_module.db_query_one(
            "SELECT id FROM sessions WHERE token_hash = ?",
            (auth_core.token_hash(dead_token),),
        )
        past = "2000-01-01T00:00:00+00:00"
        db_module.db_execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            (past, dead_row["id"]),
        )
        removed = auth_core.purge_expired_sessions()
        assert removed == 1
        assert auth_core.validate_session(live_token) is not None
        assert auth_core.validate_session(dead_token) is None


# ── writing_chat 保留期清理 ──────────────────────────────────────
class TestWritingChatRetention:
    @staticmethod
    def _make_session_file(env, scope: str, name: str, age_days: int):
        user_id = env["user"]["id"]
        d = userspace.derived_dir(user_id, "writing_chat_sessions") / scope
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_text("{}", encoding="utf-8")
        old = time.time() - age_days * 86400
        os.utime(f, (old, old))
        return f

    def test_zero_retention_deletes_nothing(self, env):
        """默认 0 = 不自动删，仅统计：过期文件保留。"""
        f = self._make_session_file(env, "scope-a", "old.json", age_days=365)
        assert wc_cleanup.purge_expired_writing_chats() == 0
        assert f.exists()

    def test_positive_retention_purges_old_only(self, env):
        """retention>0：超期会话删除、近期保留、空 scope 目录收敛。"""
        old = self._make_session_file(env, "scope-old", "old.json", age_days=60)
        keep_scope = "scope-keep"
        recent = self._make_session_file(env, keep_scope, "new.json", age_days=1)
        with _retention_ctx(30):
            removed = wc_cleanup.purge_expired_writing_chats()
        assert removed == 1
        assert not old.exists()
        assert recent.exists()
        # 空 scope 目录收尾；仍存活的 scope 目录保留
        sessions_root = userspace.derived_dir(
            env["user"]["id"], "writing_chat_sessions"
        )
        assert not (sessions_root / "scope-old").exists()
        assert (sessions_root / keep_scope).exists()

    def test_positive_retention_refreshes_cache(self, env):
        """retention>0 清理后分类缓存行即时更新（不等下一轮对账）。"""
        user_id = env["user"]["id"]
        self._make_session_file(env, "scope-old", "old.json", age_days=60)
        self._make_session_file(env, "scope-keep", "new.json", age_days=1)
        quota.resync_user_derived_usage(user_id)  # 建立含旧文件的基线缓存
        assert (
            quota.derived_usage_breakdown(user_id)["writing_chat_sessions"] == 4
        )  # 两个 "{}"
        with _retention_ctx(30):
            assert wc_cleanup.purge_expired_writing_chats() == 1
        # 删除量即时从缓存消失（维护循环内对账先行于清理，若清理后不刷新，
        # 偏大读数要滞留到下一轮）
        assert quota.derived_usage_breakdown(user_id)["writing_chat_sessions"] == 2

    def test_non_multiuser_mode_noop(self, tmp_path):
        build_mu_env(tmp_path, enabled=False)
        try:
            with _retention_ctx(30):
                assert wc_cleanup.purge_expired_writing_chats() == 0
        finally:
            _reset_config_state()


# ── 7z 监视线程采样计数器 ────────────────────────────────────────
class TestExtractUsageSampler:
    def test_same_total_as_dir_size(self, tmp_path):
        from backend.core.multiuser.upload import _sample_written_bytes

        root = tmp_path / "extract"
        (root / "sub" / "deep").mkdir(parents=True)
        (root / "a.txt").write_bytes(b"x" * 10)
        (root / "sub" / "b.bin").write_bytes(b"y" * 20)
        (root / "sub" / "deep" / "c.dat").write_bytes(b"z" * 30)
        link = root / "link.bin"
        os.symlink(root / "a.txt", link)
        big_limit = 1 << 30
        assert _sample_written_bytes(root, big_limit) == quota.dir_size(root) == 60

    def test_short_circuits_over_limit(self, tmp_path):
        from backend.core.multiuser.upload import _sample_written_bytes

        root = tmp_path / "extract"
        root.mkdir()
        for i in range(200):
            (root / f"f{i:03d}.bin").write_bytes(b"x" * 10)  # 共 2000 字节
        used = _sample_written_bytes(root, remaining_quota_bytes=100)
        # 超限即短路：返回值越过限额即可，无需完成全树遍历
        assert used > 100

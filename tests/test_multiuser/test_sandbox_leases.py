"""沙箱并发池租约（sandbox_slots）行为测试（多用户模式，v2 P1a §5.4）。"""

import pytest

from backend.core.multiuser import db as db_module
from backend.core.multiuser import sandbox_leases as leases
from backend.utils.sandbox.errors import SANDBOX_BUSY, sandbox_code_of


@pytest.fixture
def mu_env(tmp_path):
    """最小多用户环境：临时库 + 已初始化（复用 conftest 构建器）。"""
    from tests.test_multiuser.conftest import _reset_config_state, build_mu_env

    env = build_mu_env(tmp_path)
    yield env
    db_module.close_db()
    _reset_config_state()


def _active_rows():
    return db_module.db_query_all("SELECT * FROM sandbox_slots")


class TestAcquire:
    def test_acquire_within_limits(self, mu_env):
        slot = leases.acquire("u1", 60, max_global=4, max_per_user=1)
        rows = _active_rows()
        assert len(rows) == 1
        assert rows[0]["slot_id"] == slot
        assert rows[0]["user_id"] == "u1"
        assert rows[0]["expires_at"] > db_module.utcnow()

    def test_per_user_limit_rejected(self, mu_env):
        leases.acquire("u1", 60, max_global=4, max_per_user=1)
        with pytest.raises(RuntimeError) as exc_info:
            leases.acquire("u1", 60, max_global=4, max_per_user=1)
        assert sandbox_code_of(exc_info.value) == SANDBOX_BUSY
        # 失败路径不残留半写入状态
        assert len(_active_rows()) == 1

    def test_global_limit_across_users(self, mu_env):
        leases.acquire("u1", 60, max_global=2, max_per_user=1)
        leases.acquire("u2", 60, max_global=2, max_per_user=1)
        with pytest.raises(RuntimeError) as exc_info:
            leases.acquire("u3", 60, max_global=2, max_per_user=1)
        assert sandbox_code_of(exc_info.value) == SANDBOX_BUSY
        assert len(_active_rows()) == 2

    def test_other_users_not_blocked_by_per_user_limit(self, mu_env):
        leases.acquire("u1", 60, max_global=4, max_per_user=1)
        slot2 = leases.acquire("u2", 60, max_global=4, max_per_user=1)
        assert slot2


class TestReleaseAndExpiry:
    def test_release_removes_slot(self, mu_env):
        slot = leases.acquire("u1", 60, max_global=1, max_per_user=1)
        leases.release(slot)
        assert _active_rows() == []

    def test_release_idempotent(self, mu_env):
        slot = leases.acquire("u1", 60, max_global=1, max_per_user=1)
        leases.release(slot)
        leases.release(slot)  # 二次释放不报错

    def test_expired_slot_freed_for_new_acquire(self, mu_env):
        leases.acquire("u1", 60, max_global=1, max_per_user=1)
        # 人为把现有租约改为已过期
        past = "2000-01-01T00:00:00+00:00"
        db_module.db_execute("UPDATE sandbox_slots SET expires_at = ?", (past,))
        # 过期租约不计入活跃数，新租约可获取
        slot2 = leases.acquire("u2", 60, max_global=1, max_per_user=1)
        assert slot2

    def test_purge_expired_removes_only_expired(self, mu_env):
        slot_live = leases.acquire("u1", 3600, max_global=4, max_per_user=2)
        slot_dead = leases.acquire("u2", 3600, max_global=4, max_per_user=2)
        past = "2000-01-01T00:00:00+00:00"
        db_module.db_execute(
            "UPDATE sandbox_slots SET expires_at = ? WHERE slot_id = ?",
            (past, slot_dead),
        )
        removed = leases.purge_expired()
        assert removed == 1
        remaining = [r["slot_id"] for r in _active_rows()]
        assert remaining == [slot_live]


class TestTtl:
    def test_ttl_covers_timeout_plus_buffer(self, mu_env):
        slot = leases.acquire("u1", 600, max_global=4, max_per_user=1)
        row = db_module.db_query_one(
            "SELECT * FROM sandbox_slots WHERE slot_id = ?", (slot,)
        )
        # 600s 超时 + 120s 缓冲 → TTL 至少 720s（ISO 秒级精度，允许 1s 误差）
        acquired_at = row["acquired_at"]
        expires_at = row["expires_at"]
        from datetime import datetime

        delta = (
            datetime.fromisoformat(expires_at) - datetime.fromisoformat(acquired_at)
        ).total_seconds()
        assert delta >= 719

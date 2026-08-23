"""v2 P7：维护选主租约、flock 会话互斥、配置热重载、stdout 日志开关。"""

import asyncio
import json
import logging
import os
import time
from unittest import mock

import pytest

from backend.core import config as cfg_module
from backend.core.logger import (
    TimedRotatingFileHandler,
    get_logger,
    setup_logging,
    stdout_logging_enabled,
)
from backend.core.multiuser import db as db_module
from backend.core.multiuser import maintenance_lease as leases
from backend.core.multiuser import userspace
from backend.utils.session_mutex import (
    cross_process_session_lock,
    lock_file_for,
    remove_lock_file,
)


@pytest.fixture
def mu_env(tmp_path):
    from tests.test_multiuser.conftest import _reset_config_state, build_mu_env

    env = build_mu_env(tmp_path)
    yield env
    db_module.close_db()
    _reset_config_state()
    userspace.set_current_user_id(None)


# ── maintenance_lease 选主 ────────────────────────────────────────────
class TestMaintenanceLease:
    def test_double_acquire_second_fails(self, mu_env):
        assert leases.try_acquire(leases.LEASE_KEY_MAINTENANCE, "A", 3600) is True
        assert leases.try_acquire(leases.LEASE_KEY_MAINTENANCE, "B", 3600) is False
        assert leases.current_holder(leases.LEASE_KEY_MAINTENANCE) == "A"

    def test_same_holder_renews(self, mu_env):
        assert leases.try_acquire("k", "A", 3600) is True
        assert leases.try_acquire("k", "A", 7200) is True  # 续约
        row = db_module.db_query_one("SELECT * FROM maintenance_lease WHERE key = 'k'")
        assert row["holder"] == "A"

    def test_expired_lease_taken_over(self, mu_env):
        assert leases.try_acquire("k", "OLD", 3600) is True
        # 租约过期（模拟持有人崩溃后 TTL 到期）
        db_module.db_execute(
            "UPDATE maintenance_lease SET expires_at = ? WHERE key = 'k'",
            (db_module.utcnow(),),
        )
        assert leases.try_acquire("k", "NEW", 3600) is True
        assert leases.current_holder("k") == "NEW"

    def test_release_only_by_holder(self, mu_env):
        leases.try_acquire("k", "A", 3600)
        assert leases.release("k", "B") is False
        assert leases.release("k", "A") is True
        assert leases.current_holder("k") is None

    def test_non_holder_skips_cycle(self, mu_env, monkeypatch):
        """非持有者跳过循环体；持有者执行。"""
        from backend.core.multiuser import maintenance as maint

        calls = {"count": 0}

        async def fake_cycle():
            calls["count"] += 1
            return False

        monkeypatch.setattr(maint, "run_maintenance_cycle", fake_cycle)

        # 副本 B 抢先持有租约 → 副本 A 的循环一轮不执行
        assert leases.try_acquire(leases.LEASE_KEY_MAINTENANCE, "replica-B", 3600)

        async def one_round():
            holder = leases.make_holder()
            got = await asyncio.to_thread(
                leases.try_acquire,
                leases.LEASE_KEY_MAINTENANCE,
                holder,
                3600,
            )
            if got:
                await maint.run_maintenance_cycle()

        asyncio.run(one_round())
        assert calls["count"] == 0  # A 未抢到租约，未执行

    def test_holder_runs_and_releases_on_cancel(self, mu_env):
        """持租约者执行循环体；取消时释放租约（易主立即可用）。"""
        from backend.core.multiuser import maintenance as maint

        ran = []

        async def fake_cycle():
            ran.append(1)
            return True

        async def drive():
            task = asyncio.ensure_future(maint.maintenance_loop())
            deadline = time.monotonic() + 5
            while not ran and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        orig_cycle = maint.run_maintenance_cycle
        orig_interval = maint.DEFAULT_INTERVAL_SECONDS
        maint.run_maintenance_cycle = fake_cycle
        maint.DEFAULT_INTERVAL_SECONDS = 0.05
        try:
            asyncio.run(drive())
        finally:
            maint.run_maintenance_cycle = orig_cycle
            maint.DEFAULT_INTERVAL_SECONDS = orig_interval
        assert ran
        assert leases.current_holder(leases.LEASE_KEY_MAINTENANCE) is None


# ── flock 会话互斥 ────────────────────────────────────────────────────
class TestSessionMutexFlock:
    def test_same_session_serialized_across_processes(self, tmp_path):
        """同会话并发进入被挡：第二个 flock 持有者必须等第一个退出。"""
        session_dir = tmp_path / "sess1"
        session_dir.mkdir()
        order = []

        def worker(name: str, hold: float) -> None:
            async def run():
                async with cross_process_session_lock(lock_file_for(session_dir)):
                    order.append(f"{name}-in")
                    await asyncio.sleep(hold)
                    order.append(f"{name}-out")

            asyncio.run(run())

        import threading

        t1 = threading.Thread(target=worker, args=("A", 0.3))
        t2 = threading.Thread(target=worker, args=("B", 0.0))
        t1.start()
        time.sleep(0.1)  # 确保 A 先持锁
        t2.start()
        t1.join()
        t2.join()
        assert order.index("A-out") < order.index("B-in")

    def test_different_sessions_independent(self, tmp_path):
        s1 = tmp_path / "s1"
        s2 = tmp_path / "s2"
        s1.mkdir()
        s2.mkdir()

        async def run():
            async def hold():
                async with cross_process_session_lock(lock_file_for(s1)):
                    await asyncio.sleep(0.2)

            async def enter_other():
                await asyncio.sleep(0.05)
                async with cross_process_session_lock(lock_file_for(s2)):
                    return "entered"

            task = asyncio.ensure_future(hold())
            result = await enter_other()
            await task
            return result

        assert asyncio.run(run()) == "entered"

    def test_lock_file_placed_next_to_dir_not_inside(self, tmp_path):
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        lf = lock_file_for(session_dir)
        assert lf.parent == tmp_path
        assert lf.name == "s.lock"

        async def run():
            async with cross_process_session_lock(lf):
                pass

        asyncio.run(run())
        assert lf.exists()
        remove_lock_file(lf)
        assert not lf.exists()

    def test_remove_skipped_while_lock_held(self, tmp_path):
        """持锁期间删除被跳过：防止 unlink 后新进程新建锁文件绕过互斥。"""
        import fcntl
        import os as _os

        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        lf = lock_file_for(session_dir)
        holder = _os.open(str(lf), _os.O_CREAT | _os.O_RDWR, 0o600)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            remove_lock_file(lf)
            assert lf.exists()  # 正被持有：删除被跳过，留待释放后清理
        finally:
            _os.close(holder)
        # 锁释放后：同一文件可正常清理
        remove_lock_file(lf)
        assert not lf.exists()

    def test_remove_missing_lock_file_is_noop(self, tmp_path):
        remove_lock_file(tmp_path / "never-existed.lock")  # 不抛错


# ── 配置热重载 ────────────────────────────────────────────────────────
class TestConfigHotReload:
    @pytest.fixture
    def config_env(self, tmp_path):
        from tests.test_multiuser.conftest import _reset_config_state

        cfg_path = tmp_path / "config.json"
        data = {
            "server": {"host": "127.0.0.1", "port": 8000},
            "llm": {"api_key": "k1"},
            "multi_user": {"enabled": True, "db_path": str(tmp_path / "db.sqlite")},
        }
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        cfg_module.load_config(str(cfg_path))
        yield cfg_path, data
        db_module.close_db()
        _reset_config_state()

    def _bump_mtime(self, path):
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def test_change_detected_and_reloaded(self, config_env):
        cfg_path, data = config_env
        data["llm"]["temperature"] = 0.9
        data["llm"]["model"] = "new-model"
        self._bump_mtime(cfg_path)
        cfg_path.write_text(json.dumps(data), encoding="utf-8")

        cfg = cfg_module.get_config()
        assert cfg.llm.model == "new-model"
        assert cfg.llm.temperature == 0.9

    def test_multi_user_enabled_pinned_to_boot_value(self, config_env):
        """multi_user.enabled 变更不热生效：锚定启动值并告警。"""
        cfg_path, data = config_env
        assert cfg_module.get_config().multi_user.enabled is True
        data["multi_user"]["enabled"] = False
        self._bump_mtime(cfg_path)
        cfg_path.write_text(json.dumps(data), encoding="utf-8")

        cfg = cfg_module.get_config()
        assert cfg.multi_user.enabled is True  # 保持启动值
        assert cfg_module._boot_multi_user_enabled is True

    def test_reload_reapplies_env_api_key_and_keeps_anchor(
        self, config_env, monkeypatch
    ):
        """热重载经纯解析函数：重新应用 LX_LLM_KEY 覆盖且锚定值不被改写。

        回归锚点：重载路径不得经由 load_config 的全局赋值——否则并发
        get_config() 可观察到未锚定的 multi_user.enabled 中间态。
        """
        from tests.test_multiuser.conftest import MINIMAL_CONFIG

        monkeypatch.setenv("LX_LLM_KEY", "env-key-xyz")
        cfg_path, data = config_env
        merged = {**MINIMAL_CONFIG, **data}
        merged["llm"] = {"api_key": "", "base_url": "https://x/v1", "model": "v2"}
        merged["multi_user"]["enabled"] = False  # 磁盘值与锚定值不同
        self._bump_mtime(cfg_path)
        cfg_path.write_text(json.dumps(merged), encoding="utf-8")

        cfg = cfg_module.get_config()
        assert cfg.llm.model == "v2"
        assert cfg.llm.api_key == "env-key-xyz"  # 环境变量覆盖重新生效
        assert cfg_module._api_key_from_env is True
        assert cfg.multi_user.enabled is True  # 锚定启动值
        assert cfg_module._boot_multi_user_enabled is True  # 锚定值未被改写

    def test_invalid_new_config_keeps_old(self, config_env, caplog):
        cfg_path, data = config_env
        old_model = cfg_module.get_config().llm.model
        self._bump_mtime(cfg_path)
        cfg_path.write_text("{not-json", encoding="utf-8")
        cfg = cfg_module.get_config()
        assert cfg.llm.model == old_model

    def test_save_config_does_not_self_reload(self, config_env):
        from backend.core.config import update_config_from_dict

        update_config_from_dict({"llm": {"api_key": "***abcd"}})
        fp_before = cfg_module._config_fingerprint
        cfg_module.get_config()
        assert cfg_module._config_fingerprint == fp_before


# ── outbox 原子认领（多实例防重复投递） ───────────────────────────────
class TestOutboxAtomicClaim:
    @pytest.fixture
    def mail_env(self, mu_env_factory):
        """SMTP 已启用（指向不存在的本地端口，本组用例不实际投递）。"""
        from tests.test_multiuser.conftest import smtp_enabled_extra

        return mu_env_factory(smtp_extra=smtp_enabled_extra(1))

    def _queue_one(self, recipient="a@x.test", subject="s"):
        from backend.core.multiuser import mail

        ok = mail.enqueue(recipient=recipient, subject=subject, body_text="b")
        assert ok is True
        return db_module.db_query_one(
            "SELECT id FROM email_outbox ORDER BY created_at DESC LIMIT 1"
        )["id"]

    def test_claimed_message_not_redelivered(self, mail_env):
        """已被副本 A 认领（sending）的消息，副本 B 的 process_outbox 跳过。"""
        from backend.core.multiuser import mail

        msg_id = self._queue_one()
        assert mail._claim_message(msg_id) is True  # 副本 A 认领

        async def drive():
            # 副本 B：无 queued 可取，直接返回 0（不会重复投递）
            return await mail.process_outbox()

        assert asyncio.run(drive()) == 0
        row = db_module.db_query_one(
            "SELECT * FROM email_outbox WHERE id = ?", (msg_id,)
        )
        assert row["status"] == "sending"

    def test_stale_sending_requeued(self, mail_env):
        from backend.core.multiuser import mail

        msg_id = self._queue_one()
        mail._claim_message(msg_id)
        # 认领时间回拨到阈值之外（模拟认领者崩溃）
        db_module.db_execute(
            "UPDATE email_outbox SET claimed_at = ? WHERE id = ?",
            (db_module.utcnow_after(-mail.SENDING_STALE_SECONDS - 60), msg_id),
        )
        requeued = mail.requeue_stale_outbox_claims()
        assert requeued == 1
        row = db_module.db_query_one(
            "SELECT status FROM email_outbox WHERE id = ?", (msg_id,)
        )
        assert row["status"] == "queued"

    def test_fresh_sending_not_requeued(self, mail_env):
        from backend.core.multiuser import mail

        msg_id = self._queue_one()
        mail._claim_message(msg_id)  # 刚刚认领
        assert mail.requeue_stale_outbox_claims() == 0


# ── stdout 日志开关 ───────────────────────────────────────────────────
class TestStdoutLoggingSwitch:
    @pytest.fixture
    def clean_root_logger(self):
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        yield root
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)

    def test_default_keeps_rotating_file(
        self, clean_root_logger, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("LATEXCELLENCE_LOG_STDOUT", raising=False)
        log_dir = tmp_path / "logs"
        setup_logging(level="INFO", log_dir=str(log_dir))
        handlers = clean_root_logger.handlers
        assert any(isinstance(h, TimedRotatingFileHandler) for h in handlers)
        assert any(isinstance(h, logging.StreamHandler) for h in handlers)
        assert (log_dir / "latexcellence.log").exists()

    def test_stdout_mode_only_stream(self, clean_root_logger, tmp_path, monkeypatch):
        monkeypatch.setenv("LATEXCELLENCE_LOG_STDOUT", "1")
        assert stdout_logging_enabled() is True
        log_dir = tmp_path / "logs2"
        setup_logging(level="INFO", log_dir=str(log_dir))
        handlers = clean_root_logger.handlers
        assert not any(isinstance(h, TimedRotatingFileHandler) for h in handlers)
        assert (
            sum(
                isinstance(h, logging.StreamHandler)
                and getattr(h, "_latexcellence_owned", False)
                for h in handlers
            )
            == 1
        )
        assert not log_dir.exists()  # 不创建日志目录

    def test_emit_goes_to_stream_in_stdout_mode(
        self, clean_root_logger, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LATEXCELLENCE_LOG_STDOUT", "1")
        setup_logging(level="INFO", log_dir=str(tmp_path / "logs3"))
        get_logger("p7test").info("hello-stdout")
        captured = capsys.readouterr()
        assert "hello-stdout" in (captured.out + captured.err)

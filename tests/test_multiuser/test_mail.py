"""多用户模式 v2 P6：邮件服务模块测试（outbox 队列 / 降级 / 重试 / 限速 /
待审批聚合 / 配额预警迟滞 / 迁移与邮箱唯一索引）。"""

import asyncio
import sqlite3

import pytest

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import mail
from tests.test_multiuser.conftest import (
    build_mu_env,
    last_outbox_row,
    smtp_enabled_extra,
)


def _run(coro):
    return asyncio.run(coro)


def _outbox_rows():
    return db_module.db_query_all("SELECT * FROM email_outbox ORDER BY created_at, id")


def _alert_rows():
    return db_module.db_query_all("SELECT * FROM user_alert_state")


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """退避等待归零（连接拒绝重试路径不拖慢测试）。"""
    monkeypatch.setattr(mail, "_BACKOFF_BASE_SECONDS", 0.0)


# ── outbox 队列与降级 ───────────────────────────────────────────
class TestOutboxQueue:
    def test_deliver_end_to_end(self, tmp_path, smtp_server):
        """投递全链路：入队 → process_outbox → mock SMTP 收到信件。"""
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(smtp_server["port"]))
        assert mail.enqueue("u@x.test", "Hello", "Body text") is True
        rows = _outbox_rows()
        assert len(rows) == 1 and rows[0]["status"] == "queued"

        sent = _run(mail.process_outbox())
        assert sent == 1
        assert len(smtp_server["messages"]) == 1
        msg = smtp_server["messages"][0]
        assert msg["rcpt_tos"] == ["u@x.test"]
        assert "Body text" in msg["content"]
        assert "Subject: Hello" in msg["content"]

        row = _outbox_rows()[0]
        assert row["status"] == "sent"
        assert row["attempts"] >= 1
        assert row["sent_at"] is not None
        assert row["last_error"] is None

    def test_retry_then_failed(self, tmp_path):
        """不可达端口：逐轮重试 attempts 递增，超过 retry_attempts 留档 failed。"""
        # 端口 1 不可连接（<1024 需特权，connect 直接被拒）
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(1, retry_attempts=3))
        mail.enqueue("u@x.test", "S", "B")
        # 每轮失败后退避再试（退避基数在 fixture 中归零）；多轮驱动至上限
        for _ in range(5):
            assert _run(mail.process_outbox()) == 0
        row = _outbox_rows()[0]
        assert row["status"] == "failed"
        assert row["attempts"] == 3
        assert row["last_error"]  # 失败原因留档供运维排查

    def test_rate_limit_window(self, tmp_path, smtp_server):
        """全局限速：max_send_per_minute=1 时窗口内只发一封，其余留 queued。"""
        build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"], max_send_per_minute=1),
        )
        for i in range(3):
            mail.enqueue(f"u{i}@x.test", "S", "B")
        sent = _run(mail.process_outbox())
        assert sent == 1
        statuses = [r["status"] for r in _outbox_rows()]
        assert statuses.count("sent") == 1
        assert statuses.count("queued") == 2

    def test_rate_limit_releases_after_window(self, tmp_path, smtp_server):
        """窗口滑过（sent_at 移出 60s）后，剩余消息继续投递。"""
        build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"], max_send_per_minute=1),
        )
        mail.enqueue("a@x.test", "S", "B")
        mail.enqueue("b@x.test", "S", "B")
        assert _run(mail.process_outbox()) == 1
        # 把已发行的 sent_at 拨回窗口之外
        old = db_module.utcnow_after(-120)
        db_module.db_execute(
            "UPDATE email_outbox SET sent_at = ? WHERE status='sent'", (old,)
        )
        assert _run(mail.process_outbox()) == 1
        assert len(smtp_server["messages"]) == 2

    def test_purge_old_outbox(self, tmp_path, smtp_server):
        """留档清理：超期 sent/failed 行删除，近期行保留。"""
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(smtp_server["port"]))
        mail.enqueue("a@x.test", "S", "B")
        _run(mail.process_outbox())
        old = db_module.utcnow_after(-(mail.OUTBOX_RETENTION_DAYS + 1) * 86400)
        db_module.db_execute(
            "UPDATE email_outbox SET created_at = ? WHERE recipient='a@x.test'", (old,)
        )
        mail.enqueue("b@x.test", "S", "B")  # 近期行
        removed = mail.purge_old_outbox()
        assert removed == 1
        remaining = {r["recipient"] for r in _outbox_rows()}
        assert remaining == {"b@x.test"}

    def test_html_alternative(self, tmp_path, smtp_server):
        body_html = "<p>Body</p>"
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(smtp_server["port"]))
        mail.enqueue("u@x.test", "S", "B", body_html=body_html)
        _run(mail.process_outbox())
        content = smtp_server["messages"][0]["content"]
        assert "text/plain" in content and "text/html" in content


class TestDegradation:
    def test_enqueue_silent_when_disabled(self, mu_env):
        """smtp.enabled=false：enqueue 返回 False 且不落行。"""
        assert mail.smtp_ready() is False
        assert mail.enqueue("u@x.test", "S", "B") is False
        assert _outbox_rows() == []
        assert _run(mail.process_outbox()) == 0

    def test_enqueue_silent_when_host_missing(self, tmp_path):
        """enabled=true 但缺 host：同样视为未就绪（静默跳过）。"""
        build_mu_env(tmp_path, smtp_extra={"enabled": True, "host": ""})
        assert mail.smtp_ready() is False
        assert mail.enqueue("u@x.test", "S", "B") is False


# ── 待审批聚合通知 ──────────────────────────────────────────────
class TestPendingDigest:
    def test_digest_aggregated_and_debounced(self, tmp_path, smtp_server):
        """多个 pending 注册合并为一封；5 分钟去抖窗口内不重复发送。"""
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(smtp_server["port"]))
        admin = auth_core.create_user(
            "admin",
            "password-123",
            email="admin@x.test",
            role="admin",
            status="active",
        )
        assert admin["email"]
        auth_core.create_user("alice", "password-123")
        auth_core.create_user("bob", "password-123")

        assert mail.notify_pending_registrations() == 1
        _run(mail.process_outbox())
        assert len(smtp_server["messages"]) == 1
        content = smtp_server["messages"][0]["content"]
        assert "alice" in content and "bob" in content
        assert smtp_server["messages"][0]["rcpt_tos"] == ["admin@x.test"]

        # 去抖窗口内再次触发：不重复投递
        auth_core.create_user("carol", "password-123")
        assert mail.notify_pending_registrations() == 0
        assert len(smtp_server["messages"]) == 1

        # 窗口滑过后恢复发送，且合并列出新增用户
        old = db_module.utcnow_after(-mail.PENDING_DIGEST_INTERVAL_SECONDS - 1)
        db_module.db_execute("UPDATE user_alert_state SET sent_at = ?", (old,))
        assert mail.notify_pending_registrations() == 1
        _run(mail.process_outbox())
        assert "carol" in smtp_server["messages"][1]["content"]

    def test_no_marker_without_admin_email(self, tmp_path, smtp_server):
        """admin 未绑邮箱时不留去抖标记：绑定邮箱后可正常发出。"""
        build_mu_env(tmp_path, smtp_extra=smtp_enabled_extra(smtp_server["port"]))
        auth_core.create_user("admin", "password-123", role="admin", status="active")
        auth_core.create_user("alice", "password-123")
        assert mail.notify_pending_registrations() == 0
        # 管理员补绑邮箱后立即可发
        auth_core.set_user_email(
            db_module.db_query_one("SELECT id FROM users WHERE username='admin'")["id"],
            "admin@x.test",
        )
        assert mail.notify_pending_registrations() == 1


# ── 配额预警迟滞 ────────────────────────────────────────────────
class TestQuotaAlerts:
    @pytest.fixture()
    def quota_env(self, tmp_path, smtp_server):
        env = build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"]),
            mu_extra={
                "quota_warn_percent": 80,
                "quota_critical_percent": 95,
                "quota_alert_reset_percent": 75,
            },
        )
        admin = auth_core.create_user(
            "admin",
            "password-123",
            email="admin@x.test",
            role="admin",
            status="active",
        )
        auth_core.set_user_quota(admin["id"], 1000, None)
        user = auth_core.create_user(
            "alice",
            "password-123",
            email="alice@x.test",
            status="active",
        )
        auth_core.set_user_quota(user["id"], 1000, 500)
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "created_at, updated_at) VALUES ('p1', ?, 'proj', 0, 'active', ?, ?)",
            (user["id"], db_module.utcnow(), db_module.utcnow()),
        )
        return {
            **env,
            "admin_id": admin["id"],
            "user_id": user["id"],
            "messages": smtp_server["messages"],
        }

    def _set_project_size(self, env, size):
        db_module.db_execute(
            "UPDATE projects SET size_bytes = ? WHERE id='p1'", (size,)
        )

    def _set_derived_usage(self, env, size):
        db_module.db_execute(
            "INSERT INTO user_derived_usage (user_id, subdir, size_bytes, updated_at) "
            "VALUES (?, 'templates', ?, ?) "
            "ON CONFLICT(user_id, subdir) DO UPDATE SET size_bytes = excluded.size_bytes",
            (env["user_id"], size, db_module.utcnow()),
        )

    def test_warn_sent_once_then_hysteresis(self, quota_env):
        """80% 发一次 → 重跑不重复 → 降到 75% 下清标记 → 再越线可再发。"""
        env = quota_env
        self._set_project_size(env, 820)  # 82% ≥ warn
        assert mail.evaluate_quota_alerts() == 1
        kinds = {r["kind"] for r in _alert_rows()}
        assert "project_warn" in kinds

        # 迟滞：仍处 warn 区间，重复评估不再发送
        assert mail.evaluate_quota_alerts() == 0
        _run(mail.process_outbox())
        assert len(env["messages"]) == 1
        assert sorted(env["messages"][0]["rcpt_tos"]) == ["alice@x.test"]

        # 降到 reset 阈值以下：标记清除
        self._set_project_size(env, 500)  # 50%
        assert mail.evaluate_quota_alerts() == 0
        assert all(not r["kind"].startswith("project_") for r in _alert_rows())

        # 再次越线：可重新发送
        self._set_project_size(env, 850)
        assert mail.evaluate_quota_alerts() == 1
        _run(mail.process_outbox())
        assert len(env["messages"]) == 2

    def test_critical_includes_admins(self, quota_env):
        """≥95%：critical 邮件同时送用户与全部 admin；warn 标记升级为 critical。"""
        env = quota_env
        self._set_project_size(env, 960)
        assert mail.evaluate_quota_alerts() == 2
        _run(mail.process_outbox())
        recipients = sorted(m["rcpt_tos"][0] for m in env["messages"])
        assert recipients == ["admin@x.test", "alice@x.test"]
        kinds = {r["kind"] for r in _alert_rows()}
        assert kinds == {"project_critical"}

        # critical 已发，同区间重复评估不重发；回落 warn 区间同样保持 critical 标记
        self._set_project_size(env, 900)
        assert mail.evaluate_quota_alerts() == 0

    def test_derived_dimension(self, quota_env):
        """衍生数据维度独立检查（读 user_derived_usage 缓存）。"""
        env = quota_env
        self._set_derived_usage(env, 450)  # 90% ≥ warn < critical
        assert mail.evaluate_quota_alerts() == 1
        kinds = {r["kind"] for r in _alert_rows()}
        assert kinds == {"derived_warn"}

    def test_no_email_no_mail_but_admin_critical_only(self, tmp_path, smtp_server):
        """用户未绑邮箱时 warn 不发；critical 仍送 admin。"""
        build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"]),
            mu_extra={"quota_critical_percent": 95},
        )
        admin = auth_core.create_user(
            "admin",
            "password-123",
            email="admin@x.test",
            role="admin",
            status="active",
        )
        auth_core.set_user_quota(admin["id"], 1000, None)
        user = auth_core.create_user(
            "noemail",
            "password-123",
            status="active",
        )
        auth_core.set_user_quota(user["id"], 1000, None)
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "created_at, updated_at) VALUES ('p1', ?, 'proj', 980, 'active', ?, ?)",
            (user["id"], db_module.utcnow(), db_module.utcnow()),
        )
        assert mail.evaluate_quota_alerts() == 1
        _run(mail.process_outbox())
        assert smtp_server["messages"][0]["rcpt_tos"] == ["admin@x.test"]

    def test_no_recipient_leaves_no_marker(self, tmp_path, smtp_server):
        """零收件人（用户与 admin 均未绑邮箱）不留迟滞标记：补绑后可发出。"""
        build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"]),
            mu_extra={
                "quota_warn_percent": 80,
                "quota_critical_percent": 95,
                "quota_alert_reset_percent": 75,
            },
        )
        user = auth_core.create_user("noemail", "password-123", status="active")
        auth_core.set_user_quota(user["id"], 1000, None)
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "created_at, updated_at) VALUES ('p1', ?, 'proj', 850, 'active', ?, ?)",
            (user["id"], db_module.utcnow(), db_module.utcnow()),
        )
        # 85% ≥ warn：无收件人 → 不发信也不置标记（否则补绑后永远收不到）
        assert mail.evaluate_quota_alerts() == 0
        assert _alert_rows() == []
        # 补绑邮箱后下一轮评估正常发出并置标记
        auth_core.set_user_email(user["id"], "late@x.test")
        assert mail.evaluate_quota_alerts() == 1
        assert {r["kind"] for r in _alert_rows()} == {"project_warn"}


# ── 找回令牌核心（auth_core 层）───────────────────────────────────
class TestResetTokenCore:
    @pytest.fixture()
    def token_env(self, tmp_path):
        build_mu_env(tmp_path)
        return auth_core.create_user("alice", "password-123", email="a@x.test")

    def test_create_and_consume_single_use(self, token_env):
        token, expires = auth_core.create_password_reset_token(token_env["id"])
        assert len(token) >= 32
        # 库中只有哈希
        row = last_outbox_row()  # 无关行应为 None（未入队邮件）
        assert row is None
        stored = db_module.db_query_one(
            "SELECT * FROM password_reset_tokens WHERE user_id = ?", (token_env["id"],)
        )
        assert stored["token_hash"] == auth_core.token_hash(token)
        assert stored["used_at"] is None

        uid = auth_core.consume_password_reset_token(token)
        assert uid == token_env["id"]
        # 重放：单次有效
        assert auth_core.consume_password_reset_token(token) is None

    def test_expired_token_rejected(self, token_env):
        token, _ = auth_core.create_password_reset_token(token_env["id"])
        past = db_module.utcnow_after(-10)
        db_module.db_execute("UPDATE password_reset_tokens SET expires_at = ?", (past,))
        assert auth_core.consume_password_reset_token(token) is None

    def test_garbage_token_rejected(self, token_env):
        assert auth_core.consume_password_reset_token("") is None
        assert auth_core.consume_password_reset_token("no-such-token") is None

    def test_purge_expired_and_used(self, token_env):
        token, _ = auth_core.create_password_reset_token(token_env["id"])
        auth_core.consume_password_reset_token(token)
        _, _ = auth_core.create_password_reset_token(token_env["id"])
        past = db_module.utcnow_after(-10)
        db_module.db_execute(
            "UPDATE password_reset_tokens SET expires_at = ? WHERE used_at IS NULL",
            (past,),
        )
        removed = auth_core.purge_expired_reset_tokens()
        assert removed == 2
        assert db_module.db_query_all("SELECT * FROM password_reset_tokens") == []


# ── 迁移 v7 与邮箱部分唯一索引 ───────────────────────────────────
class TestMigrationV7:
    def test_fresh_db_tables_and_index(self, mu_env):
        tables = {
            r["name"]
            for r in db_module.db_query_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"email_outbox", "password_reset_tokens", "user_alert_state"} <= tables
        indexes = {
            r["name"]
            for r in db_module.db_query_all(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert db_module.USERS_EMAIL_UNIQUE_INDEX in indexes

    def test_unique_email_enforced_case_insensitive(self, mu_env):
        auth_core.create_user("alice", "password-123", email="Alice@X.com")
        with pytest.raises(ValueError, match="Email already taken"):
            auth_core.create_user("bob", "password-123", email="alice@x.com")

    def test_empty_emails_allowed_multiple(self, mu_env):
        auth_core.create_user("alice", "password-123")
        auth_core.create_user("bob", "password-123")
        assert db_module.db_count("SELECT COUNT(*) FROM users") == 2

    def test_legacy_db_with_duplicate_emails_skips_index(self, tmp_path, caplog):
        """存量重复邮箱：迁移完成、告警列冲突行、索引缺位、主流程不受影响。"""
        import json as _json
        import os as _os
        from unittest import mock as _mock

        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import MINIMAL_CONFIG, _reset_config_state

        db_path = tmp_path / "legacy-dup.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(db_module.SCHEMA)
        base = db_module.utcnow()
        for name in ("dup", "Dup2"):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, email, role, "
                "status, created_at, updated_at) VALUES (?, ?, 'h', ?, 'user', "
                "'pending', ?, ?)",
                (name.lower(), name, "Same@X.com", base, base),
            )
        conn.commit()
        conn.execute("PRAGMA user_version = 6")
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
        try:
            with _mock.patch.dict(_os.environ, {}, clear=True):
                cfg_module.load_config(str(config_path))
                db_module.init_db()
            assert db_module.schema_status()["user_version"] == (
                db_module.SCHEMA_VERSION
            )
            # 表建齐、索引因冲突跳过（warning 列出冲突邮箱）
            tables = {
                r["name"]
                for r in db_module.db_query_all(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "email_outbox" in tables
            indexes = {
                r["name"]
                for r in db_module.db_query_all(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert db_module.USERS_EMAIL_UNIQUE_INDEX not in indexes
            warnings = [
                r
                for r in caplog.records
                if r.levelname == "WARNING"
                and "Duplicate user emails" in r.getMessage()
            ]
            assert warnings and "same@x.com" in warnings[0].getMessage().lower()
        finally:
            db_module.close_db()
            _reset_config_state()

        # 管理员人工修复冲突后重启：索引自动补建（自愈语义）
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE users SET email='' WHERE username='Dup2'")
        conn.commit()
        conn.close()
        try:
            with _mock.patch.dict(_os.environ, {}, clear=True):
                cfg_module.load_config(str(config_path))
                db_module.init_db()
            indexes = {
                r["name"]
                for r in db_module.db_query_all(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert db_module.USERS_EMAIL_UNIQUE_INDEX in indexes
        finally:
            db_module.close_db()
            _reset_config_state()

    def test_legacy_db_without_duplicates_gets_index(self, tmp_path):
        """存量无重复邮箱：迁移后索引直接就位。"""
        import json as _json
        import os as _os
        from unittest import mock as _mock

        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import MINIMAL_CONFIG, _reset_config_state

        db_path = tmp_path / "legacy-clean.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(db_module.SCHEMA)
        base = db_module.utcnow()
        conn.execute(
            "INSERT INTO users (id, username, password_hash, email, role, "
            "status, created_at, updated_at) VALUES "
            "('u1', 'solo', 'h', 'solo@x.com', 'user', 'pending', ?, ?)",
            (base, base),
        )
        conn.commit()
        conn.execute("PRAGMA user_version = 6")
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
        try:
            with _mock.patch.dict(_os.environ, {}, clear=True):
                cfg_module.load_config(str(config_path))
                db_module.init_db()
            indexes = {
                r["name"]
                for r in db_module.db_query_all(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert db_module.USERS_EMAIL_UNIQUE_INDEX in indexes
        finally:
            db_module.close_db()
            _reset_config_state()

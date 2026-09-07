"""多用户模式 v2 P6：自助找回密码与邮箱绑定 API 测试。

覆盖：找回全流程（命中投递 / 无邮箱降级 / 重复邮箱降级 / SMTP 未配置降级 /
令牌过期 / 重放 / 枚举防护恒定时长 / IP 限流）、邮箱唯一性（注册 / profile
改绑冲突）、PUT /api/auth/profile 当前密码确认、凭据/安全状态变更
（重置密码 / 改绑邮箱 / 禁用账号）连带吊销未消费找回令牌。"""

import asyncio
import re
import time

import pytest

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import mail
from tests.test_multiuser.conftest import build_mu_app as _build_app
from tests.test_multiuser.conftest import (
    build_mu_env,
    csrf,
    last_outbox_row,
    smtp_enabled_extra,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def mail_env(tmp_path, smtp_server):
    """SMTP 已启用的多用户环境 + TestClient。"""
    build_mu_env(
        tmp_path,
        smtp_extra=smtp_enabled_extra(
            smtp_server["port"], site_base_url="https://latex.test"
        ),
    )
    return {"messages": smtp_server["messages"], "tmp_path": tmp_path}


@pytest.fixture()
def mail_client(mail_env):
    from fastapi.testclient import TestClient

    return TestClient(_build_app())


def _register(client, username, email="", password="password-123"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "email": email},
    )


def _activate(username):
    uid = db_module.db_query_one(
        "SELECT id FROM users WHERE username = ?", (username,)
    )["id"]
    db_module.db_execute("UPDATE users SET status='active' WHERE id=?", (uid,))
    return uid


def _token_from_outbox():
    row = last_outbox_row()
    m = re.search(r"token=([A-Za-z0-9_\-]+)", row["body_text"])
    return m.group(1) if m else None


# ── 找回全流程 ──────────────────────────────────────────────────
class TestForgotResetFlow:
    def test_full_flow_with_real_smtp(self, mail_client, mail_env):
        """命中：注册→激活→forgot→邮件令牌→reset→旧会话吊销→新密码登录。"""
        assert _register(mail_client, "alice", "alice@x.test").status_code == 200
        uid = _activate("alice")
        # 建立一个旧会话供吊销断言
        old_token = auth_core.create_session(uid)

        resp = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "alice"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "delivery": "sent"}

        # 发送协程由 lifespan 启动；测试中手动驱动一轮队列消费
        _run(mail.process_outbox())

        # 邮件经 mock SMTP 实际投递；中文主题经 MIME 编码，链接以库内
        # 正文为准断言（含站点前缀）
        assert len(mail_env["messages"]) == 1
        msg = mail_env["messages"][0]
        assert msg["rcpt_tos"] == ["alice@x.test"]

        token = _token_from_outbox()
        assert token, last_outbox_row()["body_text"]
        assert (
            f"https://latex.test/reset-password?token={token}"
            in last_outbox_row()["body_text"]
        )
        new_password = "brand-new-pass-1"
        resp = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": new_password},
        )
        assert resp.status_code == 200
        # 旧会话已吊销；新密码可登录，旧密码不可
        assert auth_core.validate_session(old_token) is None
        login = mail_client.post(
            "/api/auth/login", json={"username": "alice", "password": new_password}
        )
        assert login.status_code == 200
        bad = mail_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password-123"}
        )
        assert bad.status_code == 401

    def test_reset_by_email_identifier(self, mail_client):
        """按邮箱定位用户（用户名≠邮箱输入）同样命中。"""
        _register(mail_client, "bob", "bob@x.test")
        _activate("bob")
        resp = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "BOB@x.test"}
        )
        assert resp.status_code == 200
        assert resp.json()["delivery"] == "sent"
        token = _token_from_outbox()
        assert token
        resp = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "another-pass-2"},
        )
        assert resp.status_code == 200
        login = mail_client.post(
            "/api/auth/login", json={"username": "bob", "password": "another-pass-2"}
        )
        assert login.status_code == 200

    def test_token_expired(self, mail_client):
        """令牌过期 → reset_token_invalid。"""
        _register(mail_client, "carl", "carl@x.test")
        _activate("carl")
        mail_client.post("/api/auth/forgot-password", json={"identifier": "carl"})
        token = _token_from_outbox()
        db_module.db_execute(
            "UPDATE password_reset_tokens SET expires_at = ?",
            (db_module.utcnow_after(-10),),
        )
        resp = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "whatever-pass-1"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "reset_token_invalid"

    def test_token_replay_rejected(self, mail_client):
        """令牌单次有效：第二次使用返回 reset_token_invalid。"""
        _register(mail_client, "dora", "dora@x.test")
        _activate("dora")
        mail_client.post("/api/auth/forgot-password", json={"identifier": "dora"})
        token = _token_from_outbox()
        first = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "first-pass-123"},
        )
        assert first.status_code == 200
        second = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "second-pass-3"},
        )
        assert second.status_code == 400
        assert second.json()["detail"]["error"] == "reset_token_invalid"

    def test_weak_new_password(self, mail_client):
        """新密码过短：400 invalid_password，且不消耗令牌。"""
        _register(mail_client, "erin", "erin@x.test")
        _activate("erin")
        mail_client.post("/api/auth/forgot-password", json={"identifier": "erin"})
        token = _token_from_outbox()
        resp = mail_client.post(
            "/api/auth/reset-password", json={"token": token, "new_password": "short"}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_password"
        # 令牌未被消耗，修正后可用
        ok = mail_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "fixed-pass-12"},
        )
        assert ok.status_code == 200

    def test_target_debounce_five_minutes(self, mail_client, mail_env):
        """每邮箱 5 分钟 1 次：窗口内重复 forgot 不再投递。"""
        _register(mail_client, "frank", "frank@x.test")
        _activate("frank")
        first = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "frank"}
        )
        assert first.json()["delivery"] == "sent"
        _run(mail.process_outbox())
        n_tokens = db_module.db_count("SELECT COUNT(*) FROM password_reset_tokens")
        again = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "frank"}
        )
        # 对外语义不变，但不新建令牌、不重复投递
        assert again.json()["delivery"] == "sent"
        assert (
            db_module.db_count("SELECT COUNT(*) FROM password_reset_tokens") == n_tokens
        )
        _run(mail.process_outbox())
        assert len(mail_env["messages"]) == 1


class TestForgotDegradation:
    def test_user_without_email_generic_unavailable(self, mail_client):
        """无邮箱：通用 unavailable 响应，不落令牌、不发信。"""
        _register(mail_client, "grace")  # 未填邮箱
        _activate("grace")
        resp = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "grace"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "delivery": "unavailable"}
        assert db_module.db_count("SELECT COUNT(*) FROM password_reset_tokens") == 0

    def test_nonexistent_user_same_unavailable(self, mail_client):
        """账号不存在：与「无邮箱」同一响应语义（不泄露存在性）。"""
        resp = mail_client.post(
            "/api/auth/forgot-password", json={"identifier": "ghost-user"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "delivery": "unavailable"}

    def test_duplicate_emails_degrade(self, tmp_path, smtp_server):
        """存量重复邮箱（唯一索引缺位）：无法唯一定位 → unavailable。"""
        import sqlite3 as _sqlite3

        # 预置带重复邮箱的存量库：init_db 检测到冲突将跳过部分唯一索引
        db_path = tmp_path / "multiuser.db"
        conn = _sqlite3.connect(str(db_path))
        conn.executescript(db_module.SCHEMA)
        base = db_module.utcnow()
        for name in ("twin1", "twin2"):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, email, role, "
                "status, created_at, updated_at) VALUES (?, ?, 'h', ?, 'user', "
                "'active', ?, ?)",
                (name, name, "shared@x.test", base, base),
            )
        conn.commit()
        conn.close()
        build_mu_env(
            tmp_path,
            smtp_extra=smtp_enabled_extra(smtp_server["port"]),
        )
        indexes = {
            r["name"]
            for r in db_module.db_query_all(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert db_module.USERS_EMAIL_UNIQUE_INDEX not in indexes

        from fastapi.testclient import TestClient

        client = TestClient(_build_app())
        resp = client.post(
            "/api/auth/forgot-password", json={"identifier": "shared@x.test"}
        )
        assert resp.status_code == 200
        assert resp.json()["delivery"] == "unavailable"
        assert db_module.db_count("SELECT COUNT(*) FROM password_reset_tokens") == 0

    def test_smtp_disabled_degrades(self, mu_client, mu_env):
        """SMTP 未配置：入口后端兜底 unavailable（前端隐藏入口为第一道防线）。"""
        r = mu_client.post(
            "/api/auth/register",
            json={
                "username": "henry",
                "password": "password-123",
                "email": "henry@x.test",
            },
        )
        assert r.status_code == 200
        db_module.db_execute("UPDATE users SET status='active'")
        resp = mu_client.post("/api/auth/forgot-password", json={"identifier": "henry"})
        assert resp.status_code == 200
        assert resp.json()["delivery"] == "unavailable"

    def test_registration_works_silently_without_smtp(self, mu_client):
        """回归：未配置 SMTP 时注册等主流程完全不受影响。"""
        r = _register(mu_client, "iris", "iris@x.test")
        assert r.status_code == 200
        assert r.json()["user"]["status"] == "pending"


# ── 枚举防护与限流 ──────────────────────────────────────────────
class TestEnumerationAndRateLimit:
    def test_constant_duration_hit_vs_miss(self, mail_client):
        """命中与未命中的响应耗时对齐（argon2 占位校验拉平），语义统一。"""
        _register(mail_client, "ken", "ken@x.test")
        _activate("ken")

        def measure(identifier):
            start = time.perf_counter()
            resp = mail_client.post(
                "/api/auth/forgot-password", json={"identifier": identifier}
            )
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200
            return elapsed

        hit = measure("ken")
        miss = measure("ghost-user")
        assert hit >= 0.02 and miss >= 0.02  # 双路径都执行了占位 argon2 校验
        assert abs(hit - miss) < 0.5  # 耗时同数量级（CI 噪声余量）

    def test_forgot_ip_rate_limited(self, mail_client):
        """IP 维度滑动窗口限流：超限 429 rate_limited。"""
        _register(mail_client, "lena", "lena@x.test")
        for i in range(6):  # 上限 5/min，第 6 次触发
            resp = mail_client.post(
                "/api/auth/forgot-password", json={"identifier": f"probe-{i}"}
            )
            if i < 5:
                assert resp.status_code == 200
            else:
                assert resp.status_code == 429
                assert resp.json()["detail"]["error"] == "rate_limited"

    def test_rate_limited_requests_skip_timing_guard(self, mail_client, monkeypatch):
        """与 login 同序：限流先于 argon2 占位校验，被 429 的请求不消耗其 CPU。"""
        _register(mail_client, "lena2", "lena2@x.test")
        calls = {"n": 0}
        real = auth_core.check_credentials

        def counting(hashed, password):
            calls["n"] += 1
            return real(hashed, password)

        monkeypatch.setattr(auth_core, "check_credentials", counting)
        for i in range(6):  # 上限 5/min：前 5 次执行占位校验，第 6 次 429 前短路
            resp = mail_client.post(
                "/api/auth/forgot-password", json={"identifier": f"probe-{i}"}
            )
            if i < 5:
                assert resp.status_code == 200
            else:
                assert resp.status_code == 429
        assert calls["n"] == 5

    def test_reset_ip_rate_limited(self, mail_client):
        """reset 端点同口径 IP 限流。"""
        for i in range(11):  # 上限 10/min
            resp = mail_client.post(
                "/api/auth/reset-password",
                json={"token": f"bogus-{i}", "new_password": "password-123"},
            )
            if i < 10:
                assert resp.status_code == 400
            else:
                assert resp.status_code == 429


# ── 邮箱唯一性与个人资料 ────────────────────────────────────────
class TestEmailUniquenessAndProfile:
    def test_register_duplicate_email_conflict(self, mail_client):
        """注册重复邮箱（大小写不敏感）→ 409 email_taken。"""
        assert _register(mail_client, "adam", "Mail@X.com").status_code == 200
        dup = _register(mail_client, "brad", "mail@x.com")
        assert dup.status_code == 409
        assert dup.json()["detail"]["error"] == "email_taken"

    def test_profile_bind_and_change_requires_current_password(self, mail_client):
        """绑定/改绑需当前密码确认；错误密码 401 invalid_credentials。"""
        _register(mail_client, "cathy", "cathy@x.test")
        _activate("cathy")
        login = mail_client.post(
            "/api/auth/login", json={"username": "cathy", "password": "password-123"}
        )
        assert login.status_code == 200

        wrong = mail_client.put(
            "/api/auth/profile",
            json={"current_password": "wrong-pass-9", "email": "new@x.test"},
            headers=csrf(mail_client),
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"]["error"] == "invalid_credentials"

        bound = mail_client.put(
            "/api/auth/profile",
            json={"current_password": "password-123", "email": "new@x.test"},
            headers=csrf(mail_client),
        )
        assert bound.status_code == 200
        assert bound.json()["user"]["email"] == "new@x.test"

    def test_profile_email_conflict(self, mail_client):
        """profile 改绑他人邮箱 → 409 email_taken。"""
        _register(mail_client, "dave", "dave@x.test")
        _register(mail_client, "faye", "faye@x.test")
        _activate("faye")
        mail_client.post(
            "/api/auth/login", json={"username": "faye", "password": "password-123"}
        )
        resp = mail_client.put(
            "/api/auth/profile",
            json={"current_password": "password-123", "email": "DAVE@x.test"},
            headers=csrf(mail_client),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "email_taken"

    def test_profile_invalid_email_format(self, mail_client):
        """非法邮箱格式 → 400 invalid_email。"""
        _register(mail_client, "gina")
        _activate("gina")
        mail_client.post(
            "/api/auth/login", json={"username": "gina", "password": "password-123"}
        )
        resp = mail_client.put(
            "/api/auth/profile",
            json={"current_password": "password-123", "email": "not-an-email"},
            headers=csrf(mail_client),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_email"

    def test_profile_unbind_email(self, mail_client):
        """空字符串解绑邮箱。"""
        _register(mail_client, "hope", "hope@x.test")
        _activate("hope")
        mail_client.post(
            "/api/auth/login", json={"username": "hope", "password": "password-123"}
        )
        resp = mail_client.put(
            "/api/auth/profile",
            json={"current_password": "password-123", "email": ""},
            headers=csrf(mail_client),
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == ""


# ── 凭据/安全状态变更连带吊销未消费找回令牌 ────────────────────
class TestCredentialChangeRevokesResetTokens:
    """令牌签发后若发生重置密码 / 改绑邮箱 / 禁用账号，必须在 TTL 内失效。"""

    def _pending_token_count(self, uid: str) -> int:
        return db_module.db_count(
            "SELECT COUNT(*) FROM password_reset_tokens "
            "WHERE user_id = ? AND used_at IS NULL",
            (uid,),
        )

    def test_reset_password_revokes_other_pending_tokens(self, mu_client):
        """重置密码后：同用户其余未消费令牌立即失效，不可再次重置。"""
        assert _register(mu_client, "mia", "mia@x.test").status_code == 200
        uid = _activate("mia")
        first, _ = auth_core.create_password_reset_token(uid)
        second, _ = auth_core.create_password_reset_token(uid)

        ok = mu_client.post(
            "/api/auth/reset-password",
            json={"token": first, "new_password": "fresh-pass-11"},
        )
        assert ok.status_code == 200

        # second 签发于重置之前且未被消费：必须被连带吊销，
        # 防止攻击者凭 TTL 内的旧令牌再次重置密码夺回控制权
        replay = mu_client.post(
            "/api/auth/reset-password",
            json={"token": second, "new_password": "second-pass-22"},
        )
        assert replay.status_code == 400
        assert replay.json()["detail"]["error"] == "reset_token_invalid"
        assert self._pending_token_count(uid) == 0

    def test_set_user_email_revokes_pending_tokens(self, mu_client):
        """改绑邮箱后：改绑前签发的找回令牌立即失效。"""
        assert _register(mu_client, "noa", "noa@x.test").status_code == 200
        uid = _activate("noa")
        login = mu_client.post(
            "/api/auth/login", json={"username": "noa", "password": "password-123"}
        )
        assert login.status_code == 200
        token, _expires = auth_core.create_password_reset_token(uid)

        bound = mu_client.put(
            "/api/auth/profile",
            json={"current_password": "password-123", "email": "noa-new@x.test"},
            headers=csrf(mu_client),
        )
        assert bound.status_code == 200
        assert bound.json()["user"]["email"] == "noa-new@x.test"

        stale = mu_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "hijack-pass-1"},
        )
        assert stale.status_code == 400
        assert stale.json()["detail"]["error"] == "reset_token_invalid"
        assert self._pending_token_count(uid) == 0

    def test_disable_account_revokes_pending_tokens(self, mu_client):
        """禁用账号：未消费找回令牌一并吊销（复启用后旧令牌仍不可用）。"""
        assert _register(mu_client, "pia", "pia@x.test").status_code == 200
        uid = _activate("pia")
        token, _expires = auth_core.create_password_reset_token(uid)

        auth_core.set_user_status(uid, "disabled")
        stale = mu_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "reactivate-pass-1"},
        )
        assert stale.status_code == 400
        assert stale.json()["detail"]["error"] == "reset_token_invalid"

        # 令牌行已被删除而非仅被状态拦截：复启用后同样无效
        auth_core.set_user_status(uid, "active")
        still_stale = mu_client.post(
            "/api/auth/reset-password",
            json={"token": token, "new_password": "reactivate-pass-2"},
        )
        assert still_stale.status_code == 400
        assert still_stale.json()["detail"]["error"] == "reset_token_invalid"

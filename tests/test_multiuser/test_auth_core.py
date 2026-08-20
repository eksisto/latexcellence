"""多用户模式 P1：认证核心纯函数测试（哈希/会话/引导/限流）。"""

import os
from unittest import mock

import pytest

from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module

VALID_PW = "password-123"


class TestPasswordHashing:
    def test_roundtrip(self, mu_env):
        h = auth_core.hash_password(VALID_PW)
        assert h != VALID_PW
        assert auth_core.verify_password(VALID_PW, h) is True

    def test_wrong_password_fails(self, mu_env):
        h = auth_core.hash_password(VALID_PW)
        assert auth_core.verify_password("wrong-pass", h) is False

    def test_garbage_hash_fails(self, mu_env):
        assert auth_core.verify_password(VALID_PW, "not-a-valid-hash") is False


class TestValidation:
    def test_valid_usernames(self):
        for name in ("alice", "Alice_01", "李雷", "li-lei", "a@b"):
            assert auth_core.validate_username(name) is None

    def test_invalid_usernames(self):
        for name in ("a", "", "x" * 33, "has space", "path/name", "bad\\name"):
            assert auth_core.validate_username(name) is not None

    def test_password_too_short(self):
        assert auth_core.validate_password("short123") is None  # 8 位合法
        assert auth_core.validate_password("short") == (
            "Password must be at least 8 characters"
        )


class TestUserManagement:
    def test_create_get(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW, email="a@x.io")
        assert user["status"] == "pending"
        assert user["role"] == "user"
        assert user["email"] == "a@x.io"
        assert (
            auth_core.get_user_by_username("ALICE")["id"] == user["id"]
        )  # 大小写不敏感
        assert auth_core.get_user_by_id(user["id"])["username"] == "alice"

    def test_duplicate_username(self, mu_env):
        auth_core.create_user("alice", VALID_PW)
        with pytest.raises(ValueError):
            auth_core.create_user("Alice", "another-pass-1")

    def test_set_status_revokes_sessions(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_status(user["id"], "active")
        token = auth_core.create_session(user["id"])
        assert auth_core.validate_session(token) is not None
        auth_core.set_user_status(user["id"], "disabled")
        # 禁用后会话即时失效
        assert auth_core.validate_session(token) is None
        auth_core.set_user_status(user["id"], "active")
        # 重新启用（但旧会话已吊销）
        assert auth_core.validate_session(token) is None

    def test_reset_password(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_status(user["id"], "active")
        token = auth_core.create_session(user["id"])
        assert auth_core.validate_session(token) is not None
        auth_core.reset_password(user["id"], "new-pass-123")
        assert auth_core.validate_session(token) is None
        assert auth_core.verify_password(
            "new-pass-123", auth_core.get_user_by_id(user["id"])["password_hash"]
        )
        assert not auth_core.verify_password(
            VALID_PW, auth_core.get_user_by_id(user["id"])["password_hash"]
        )

    def test_quota_override(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_quota(user["id"], 100, None)
        got = auth_core.get_user_by_id(user["id"])
        assert got["project_quota_bytes"] == 100
        assert got["derived_quota_bytes"] is None


class TestSessionLifecycle:
    def test_create_validate_delete(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_status(user["id"], "active")
        token = auth_core.create_session(user["id"], remember=True)
        # 数据库只存哈希，不存明文
        assert token != ""
        rows = db_module.db_query_all("SELECT token_hash FROM sessions")
        assert all(r["token_hash"] != token for r in rows)
        got = auth_core.validate_session(token)
        assert got is not None and got["id"] == user["id"]
        assert auth_core.delete_session(token) is True
        assert auth_core.validate_session(token) is None

    def test_expired_session_invalid(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_status(user["id"], "active")
        token = auth_core.create_session(user["id"])
        # 直接把过期时间改到过去
        db_module.db_execute(
            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
        assert auth_core.validate_session(token) is None

    def test_pending_user_session_invalid(self, mu_env):
        """未审批用户即使有会话也无法通过校验（状态必须 active）。"""
        user = auth_core.create_user("alice", VALID_PW)  # status=pending
        token = auth_core.create_session(user["id"])
        assert auth_core.validate_session(token) is None

    def test_revoke_user_sessions(self, mu_env):
        user = auth_core.create_user("alice", VALID_PW)
        auth_core.set_user_status(user["id"], "active")
        t1 = auth_core.create_session(user["id"])
        t2 = auth_core.create_session(user["id"])
        assert auth_core.revoke_user_sessions(user["id"]) == 2
        assert auth_core.validate_session(t1) is None
        assert auth_core.validate_session(t2) is None


class TestBootstrapAdmin:
    def test_creates_from_env(self, mu_env):
        with mock.patch.dict(
            os.environ,
            {
                "LATEXCELLENCE_ADMIN_USERNAME": "root",
                "LATEXCELLENCE_ADMIN_PASSWORD": "root-pass-123",
            },
        ):
            admin = auth_core.ensure_bootstrap_admin()
        assert admin is not None
        assert admin["role"] == "admin"
        assert admin["status"] == "active"
        assert auth_core.get_user_by_username("root")["role"] == "admin"

    def test_no_env_returns_none(self, mu_env, caplog):
        with mock.patch.dict(os.environ, {}, clear=True):
            admin = auth_core.ensure_bootstrap_admin()
        assert admin is None
        assert "create-admin" in caplog.text

    def test_skips_when_users_exist(self, mu_env):
        auth_core.create_user("someone", "password-123")
        with mock.patch.dict(
            os.environ,
            {
                "LATEXCELLENCE_ADMIN_USERNAME": "root",
                "LATEXCELLENCE_ADMIN_PASSWORD": "root-pass-123",
            },
        ):
            admin = auth_core.ensure_bootstrap_admin()
        assert admin is None


class TestLoginRateLimit:
    def test_limits_per_username_ip_pair(self, mu_env):
        # 配置限流 5 次/分钟
        for i in range(5):
            assert auth_core.login_rate_limited("alice", "127.0.0.1") is False
        assert auth_core.login_rate_limited("alice", "127.0.0.1") is True
        # 用户名维度已达上限：换 IP 也被拒（防多出口 IP 放大单账号爆破）
        assert auth_core.login_rate_limited("alice", "10.0.0.2") is True
        # 其他用户名不受影响
        assert auth_core.login_rate_limited("bob", "127.0.0.1") is False


class TestTimingEqualizer:
    """登录恒定时长校验：用户不存在时也执行一次 argon2 验证（防时序枚举用户名）。"""

    def test_missing_user_still_verifies(self, mu_env, monkeypatch):
        calls = []
        real_verify = auth_core.verify_password

        def spy(password, password_hash):
            calls.append(password_hash)
            return real_verify(password, password_hash)

        monkeypatch.setattr(auth_core, "verify_password", spy)
        assert auth_core.check_credentials(None, "whatever-pass") is False
        # hashed=None（用户不存在）仍应执行一次验证（针对占位哈希）
        assert len(calls) == 1

    def test_existing_user_verifies_once(self, mu_env, monkeypatch):
        user = auth_core.create_user("alice", VALID_PW)
        calls = []
        real_verify = auth_core.verify_password

        def spy(password, password_hash):
            calls.append(True)
            return real_verify(password, password_hash)

        monkeypatch.setattr(auth_core, "verify_password", spy)
        hashed = auth_core.get_user_by_id(user["id"])["password_hash"]
        assert auth_core.check_credentials(hashed, VALID_PW) is True
        assert len(calls) == 1

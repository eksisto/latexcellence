"""多用户模式 P1：CLI 管理工具（python -m backend.manage）测试。"""

import pytest

from backend.core.multiuser import auth as auth_core
from backend.manage import main


class TestCli:
    def test_create_admin(self, mu_env_factory):
        env = mu_env_factory()
        config = str(env["config_path"])
        main(
            ["--config", config, "create-admin", "root", "--password", "root-pass-123"]
        )
        user = auth_core.get_user_by_username("root")
        assert user is not None
        assert user["role"] == "admin"
        assert user["status"] == "active"

    def test_create_admin_existing_upgrades(self, mu_env_factory):
        env = mu_env_factory()
        config = str(env["config_path"])
        auth_core.create_user("root", "password-123", role="user", status="active")
        main(
            ["--config", config, "create-admin", "root", "--password", "new-pass-1234"]
        )
        user = auth_core.get_user_by_username("root")
        assert user["role"] == "admin"

    def test_reset_password(self, mu_env_factory):
        env = mu_env_factory()
        config = str(env["config_path"])
        user = auth_core.create_user("alice", "password-123")
        main(
            [
                "--config",
                config,
                "reset-password",
                "alice",
                "--password",
                "fresh-pass-1",
            ]
        )
        updated = auth_core.get_user_by_id(user["id"])
        assert auth_core.verify_password("fresh-pass-1", updated["password_hash"])

    def test_set_status_approves(self, mu_env_factory):
        env = mu_env_factory()
        config = str(env["config_path"])
        user = auth_core.create_user("alice", "password-123")
        main(["--config", config, "set-status", "alice", "active"])
        assert auth_core.get_user_by_id(user["id"])["status"] == "active"

    def test_list_users_prints(self, mu_env_factory, capsys):
        env = mu_env_factory()
        config = str(env["config_path"])
        main(
            ["--config", config, "create-admin", "root", "--password", "root-pass-123"]
        )
        main(["--config", config, "list-users"])
        assert "root" in capsys.readouterr().out

    def test_disabled_mode_rejected(self, mu_env_factory):
        env = mu_env_factory(enabled=False)
        config = str(env["config_path"])
        with pytest.raises(SystemExit) as exc:
            main(["--config", config, "list-users"])
        assert exc.value.code == 1

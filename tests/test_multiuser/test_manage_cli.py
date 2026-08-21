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

    # ── P6：migrate-legacy ────────────────────────────────────────────

    def test_migrate_legacy_copies_into_admin_space(self, mu_env_factory, tmp_path):
        env = mu_env_factory()
        config = str(env["config_path"])

        # 把会话类资源目录指向 tmp_path，写入 config.json（CLI 会重新加载文件）
        import json

        with open(config, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("features", {})
        features = data["features"]
        features.setdefault("writing_chat", {})["sessions_dir"] = str(
            tmp_path / "writing_chat_sessions"
        )
        features.setdefault("style_modify", {}).setdefault("agent", {})[
            "sessions_dir"
        ] = str(tmp_path / "style_modify_sessions")
        features.setdefault("template_gen", {})["output_dir"] = str(
            tmp_path / "templates"
        )
        features.setdefault("plot_gen", {})["output_dir"] = str(tmp_path / "plots")
        features.setdefault("bib_manager", {})["format_configs_dir"] = str(
            tmp_path / "formats"
        )
        features.setdefault("expression_opt", {})["custom_prompts_dir"] = str(
            tmp_path / "prompts" / "expression"
        )
        with open(config, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        main(
            ["--config", config, "create-admin", "root", "--password", "root-pass-123"]
        )
        admin = auth_core.get_user_by_username("root")

        # 构造存量全局数据
        (tmp_path / "writing_chat_sessions" / "scope1").mkdir(parents=True)
        (tmp_path / "writing_chat_sessions" / "scope1" / "x.json").write_text(
            "{}", encoding="utf-8"
        )
        (tmp_path / "templates" / "tpl").mkdir(parents=True)
        (tmp_path / "templates" / "tpl" / "main.tex").write_text(
            r"\documentclass{article}", encoding="utf-8"
        )
        (tmp_path / "plots").mkdir(parents=True)
        (tmp_path / "plots" / "p1" / "scripts").mkdir(parents=True)
        (tmp_path / "plots" / "p1" / "scripts" / "run.py").write_text(
            "print(1)", encoding="utf-8"
        )

        main(["--config", config, "migrate-legacy"])
        admin_root = env["users_root"] / admin["id"]
        assert (admin_root / "writing_chat_sessions" / "scope1" / "x.json").exists()
        assert (admin_root / "templates" / "tpl" / "main.tex").exists()
        assert (admin_root / "plots" / "p1" / "scripts" / "run.py").exists()

    def test_migrate_legacy_requires_admin(self, mu_env_factory, capsys):
        env = mu_env_factory()
        config = str(env["config_path"])
        with pytest.raises(SystemExit) as exc:
            main(["--config", config, "migrate-legacy"])
        assert exc.value.code == 1
        assert "admin" in capsys.readouterr().err.lower()

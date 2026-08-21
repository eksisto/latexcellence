"""
多用户模式 P6：迁移与存量隔离测试。

覆盖：
    1. migrate-legacy 迁移工具：幂等、不覆盖新数据、报告校验、dry-run
    2. 会话类资源归属校验：写作会话 / style_modify 会话 / 模板 / 绘图
       在多用户模式下按当前用户隔离到 <users_root>/<user_id>/<subdir>/
    3. 存量全局路径回归：单用户模式下四类资源仍落到存量全局目录
"""

from pathlib import Path

import pytest

from backend.core import config as cfg_module
from backend.core.multiuser import migrate as migrate_core
from backend.core.multiuser import userspace
from tests.test_multiuser.conftest import build_mu_env, _reset_config_state


@pytest.fixture
def default_config():
    """加载项目默认单用户配置（multi_user.enabled=false），测试后复位。"""
    cfg_module.load_config("config.json")
    yield cfg_module.get_config()
    _reset_config_state()


# ── fixtures ──────────────────────────────────────────────────────────────
@pytest.fixture
def legacy_data(tmp_path):
    """构造存量全局数据目录（对应 config 中的各 sessions_dir / output_dir）。"""
    root = tmp_path

    def _mk(rel: str, files: list[str]) -> None:
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        for name in files:
            f = d / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"content-{rel}-{name}", encoding="utf-8")

    _mk("writing_chat_sessions/scope1", ["abc.json"])
    _mk("writing_chat_sessions/scope2", ["def.json"])
    _mk("style_modify_sessions", ["sess-a/meta.json", "sess-a/baseline/main.tex"])
    _mk("templates", ["tpl1/main.tex"])
    _mk("plots", ["plot1/scripts/run.py"])
    _mk("formats", ["format1.json"])
    _mk("prompts/expression", ["academic.txt"])
    _mk("prompts/writing_chat", ["assistant1.txt"])
    return root


@pytest.fixture
def mu_env_with_legacy(legacy_data, tmp_path, monkeypatch):
    """多用户环境 + 指向 legacy_data 的存量目录配置 + 管理员账号。"""
    from backend.core.multiuser import auth as auth_core
    from backend.core.multiuser.db import close_db

    env = build_mu_env(tmp_path)
    # 将四类会话资源目录指向临时存量目录（覆盖 config 默认值）
    cfg = cfg_module.get_config()
    cfg.features.writing_chat.sessions_dir = str(legacy_data / "writing_chat_sessions")
    cfg.features.style_modify.agent.sessions_dir = str(
        legacy_data / "style_modify_sessions"
    )
    cfg.features.template_gen.output_dir = str(legacy_data / "templates")
    cfg.features.plot_gen.output_dir = str(legacy_data / "plots")
    cfg.features.bib_manager.format_configs_dir = str(legacy_data / "formats")
    cfg.features.expression_opt.custom_prompts_dir = str(
        legacy_data / "prompts" / "expression"
    )
    cfg.features.writing_chat.custom_prompts_dir = str(
        legacy_data / "prompts" / "writing_chat"
    )
    admin = auth_core.create_user(
        "admin", "admin-pass-123", role="admin", status="active"
    )
    env["admin"] = admin
    yield env
    close_db()
    _reset_config_state()


# ── migrate-legacy 迁移工具 ───────────────────────────────────────────────


class TestMigrateLegacy:
    def test_copies_all_dirs_into_admin_space(self, mu_env_with_legacy):
        admin = mu_env_with_legacy["admin"]
        report = migrate_core.migrate_legacy(admin["id"])

        assert report["total_copied"] > 0
        assert report["all_verified"] is True
        names = {d["name"]: d for d in report["dirs"]}
        assert set(names) == {
            "writing_chat_sessions",
            "style_modify_sessions",
            "templates",
            "plots",
            "formats",
            "prompts",
        }
        # 目标目录已存在且文件被复制
        admin_root = userspace.user_root(admin["id"])
        assert (admin_root / "writing_chat_sessions" / "scope1" / "abc.json").exists()
        assert (admin_root / "style_modify_sessions" / "sess-a" / "meta.json").exists()
        assert (admin_root / "templates" / "tpl1" / "main.tex").exists()
        assert (admin_root / "plots" / "plot1" / "scripts" / "run.py").exists()
        assert (admin_root / "formats" / "format1.json").exists()
        assert (admin_root / "prompts" / "expression" / "academic.txt").exists()

    def test_idempotent_no_overwrite(self, mu_env_with_legacy, legacy_data):
        admin = mu_env_with_legacy["admin"]
        migrate_core.migrate_legacy(admin["id"])
        # 第二次运行：全部跳过（已存在），不产生重复/覆盖
        second = migrate_core.migrate_legacy(admin["id"])
        assert second["total_copied"] == 0
        assert (
            second["total_skipped"]
            == second["total_copied"] + second["total_skipped"]
            == sum(d["files"] for d in second["dirs"] if d["status"] == "copied")
        )
        assert second["all_verified"] is True

    def test_new_data_in_target_not_overwritten(self, mu_env_with_legacy):
        admin = mu_env_with_legacy["admin"]
        # 先在管理员空间放一个"迁移后新产生"的同名文件
        target = userspace.user_root(admin["id"]) / "templates" / "tpl1" / "main.tex"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("NEW-DATA", encoding="utf-8")

        report = migrate_core.migrate_legacy(admin["id"])
        # 已存在的目标文件被跳过（保留新数据）
        assert target.read_text(encoding="utf-8") == "NEW-DATA"
        tpl = next(d for d in report["dirs"] if d["name"] == "templates")
        assert tpl["skipped"] >= 1

    def test_dry_run_writes_nothing(self, mu_env_with_legacy):
        admin = mu_env_with_legacy["admin"]
        admin_root = userspace.user_root(admin["id"])
        before = admin_root.exists()
        report = migrate_core.migrate_legacy(admin["id"], dry_run=True)
        assert report["dry_run"] is True
        admin_root = userspace.user_root(admin["id"])
        # dry-run 不创建任何目标内容
        files_created = (
            list((admin_root / "writing_chat_sessions").rglob("*"))
            if (admin_root / "writing_chat_sessions").exists()
            else []
        )
        assert files_created == []
        # 源目录保留
        assert (
            mu_env_with_legacy["tmp_path"] / "templates" / "tpl1" / "main.tex"
        ).exists()

    def test_create_admin_required(self, mu_env_factory):
        env = mu_env_factory()
        # 无管理员时 find_first_admin 返回 None
        from backend.core.multiuser.migrate import find_first_admin

        assert find_first_admin() is None

    def test_legacy_source_dirs_mapping(self, mu_env_with_legacy):
        sources = migrate_core.legacy_source_dirs()
        assert set(sources) == {
            "writing_chat_sessions",
            "style_modify_sessions",
            "templates",
            "plots",
            "formats",
            "prompts",
        }

    def test_failed_copy_leaves_no_partial_target(
        self, mu_env_with_legacy, monkeypatch
    ):
        """复制中断（磁盘满等）不在目标侧留下截断文件，重跑可完整补齐。"""
        admin = mu_env_with_legacy["admin"]

        def boom(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(migrate_core.os, "replace", boom)
        with pytest.raises(OSError):
            migrate_core.migrate_legacy(admin["id"])
        monkeypatch.undo()

        # 目标侧无截断文件、无临时残留
        target = userspace.user_root(admin["id"]) / "templates" / "tpl1" / "main.tex"
        assert not target.exists()
        leftovers = [
            p
            for p in userspace.user_root(admin["id"]).rglob("*")
            if ".migrate-tmp" in p.name
        ]
        assert leftovers == []

        # 重跑成功且校验通过，无临时残留
        report = migrate_core.migrate_legacy(admin["id"])
        assert report["all_verified"] is True
        assert target.read_text(encoding="utf-8") == "content-templates-tpl1/main.tex"
        leftovers = [
            p
            for p in userspace.user_root(admin["id"]).rglob("*")
            if ".migrate-tmp" in p.name
        ]
        assert leftovers == []

    def test_truncated_target_reported_as_mismatch(self, mu_env_with_legacy):
        """历史中断残留的截断目标文件：不覆盖、校验失败并进报告（不误报通过）。"""
        admin = mu_env_with_legacy["admin"]
        target = userspace.user_root(admin["id"]) / "templates" / "tpl1" / "main.tex"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("trunc", encoding="utf-8")

        report = migrate_core.migrate_legacy(admin["id"])
        tpl = next(d for d in report["dirs"] if d["name"] == "templates")
        # 幂等跳过（不覆盖），但大小校验失败且给出样本
        assert tpl["skipped"] >= 1
        assert target.read_text(encoding="utf-8") == "trunc"
        assert tpl["verify"]["mismatch"] == 1
        assert "tpl1/main.tex" in tpl["verify"]["mismatch_samples"]
        assert report["all_verified"] is False

    def test_stale_temp_files_swept(self, mu_env_with_legacy):
        """被强杀进程遗留的过期临时文件在下次运行开头被清扫。"""
        import os
        import time

        admin = mu_env_with_legacy["admin"]
        stale = (
            userspace.user_root(admin["id"])
            / "templates"
            / "tpl1"
            / "main.tex.migrate-tmp-999"
        )
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("partial", encoding="utf-8")
        old = time.time() - 7200
        os.utime(stale, (old, old))

        migrate_core.migrate_legacy(admin["id"])
        assert not stale.exists()


# ── 会话类资源归属校验（按用户隔离） ──────────────────────────────────────


class TestDerivedDirIsolation:
    def test_single_user_mode_uses_legacy_root(self, default_config):
        """单用户模式：user_resource_root 返回存量全局路径（回归保证）。"""
        root = userspace.user_resource_root("templates", Path("/legacy/templates"))
        assert root == Path("/legacy/templates")

    def test_multiuser_routes_to_user_dir(self, mu_env, monkeypatch):
        """多用户模式：writing_chat 会话落到当前用户衍生目录。"""
        user_id = "user-aaaaaaaa"
        userspace.ensure_user_dirs(user_id)
        userspace.set_current_user_id(user_id)
        try:
            legacy = Path("/legacy/writing_chat_sessions")
            root = userspace.user_resource_root("writing_chat_sessions", legacy)
            assert root == userspace.user_root(user_id) / "writing_chat_sessions"
            assert root.is_dir()
        finally:
            userspace.set_current_user_id(None)

    def test_multiuser_isolates_two_users(self, mu_env, monkeypatch):
        """两个用户分别拥有各自的衍生数据目录，互不串扰。"""
        u1 = "user-one"
        u2 = "user-two"
        userspace.ensure_user_dirs(u1)
        userspace.ensure_user_dirs(u2)

        userspace.set_current_user_id(u1)
        try:
            root1 = userspace.user_resource_root("plots", Path("/legacy/plots"))
            (root1 / "share").mkdir(parents=True, exist_ok=True)
        finally:
            userspace.set_current_user_id(None)

        userspace.set_current_user_id(u2)
        try:
            root2 = userspace.user_resource_root("plots", Path("/legacy/plots"))
            # 用户 2 的目录下看不到用户 1 的会话数据
            assert not (root2 / "share").exists()
            assert root1 != root2
        finally:
            userspace.set_current_user_id(None)

    def test_no_user_context_falls_back_to_legacy(self, mu_env):
        """多用户模式但未注入用户上下文（绕过路由直调）时回退存量路径（不崩溃）。"""
        userspace.set_current_user_id(None)
        root = userspace.user_resource_root("templates", Path("/legacy/templates"))
        assert root == Path("/legacy/templates")

    def test_unknown_subdir_rejected(self, mu_env):
        userspace.set_current_user_id("user-x")
        try:
            with pytest.raises(ValueError):
                userspace.user_resource_root("evil", Path("/legacy/evil"))
        finally:
            userspace.set_current_user_id(None)


# ── 存量全局路径回归（单用户行为不变） ────────────────────────────────────


class TestLegacyRegression:
    def test_writing_chat_root_single_user(self, tmp_path, monkeypatch, default_config):
        """单用户模式 writing_chat._sessions_dir 仍返回存量全局路径。"""
        from backend.modules.writing_chat.context import ConversationContext

        monkeypatch.setattr(
            default_config.features.writing_chat, "sessions_dir", str(tmp_path)
        )
        d = ConversationContext._sessions_dir("scope1")
        assert d == (tmp_path / "scope1")
        assert d.exists()

    def test_plot_output_dir_single_user(self, tmp_path, monkeypatch, default_config):
        from backend.modules.plot_gen.agent import get_plot_output_dir

        monkeypatch.setattr(
            default_config.features.plot_gen, "output_dir", str(tmp_path)
        )
        assert get_plot_output_dir() == tmp_path

    def test_template_output_root_single_user(
        self, tmp_path, monkeypatch, default_config
    ):
        from backend.modules.template_gen.service import TemplateGenService

        monkeypatch.setattr(
            default_config.features.template_gen, "output_dir", str(tmp_path)
        )
        out = TemplateGenService()._output_root(default_config)
        assert out == tmp_path

    def test_style_modify_root_single_user(self, tmp_path, monkeypatch, default_config):
        from backend.modules.style_modify.session import get_sessions_root

        monkeypatch.setattr(
            default_config.features.style_modify.agent, "sessions_dir", str(tmp_path)
        )
        assert get_sessions_root(default_config).resolve() == tmp_path.resolve()


# ── 路由依赖注入（current_user_context） ──────────────────────────────────


class TestUserContextDependency:
    """验证路由依赖能把当前用户注入到请求级上下文（会话类资源归属校验的接线）。"""

    def test_dependency_injects_user(self, mu_env):
        """多用户模式下经依赖注入后，当前用户上下文被设置。"""
        import asyncio

        from backend.core.multiuser.dependencies import current_user_context
        from backend.core.multiuser import userspace as us

        async def _with_user():
            fake_user = {
                "id": "user-alice",
                "username": "alice",
                "role": "user",
                "status": "active",
            }
            await current_user_context(user=fake_user)
            assert us.get_current_user_id() == "user-alice"
            # 资源根随之定向到对应用户衍生目录
            root = us.user_resource_root("plots", Path("/legacy/plots"))
            assert root == us.user_root("user-alice") / "plots"
            userspace.set_current_user_id(None)

        asyncio.run(_with_user())

    def test_dependency_injects_none_when_anonymous(self, mu_env):
        """未登录（单用户/匿名）时注入 None，资源根回退存量全局路径。"""
        import asyncio

        from backend.core.multiuser.dependencies import current_user_context
        from backend.core.multiuser import userspace as us

        async def _anonymous():
            await current_user_context(user=None)
            assert us.get_current_user_id() is None
            root = us.user_resource_root("templates", Path("/legacy/templates"))
            assert root == Path("/legacy/templates")

        asyncio.run(_anonymous())


class TestWritingChatIsolationEndToEnd:
    """经真实路由 + 认证依赖验证写作会话按用户隔离。"""

    def test_list_sessions_scoped_to_user(self, mu_env):
        from fastapi.testclient import TestClient

        from backend.core.multiuser import auth as auth_core
        from backend.core.multiuser import userspace as us
        from backend.modules.writing_chat.router import router as wc_router

        app = mu_env["_app"] if "_app" in mu_env else None
        if app is None:
            from fastapi import FastAPI

            app = FastAPI()
            app.include_router(wc_router)
            mu_env["_app"] = app

        user = auth_core.create_user("alice", "alice-pass-123", status="active")
        token = auth_core.create_session(user["id"])
        us.ensure_user_dirs(user["id"])

        # 预置属于当前用户的写作会话文件
        user_sessions = us.user_root(user["id"]) / "writing_chat_sessions" / "scopeA"
        user_sessions.mkdir(parents=True, exist_ok=True)
        (user_sessions / "session-1.json").write_text(
            '{"id":"scopeA:aaa","created_at":1.0,"last_updated_at":1.0,'
            '"cumulative_tokens":0,"file_path":"main.tex","title":"t1",'
            '"messages":[]}',
            encoding="utf-8",
        )

        client = TestClient(app)
        client.cookies.set("latexcellence_session", token)
        r = client.get("/api/writing-chat/sessions")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert any(s["id"] == "scopeA:aaa" for s in sessions)

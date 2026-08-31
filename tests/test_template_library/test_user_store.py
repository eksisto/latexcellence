"""user_store CRUD 测试（多用户 / 单用户双模式 + 用户隔离 + 配额并入）。"""

import pytest

from backend.core.multiuser import quota, userspace
from backend.modules.template_library import user_store
from tests.test_template_library.conftest import (
    make_manifest,
    make_user_template,
)


class TestMultiuserStore:
    def test_library_root_scoped_to_user_space(self, env):
        root = user_store.library_root(env["alice"]["id"])
        assert root == (
            userspace.user_root(env["alice"]["id"]) / user_store.TEMPLATE_LIBRARY_SUBDIR
        )

    def test_create_list_get_update_delete(self, env):
        alice = env["alice"]["id"]
        staging_root = user_store.library_root(alice).parent / "_stage"
        staging_root.mkdir(parents=True, exist_ok=True)
        (staging_root / "main.tex").write_text(
            r"\documentclass{article}x", encoding="utf-8"
        )
        manifest = make_manifest()
        manifest["id"] = "ignored"
        from backend.modules.template_library.models import TemplateManifest

        summary = user_store.create_template(
            alice, staging_root, TemplateManifest.model_validate(manifest)
        )
        assert summary.kind == "user"
        assert summary.id != "ignored"
        assert (user_store.template_dir(alice, summary.id) / "main.tex").is_file()
        # manifest 已写盘且 id 与目录一致
        on_disk = user_store.get_template(alice, summary.id)
        assert on_disk is not None
        assert on_disk.name == "我的测试模板"
        assert [t.id for t in user_store.list_templates(alice)] == [summary.id]

        # 改名
        updated = user_store.update_meta(alice, summary.id, name="新名字")
        assert updated.name == "新名字"

        # 删除
        user_store.delete_template(alice, summary.id)
        assert user_store.get_template(alice, summary.id) is None
        assert not user_store.template_dir(alice, summary.id).exists()

    def test_update_missing_template_raises(self, env):
        with pytest.raises(FileNotFoundError):
            user_store.update_meta(env["alice"]["id"], "nonexistent", name="x")

    def test_delete_missing_template_raises(self, env):
        with pytest.raises(FileNotFoundError):
            user_store.delete_template(env["alice"]["id"], "nonexistent")

    def test_user_isolation(self, env):
        alice, bob = env["alice"]["id"], env["bob"]["id"]
        tpl = make_user_template(alice, {"main.tex": "x"})
        assert user_store.get_template(bob, tpl["id"]) is None
        assert user_store.list_templates(bob) == []

    def test_unsafe_id_rejected(self, env):
        with pytest.raises(ValueError):
            user_store.template_dir(env["alice"]["id"], "../escape")


class TestQuotaIntegration:
    def test_template_library_counted_in_templates_category(self, env):
        """template_library 目录并入「模板」衍生类别扫描（不新增类别）。"""
        alice = env["alice"]["id"]
        make_user_template(alice, {"main.tex": "x" * 2048})
        size = quota._scan_derived_dir(alice, "templates")
        assert size >= 2048
        breakdown = quota.derived_usage_breakdown(alice)
        assert set(breakdown) == set(userspace.DERIVED_SUBDIRS)
        assert breakdown["templates"] >= 2048

    def test_self_service_cleanup_clears_template_library(self, env):
        alice = env["alice"]["id"]
        tpl = make_user_template(alice, {"main.tex": "x"})
        quota.cleanup_derived_category(alice, "templates")
        assert not user_store.template_dir(alice, tpl["id"]).exists()
        assert quota.derived_usage_breakdown(alice)["templates"] == 0


class TestSingleUserStore:
    def test_library_root_from_config(self, su_env):
        root = user_store.library_root(None)
        assert str(su_env / "library") in str(root)

    def test_crud_roundtrip(self, su_env):
        from backend.modules.template_library.models import TemplateManifest

        staging = su_env / "stage"
        staging.mkdir()
        (staging / "main.tex").write_text("x", encoding="utf-8")
        summary = user_store.create_template(
            None, staging, TemplateManifest.model_validate(make_manifest())
        )
        assert summary.kind == "user"
        assert user_store.get_template(None, summary.id) is not None
        user_store.delete_template(None, summary.id)
        assert user_store.get_template(None, summary.id) is None

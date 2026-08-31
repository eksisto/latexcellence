"""builtin_store 发现与共享文件原语测试。"""

import pytest

from backend.modules.template_library import builtin_store
from backend.modules.template_library.builtin_store import (
    BUILTIN_ROOT,
    get_template,
    iter_template_files,
    list_templates,
    resolve_template_file,
    validate_template_id,
)


class TestDiscovery:
    def test_discovers_all_builtin_templates(self):
        dir_ids = sorted(
            d.name
            for d in BUILTIN_ROOT.iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()
        )
        found = list_templates()
        assert [t.id for t in found] == dir_ids
        assert all(t.kind == "builtin" for t in found)
        assert all(t.file_count >= 1 for t in found)

    def test_get_template_by_id(self):
        summary = get_template("paper-cn")
        assert summary is not None
        assert summary.category == "paper"
        assert summary.main_file == "main.tex"

    def test_get_template_missing(self):
        assert get_template("no-such-template") is None

    def test_builtin_files_exclude_meta(self):
        files = iter_template_files(
            BUILTIN_ROOT / "paper-cn", exclude=builtin_store.BUILTIN_META_EXCLUDES
        )
        paths = [f.path for f in files]
        assert "main.tex" in paths
        assert all(not p.startswith("assets/") for p in paths)
        assert "manifest.json" not in paths


class TestIdValidation:
    @pytest.mark.parametrize("bad", ["", "../evil", "a/b", "a\\b", "..", ".hidden"])
    def test_unsafe_ids_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_template_id(bad)

    def test_safe_ids_accepted(self):
        assert validate_template_id("paper-cn") == "paper-cn"
        assert validate_template_id("a" * 32) == "a" * 32


class TestResolveFile:
    def test_resolves_valid_file(self):
        resolved = resolve_template_file(BUILTIN_ROOT / "paper-cn", "main.tex")
        assert resolved.is_file()
        assert resolved.name == "main.tex"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            resolve_template_file(BUILTIN_ROOT / "paper-cn", "../__init__.py")
        with pytest.raises(ValueError):
            resolve_template_file(BUILTIN_ROOT / "paper-cn", "../../etc/passwd")

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            resolve_template_file(BUILTIN_ROOT / "paper-cn", "/etc/passwd")

    def test_suffix_whitelist_enforced(self):
        with pytest.raises(ValueError):
            resolve_template_file(
                BUILTIN_ROOT / "paper-cn",
                "assets/thumbnail.png",
                allowed_suffixes=(".tex",),
            )

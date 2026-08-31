"""TemplateManifest 等模型校验测试（严格对齐阶段① manifest schema）。"""

import pytest
from pydantic import ValidationError

from backend.modules.template_library.builtin_store import BUILTIN_ROOT, load_manifest
from backend.modules.template_library.models import TemplateManifest
from tests.test_template_library.conftest import make_manifest


class TestTemplateManifest:
    def test_valid_manifest(self):
        m = TemplateManifest.model_validate(make_manifest())
        assert m.id == "my-template"
        assert m.category == "paper"
        assert m.source == "upload"
        assert m.engine == "xelatex"

    def test_optional_fields_default(self):
        data = make_manifest()
        for key in ("name_en", "engine", "author", "license", "description"):
            data.pop(key)
        m = TemplateManifest.model_validate(data)
        assert m.name_en == ""
        assert m.engine is None

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(make_manifest(category="diary"))

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(make_manifest(source="marketplace"))

    def test_invalid_engine_rejected(self):
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(make_manifest(engine="lualatex"))

    def test_missing_required_field_rejected(self):
        data = make_manifest()
        data.pop("main_file")
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(data)

    def test_missing_category_rejected(self):
        data = make_manifest()
        data.pop("category")
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(data)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            TemplateManifest.model_validate(make_manifest(unexpected=1))


class TestBuiltinManifestsConform:
    """阶段①内置模板 manifest 必须全部通过当前 schema 校验。"""

    def test_all_builtin_manifests_validate(self):
        dirs = [
            d
            for d in BUILTIN_ROOT.iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()
        ]
        assert dirs, "builtin templates missing"
        for d in dirs:
            manifest = load_manifest(d)
            assert manifest is not None, f"invalid manifest: {d.name}"
            assert manifest.id == d.name
            assert manifest.source == "builtin"

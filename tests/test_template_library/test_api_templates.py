"""模板库单用户模式 API 测试（/api/templates 全端点）。"""

import json

from backend.core.config import get_config
from tests.test_template_library.conftest import (
    make_manifest,
    make_zip,
)


class TestListAndDetail:
    def test_list_contains_builtin_and_mine(self, su_client, su_env):
        r = su_client.get("/api/templates")
        assert r.status_code == 200
        items = r.json()["templates"]
        kinds = {t["kind"] for t in items}
        assert "builtin" in kinds
        assert all("root_path" not in t for t in items)

    def test_category_filter(self, su_client):
        r = su_client.get("/api/templates", params={"category": "resume"})
        assert r.status_code == 200
        items = r.json()["templates"]
        assert items
        assert all(t["category"] == "resume" for t in items)

    def test_detail_with_file_tree(self, su_client):
        r = su_client.get("/api/templates/paper-cn")
        assert r.status_code == 200
        data = r.json()
        assert data["kind"] == "builtin"
        paths = [f["path"] for f in data["files"]]
        assert "main.tex" in paths
        assert "manifest.json" not in paths
        assert all(not p.startswith("assets/") for p in paths)

    def test_detail_not_found(self, su_client):
        r = su_client.get("/api/templates/no-such")
        assert r.status_code == 404


class TestFileView:
    def test_read_source(self, su_client):
        r = su_client.get("/api/templates/paper-cn/file", params={"path": "main.tex"})
        assert r.status_code == 200
        assert r.json()["path"] == "main.tex"
        assert "documentclass" in r.json()["content"]

    def test_path_traversal_rejected(self, su_client):
        r = su_client.get(
            "/api/templates/paper-cn/file", params={"path": "../../etc/passwd"}
        )
        assert r.status_code == 400

    def test_binary_type_not_viewable(self, su_client):
        r = su_client.get(
            "/api/templates/paper-cn/file",
            params={"path": "assets/thumbnail.png"},
        )
        assert r.status_code == 400


class TestBuiltinAssets:
    def test_thumbnail(self, su_client):
        r = su_client.get("/api/templates/paper-cn/thumbnail")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_preview_pdf(self, su_client):
        r = su_client.get("/api/templates/paper-cn/preview")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"

    def test_asset_of_missing_template_404(self, su_client):
        assert su_client.get("/api/templates/no-such/preview").status_code == 404


class TestUpload:
    def test_upload_with_manifest(self, su_client):
        payload = make_zip(
            {
                "manifest.json": json.dumps(make_manifest()),
                "main.tex": r"\documentclass{article}",
            }
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        assert tpl["kind"] == "user"
        assert tpl["name"] == "我的测试模板"
        assert tpl["source"] == "upload"
        # file_count 与详情文件树同口径（库内 manifest.json 不计数）
        assert tpl["file_count"] == 1
        # 入库后在列表可见，且 manifest.json 不在文件树
        detail = su_client.get(f"/api/templates/{tpl['id']}").json()
        assert "main.tex" in [f["path"] for f in detail["files"]]
        assert len(detail["files"]) == detail["file_count"] == 1

    def test_upload_without_manifest_autogenerates(self, su_client):
        payload = make_zip(
            {
                "main.tex": r"\documentclass{article}",
                "chapters/intro.tex": r"\section{x}",
            }
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("我的论文包.zip", payload, "application/zip")},
            data={"category": "paper"},
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        # 自动生成：zip 名为名、传入分类、main_file 自动探测
        assert tpl["name"] == "我的论文包"
        assert tpl["category"] == "paper"
        assert tpl["main_file"] == "main.tex"
        assert tpl["source"] == "upload"

    def test_upload_rejects_non_zip(self, su_client):
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.7z", b"123", "application/x-7z")},
        )
        assert r.status_code == 400

    def test_upload_rejects_disallowed_extension(self, su_client):
        payload = make_zip(
            {
                "main.tex": "x",
                "evil.exe": "MZ...",
            }
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_file_type"

    def test_upload_cleans_extensionless_files(self, su_client):
        """无扩展名文件豁免白名单校验，但解压后清理、不入库。"""
        payload = make_zip(
            {
                "main.tex": r"\documentclass{article}",
                "figs/plot.png": "png",
                ".gitignore": "build/",
                "Makefile": "all:",
                "LICENSE": "MIT",
                "notes/readme": "junk",
            }
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        # 无扩展名杂项全部被清理，仅剩白名单内文件
        assert tpl["file_count"] == 2
        detail = su_client.get(f"/api/templates/{tpl['id']}").json()
        paths = [f["path"] for f in detail["files"]]
        assert set(paths) == {"main.tex", "figs/plot.png"}

    def test_upload_over_size_limit(self, su_client, su_env):
        cfg = get_config()
        old = cfg.features.template_library.max_upload_mb
        cfg.features.template_library.max_upload_mb = 1
        try:
            payload = make_zip({"main.tex": "x" * (2 * 1024 * 1024)})
            r = su_client.post(
                "/api/templates",
                files={"archive": ("big.zip", payload, "application/zip")},
            )
            assert r.status_code == 413
        finally:
            cfg.features.template_library.max_upload_mb = old


class TestUpdateAndDelete:
    def _upload(self, su_client) -> str:
        payload = make_zip(
            {
                "manifest.json": json.dumps(make_manifest()),
                "main.tex": "x",
            }
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        assert r.status_code == 201
        return r.json()["template"]["id"]

    def test_rename_and_category(self, su_client):
        tid = self._upload(su_client)
        r = su_client.put(
            f"/api/templates/{tid}",
            json={"name": "改名后", "category": "report"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["template"]
        assert data["name"] == "改名后"
        assert data["category"] == "report"

    def test_update_builtin_rejected(self, su_client):
        r = su_client.put("/api/templates/paper-cn", json={"name": "x"})
        assert r.status_code == 400

    def test_delete_user_template(self, su_client):
        tid = self._upload(su_client)
        assert su_client.delete(f"/api/templates/{tid}").status_code == 200
        assert su_client.get(f"/api/templates/{tid}").status_code == 404

    def test_delete_builtin_rejected(self, su_client):
        assert su_client.delete("/api/templates/paper-cn").status_code == 400

    def test_delete_missing_404(self, su_client):
        assert su_client.delete("/api/templates/no-such").status_code == 404

    def test_blank_name_means_no_change(self, su_client):
        """PUT name 为空白：视为未提供，不回退成兜底名"我的模板"。"""
        tid = self._upload(su_client)
        r = su_client.put(f"/api/templates/{tid}", json={"name": "   "})
        assert r.status_code == 200
        assert r.json()["template"]["name"] == "我的测试模板"


class TestCreateProjectSingleUser:
    def test_creates_cleaned_project(self, su_client, su_env, tmp_path):
        payload = make_zip({"main.tex": "x", "chapters/a.tex": "y"})
        r = su_client.post(
            "/api/templates",
            files={
                "archive": (
                    "pack.zip",
                    payload,
                    "application/zip",
                )
            },
        )
        tid = r.json()["template"]["id"]
        target = tmp_path / "projects"
        target.mkdir()
        (target / ".git").mkdir()
        r = su_client.post(
            f"/api/templates/{tid}/create-project",
            json={"name": "我的新项目", "target_dir": str(target)},
        )
        assert r.status_code == 201, r.text
        project_dir = target / "我的新项目"
        assert (project_dir / "main.tex").is_file()
        assert (project_dir / "chapters" / "a.tex").is_file()

    def test_target_not_empty_conflict(self, su_client, su_env, tmp_path):
        payload = make_zip(
            {"manifest.json": json.dumps(make_manifest()), "main.tex": "x"}
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        tid = r.json()["template"]["id"]
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "我的项目").mkdir()
        (target / "我的项目" / "old.tex").write_text("x", encoding="utf-8")
        r = su_client.post(
            f"/api/templates/{tid}/create-project",
            json={"name": "我的项目", "target_dir": str(target)},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "target_not_empty"

    def test_target_dir_required(self, su_client, su_env):
        payload = make_zip(
            {"manifest.json": json.dumps(make_manifest()), "main.tex": "x"}
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        tid = r.json()["template"]["id"]
        r = su_client.post(f"/api/templates/{tid}/create-project", json={"name": "x"})
        assert r.status_code == 400

    def test_create_from_builtin(self, su_client, su_env, tmp_path):
        """内置模板创建项目：manifest.json 与 assets/ 被排除。"""
        target = tmp_path / "out"
        target.mkdir()
        r = su_client.post(
            "/api/templates/paper-cn/create-project",
            json={"name": "论文副本", "target_dir": str(target)},
        )
        assert r.status_code == 201, r.text
        project_dir = target / "论文副本"
        assert (project_dir / "main.tex").is_file()
        assert not (project_dir / "manifest.json").exists()
        assert not (project_dir / "assets").exists()

    def test_create_from_user_template_drops_manifest(
        self, su_client, su_env, tmp_path
    ):
        """用户模板创建项目：库内服务端生成的 manifest.json 不混入项目。"""
        payload = make_zip(
            {"manifest.json": json.dumps(make_manifest()), "main.tex": "x"}
        )
        r = su_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        tid = r.json()["template"]["id"]
        target = tmp_path / "user-out"
        target.mkdir()
        r = su_client.post(
            f"/api/templates/{tid}/create-project",
            json={"name": "用户模板项目", "target_dir": str(target)},
        )
        assert r.status_code == 201, r.text
        project_dir = target / "用户模板项目"
        assert (project_dir / "main.tex").is_file()
        assert not (project_dir / "manifest.json").exists()


class TestFromProject:
    def test_save_opened_project_as_template(self, su_client, su_env):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        project_dir = su_env / "proj"
        (project_dir / ".git").mkdir(parents=True)
        (project_dir / "main.tex").write_text("x", encoding="utf-8")
        (project_dir / "main.aux").write_text("junk", encoding="utf-8")
        (project_dir / "main.pdf").write_bytes(b"%PDF")
        get_project_manager_for_workspace("ws-save").open(str(project_dir))
        r = su_client.post(
            "/api/templates/from-project",
            json={"workspace_id": "ws-save", "name": "项目模板"},
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        assert tpl["source"] == "project_save"
        detail = su_client.get(f"/api/templates/{tpl['id']}").json()
        paths = [f["path"] for f in detail["files"]]
        assert "main.tex" in paths
        # 清洗：.git、编译产物被剔除
        assert all(not p.startswith(".git") for p in paths)
        assert "main.aux" not in paths

    def test_workspace_not_open_rejected(self, su_client, su_env):
        r = su_client.post(
            "/api/templates/from-project",
            json={"workspace_id": "ws-none", "name": "x"},
        )
        assert r.status_code == 400


class TestFeatureDisabled:
    def test_disabled_endpoints_closed(self, su_client, su_env):
        cfg = get_config()
        old = cfg.features.template_library.enabled
        cfg.features.template_library.enabled = False
        try:
            assert su_client.get("/api/templates").status_code == 404
            assert su_client.get("/api/templates/paper-cn").status_code == 404
            assert (
                su_client.post("/api/templates/from-project", json={}).status_code
                == 404
            )
        finally:
            cfg.features.template_library.enabled = old

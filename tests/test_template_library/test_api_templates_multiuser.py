"""模板库多用户模式 API 测试（认证 / 越权隔离 / 配额 413 / 清洗 / 特殊端点）。"""

import json

from backend.core.multiuser import userspace
from tests.test_template_library.conftest import (
    csrf,
    make_manifest,
    make_project_row,
    make_user_template,
    make_zip,
)

PASSWORD = "password-123"


def _project_dir(env, user_id: str, project_id: str):
    return userspace.project_dir(user_id, project_id)


class TestAuth:
    def test_requires_login(self, env, client):
        assert client.get("/api/templates").status_code == 401

    def test_alice_can_list(self, env, alice_client):
        r = alice_client.get("/api/templates")
        assert r.status_code == 200
        kinds = {t["kind"] for t in r.json()["templates"]}
        assert "builtin" in kinds


class TestUploadAndIsolation:
    def _upload(self, client) -> dict:
        payload = make_zip(
            {
                "manifest.json": json.dumps(make_manifest()),
                "main.tex": r"\documentclass{article}",
            }
        )
        r = client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
            headers=csrf(client),
        )
        assert r.status_code == 201, r.text
        return r.json()["template"]

    def test_upload_visible_to_owner_only(self, env, alice_client, client):
        tpl = self._upload(alice_client)
        r = alice_client.get("/api/templates")
        mine = [t for t in r.json()["templates"] if t["kind"] == "user"]
        assert [t["id"] for t in mine] == [tpl["id"]]

        # bob 无法访问 alice 的模板（越权 404，不泄露存在性）
        _bob = client.post(
            "/api/auth/login", json={"username": "bob", "password": PASSWORD}
        )
        assert client.get(f"/api/templates/{tpl['id']}").status_code == 404
        assert (
            client.put(
                f"/api/templates/{tpl['id']}",
                json={"name": "x"},
                headers=csrf(client),
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/templates/{tpl['id']}", headers=csrf(client)
            ).status_code
            == 404
        )

    def test_mutating_without_csrf_rejected(self, env, alice_client):
        payload = make_zip({"main.tex": "x"})
        r = alice_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
        )
        assert r.status_code == 403

    def test_upload_over_derived_quota_413(self, env, alice_client):
        """衍生配额（模板类别）不足：上传 413 且模板库不落任何内容。"""
        from backend.core.multiuser import db as db_module
        from backend.modules.template_library import user_store

        alice = env["alice"]["id"]
        db_module.db_execute(
            "UPDATE users SET derived_quota_bytes = ? WHERE id = ?",
            (1, alice),
        )
        payload = make_zip({"main.tex": r"\documentclass{article}"})
        r = alice_client.post(
            "/api/templates",
            files={"archive": ("pack.zip", payload, "application/zip")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        lib = user_store.library_root(alice)
        assert not lib.exists() or list(lib.iterdir()) == []

    def test_builtin_readonly(self, env, alice_client):
        r = alice_client.put(
            "/api/templates/paper-cn", json={"name": "x"}, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        r = alice_client.delete("/api/templates/paper-cn", headers=csrf(alice_client))
        assert r.status_code == 400


class TestCreateProject:
    def test_create_from_builtin_copies_cleaned_tree(self, env, alice_client):
        r = alice_client.post(
            "/api/templates/paper-cn/create-project",
            json={"name": "论文项目"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        project = r.json()["project"]
        assert project["name"] == "论文项目"
        root = _project_dir(env, env["alice"]["id"], project["project_id"])
        assert (root / "main.tex").is_file()
        assert not (root / "manifest.json").exists()
        assert not (root / "assets").exists()

    def test_same_name_auto_dedup(self, env, alice_client):
        for _ in range(2):
            r = alice_client.post(
                "/api/templates/blank/create-project",
                json={"name": "同名项目"},
                headers=csrf(alice_client),
            )
            assert r.status_code == 201, r.text
        from backend.core.multiuser import db

        names = [
            row["name"]
            for row in db.db_query_all(
                "SELECT name FROM projects WHERE owner_id = ? ORDER BY created_at",
                (env["alice"]["id"],),
            )
        ]
        assert names == ["同名项目", "同名项目 (2)"]

    def test_quota_exceeded_413(self, env, alice_client):
        """项目配额超限：创建项目返回 413 且项目不落位。"""
        alice = env["alice"]["id"]
        from backend.core.multiuser import db as db_module

        # 把 alice 的项目配额压到极小（已有用量 + 模板体量 > 配额）
        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = ? WHERE id = ?",
            (1, alice),
        )
        r = alice_client.post(
            "/api/templates/blank/create-project",
            json={"name": "超限项目"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        projects_dir = userspace.project_dir(env["alice"]["id"], "placeholder").parent
        assert list(projects_dir.iterdir()) == []

    def test_create_from_user_template_cleaned(self, env, alice_client):
        """从用户模板创建：.git 与编译产物被清洗。"""
        alice = env["alice"]["id"]
        tpl = make_user_template(
            alice,
            {
                "main.tex": "x",
                "notes.txt": "y",
            },
        )
        # 手工塞入 .git 与 .aux（make_user_template 只写白名单文本）
        from backend.modules.template_library import user_store

        tdir = user_store.template_dir(alice, tpl["id"])
        (tdir / ".git").mkdir()
        (tdir / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        (tdir / "main.aux").write_text("junk", encoding="utf-8")

        r = alice_client.post(
            f"/api/templates/{tpl['id']}/create-project",
            json={"name": "清洗验证"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        root = _project_dir(env, env["alice"]["id"], r.json()["project"]["project_id"])
        assert (root / "main.tex").is_file()
        assert (root / "notes.txt").is_file()
        assert not (root / ".git").exists()
        assert not (root / "main.aux").exists()
        # 库内服务端生成的 manifest.json 不混入新项目
        assert not (root / "manifest.json").exists()


class TestFromProject:
    def test_save_project_as_template(self, env, alice_client):
        alice = env["alice"]["id"]
        project_id = make_project_row(
            env,
            alice,
            "源项目",
            {"main.tex": "x", "chapters/a.tex": "y", "main.log": "junk"},
        )
        project_root = userspace.project_dir(alice, project_id)
        (project_root / ".git").mkdir()
        (project_root / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        r = alice_client.post(
            "/api/project/open",
            params={"workspace_id": "ws-tpl"},
            json={"project_id": project_id},
            headers=csrf(alice_client),
        )
        assert r.status_code == 200, r.text
        r = alice_client.post(
            "/api/templates/from-project",
            json={"workspace_id": "ws-tpl", "name": "另存模板"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        assert tpl["source"] == "project_save"
        detail = alice_client.get(f"/api/templates/{tpl['id']}").json()
        paths = [f["path"] for f in detail["files"]]
        assert "main.tex" in paths
        assert "chapters/a.tex" in paths
        assert "main.log" not in paths
        assert all(not p.startswith(".git") for p in paths)


class TestFromTemplateGen:
    def test_save_session_artifacts(self, env, alice_client):
        """template_gen 会话产物入库：剔除 PDF / trace / 编译辅助文件。"""
        from backend.modules.template_library import user_store

        sandbox = (
            user_store.library_root(env["alice"]["id"]).parent / "templates" / "sess001"
        )
        (sandbox / "chapters").mkdir(parents=True)
        (sandbox / "main.tex").write_text("x", encoding="utf-8")
        (sandbox / "chapters" / "a.tex").write_text("y", encoding="utf-8")
        (sandbox / "main.pdf").write_bytes(b"%PDF")
        (sandbox / "trace.jsonl").write_text("{}", encoding="utf-8")
        (sandbox / "main.aux").write_text("junk", encoding="utf-8")

        r = alice_client.post(
            "/api/templates/from-template-gen",
            json={"session_id": "sess001", "name": "生成模板"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        tpl = r.json()["template"]
        assert tpl["source"] == "template_gen"
        assert tpl["main_file"] == "main.tex"
        detail = alice_client.get(f"/api/templates/{tpl['id']}").json()
        paths = [f["path"] for f in detail["files"]]
        assert paths == ["chapters/a.tex", "main.tex"]

    def test_session_not_found(self, env, alice_client):
        r = alice_client.post(
            "/api/templates/from-template-gen",
            json={"session_id": "missing", "name": "x"},
            headers=csrf(alice_client),
        )
        assert r.status_code == 404

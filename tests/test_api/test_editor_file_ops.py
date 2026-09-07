"""编辑器文件树新建/删除接口测试（单用户模式）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖：创建成功（磁盘落盘 + 响应携带重扫后 file_tree + 子目录场景）、
重复创建 409、白名单外后缀拒绝、父目录缺失、路径穿越防护、超长内容
413；删除成功（磁盘移除 + 树同步）、删除不存在 404、主文档删除保护、
白名单外后缀删除拒绝；以及 refresh_file_tree 对 GET /file-tree 缓存的
刷新效果。
"""

import pytest

MAIN_TEX = r"""\documentclass{article}
\begin{document}
Hello world.
\end{document}
"""


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture
def env(client, tmp_path):
    """已打开临时项目的单用户工作区；结束释放工作区，避免跨用例残留。"""
    from backend.core.workspace_manager import get_workspace_manager

    ws_id = "editor-file-ops"
    (tmp_path / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    yield {"ws": ws_id, "root": tmp_path}
    get_workspace_manager().remove(ws_id)


def _create(client, env, file_path, content=""):
    return client.post(
        "/api/project/create-file",
        json={"file_path": file_path, "workspace_id": env["ws"], "content": content},
    )


def _delete(client, env, file_path):
    return client.post(
        "/api/project/delete-file",
        json={"file_path": file_path, "workspace_id": env["ws"]},
    )


def _tree_paths(tree, prefix=""):
    """递归收集文件树中全部文件相对路径（目录不收）。"""
    paths = []
    for node in tree:
        if node["is_dir"]:
            paths.extend(_tree_paths(node.get("children", []), node["path"]))
        else:
            paths.append(node["path"])
    return paths


class TestCreateFile:
    def test_create_empty_file_returns_rescanned_tree(self, client, env):
        resp = _create(client, env, "appendix.tex")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_path"] == "appendix.tex"
        # 响应携带重扫后的文件树，含新建文件（单次往返刷新）
        assert "appendix.tex" in _tree_paths(data["file_tree"])
        assert (env["root"] / "appendix.tex").read_bytes() == b""

    def test_create_in_subdirectory_updates_nested_tree(self, client, env):
        (env["root"] / "chapters").mkdir()
        resp = _create(client, env, "chapters/intro.tex", "% intro\n")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "chapters/intro.tex" in _tree_paths(data["file_tree"])
        assert (env["root"] / "chapters" / "intro.tex").read_text(
            encoding="utf-8"
        ) == "% intro\n"

    def test_create_duplicate_returns_409(self, client, env):
        resp = _create(client, env, "main.tex")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "file_exists"
        # 多用户脱敏约束：错误消息不得携带服务器绝对路径
        assert str(env["root"]) not in resp.text

    def test_rejects_suffix_outside_whitelist(self, client, env):
        resp = _create(client, env, "notes.txt", "plain")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert not (env["root"] / "notes.txt").exists()

    def test_missing_parent_directory_rejected(self, client, env):
        resp = _create(client, env, "no_such_dir/new.tex")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "parent_not_found"
        assert str(env["root"]) not in resp.text
        assert not (env["root"] / "no_such_dir").exists()

    def test_rejects_path_traversal(self, client, env, tmp_path):
        resp = _create(client, env, "../evil.tex", "secret")
        assert resp.status_code == 400
        assert "secret" not in resp.text
        assert not (tmp_path.parent / "evil.tex").exists()

    def test_oversized_content_rejected_before_write(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        monkeypatch.setattr(editor_service, "MAX_EDITABLE_CHARS", 10)
        resp = _create(client, env, "big.tex", "this content is too long\n")
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "content_too_long"
        assert not (env["root"] / "big.tex").exists()

    def test_corrupted_content_rejected(self, client, env):
        resp = _create(client, env, "broken.tex", "Bad:\x0bcontrol\n")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_content"
        assert not (env["root"] / "broken.tex").exists()


class TestDeleteFile:
    def test_delete_removes_file_and_updates_tree(self, client, env):
        (env["root"] / "extra.tex").write_text("% extra\n", encoding="utf-8")
        resp = _delete(client, env, "extra.tex")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_path"] == "extra.tex"
        assert "extra.tex" not in _tree_paths(data["file_tree"])
        assert not (env["root"] / "extra.tex").exists()

    def test_delete_missing_file_returns_404(self, client, env):
        resp = _delete(client, env, "no_such.tex")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "file_not_found"
        assert detail["message"] == "File not found: no_such.tex"
        assert str(env["root"]) not in resp.text

    def test_delete_main_file_protected(self, client, env):
        # open() 自动识别 main.tex 为主文档，删除应被拒绝
        resp = _delete(client, env, "main.tex")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "main_file_protected"
        assert str(env["root"]) not in resp.text
        assert (env["root"] / "main.tex").exists()

    def test_delete_after_main_file_switch_allows_old_main(self, client, env):
        # 先换主文件再删除原主文档：保护只针对当前主文件
        (env["root"] / "other.tex").write_text("% other\n", encoding="utf-8")
        switched = client.post(
            "/api/project/set-main-file",
            params={"workspace_id": env["ws"]},
            json={"file_path": "other.tex"},
        )
        assert switched.status_code == 200, switched.text
        resp = _delete(client, env, "main.tex")
        assert resp.status_code == 200, resp.text
        assert not (env["root"] / "main.tex").exists()

    def test_rejects_suffix_outside_whitelist(self, client, env):
        (env["root"] / "figure.pdf").write_bytes(b"%PDF-1.4\n")
        resp = _delete(client, env, "figure.pdf")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert (env["root"] / "figure.pdf").exists()

    def test_rejects_path_traversal(self, client, env, tmp_path):
        outside = tmp_path.parent / "evil.tex"
        outside.write_text("secret", encoding="utf-8")
        try:
            resp = _delete(client, env, "../evil.tex")
            assert resp.status_code == 400
            assert "secret" not in resp.text
        finally:
            outside.unlink(missing_ok=True)


class TestMainFilePersistence:
    """手动主文件持久化（.latexcellence/main_file）：换主文件后项目重开
    （页面刷新直达恢复 / 后端重启 / 关闭再打开）必须保持有效——否则 open()
    的自动识别（优先 main.tex）会静默重置用户选择，删除原主文件的通路
    也随之再次被保护拦截。"""

    def _set_main(self, client, env, file_path):
        return client.post(
            "/api/project/set-main-file",
            params={"workspace_id": env["ws"]},
            json={"file_path": file_path},
        )

    def test_switch_survives_project_reopen_then_delete_old_main(self, client, env):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        (env["root"] / "other.tex").write_text("% other\n", encoding="utf-8")
        switched = self._set_main(client, env, "other.tex")
        assert switched.status_code == 200, switched.text

        # 模拟刷新直达 / 重开项目：open() 重新识别主文件，应读到持久标记
        pm = get_project_manager_for_workspace(env["ws"])
        info = pm.open(str(env["root"]))
        assert info.main_file == "other.tex"

        # 删除原主文档不再被保护拦截
        resp = _delete(client, env, "main.tex")
        assert resp.status_code == 200, resp.text
        assert not (env["root"] / "main.tex").exists()

    def test_marker_dir_not_exposed_in_file_tree(self, client, env):
        (env["root"] / "other.tex").write_text("% other\n", encoding="utf-8")
        assert self._set_main(client, env, "other.tex").status_code == 200
        assert (env["root"] / ".latexcellence" / "main_file").is_file()
        listed = client.get(
            "/api/project/file-tree",
            params={"workspace_id": env["ws"]},
        )
        assert listed.status_code == 200
        # 元数据目录为隐藏目录：不进文件树，也不出现在任何响应文本
        assert ".latexcellence" not in listed.text

    def test_invalid_marker_falls_back_to_auto_detect(self, client, env):
        # 上传压缩包夹带的恶意/陈旧标记（穿越路径）→ 安全校验拒绝，
        # open 时安全回落自动识别（main.tex）
        marker_dir = env["root"] / ".latexcellence"
        marker_dir.mkdir()
        (marker_dir / "main_file").write_text("../evil.tex", encoding="utf-8")
        from backend.core.workspace_manager import get_project_manager_for_workspace

        pm = get_project_manager_for_workspace(env["ws"])
        info = pm.open(str(env["root"]))
        assert info.main_file == "main.tex"


class TestFileTreeRefresh:
    """create/delete 响应外，GET /file-tree 的打开时缓存也应反映变更
    （服务端 refresh_file_tree 就地更新 open 时扫描的缓存）。"""

    def test_file_tree_endpoint_reflects_create_and_delete(self, client, env):
        assert _create(client, env, "added.tex").status_code == 200
        listed = client.get(
            "/api/project/file-tree",
            params={"workspace_id": env["ws"]},
        )
        assert listed.status_code == 200
        assert "added.tex" in _tree_paths(listed.json()["file_tree"])

        assert _delete(client, env, "added.tex").status_code == 200
        listed = client.get(
            "/api/project/file-tree",
            params={"workspace_id": env["ws"]},
        )
        assert "added.tex" not in _tree_paths(listed.json()["file_tree"])

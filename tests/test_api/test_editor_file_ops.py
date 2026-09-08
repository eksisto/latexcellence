"""编辑器文件树新建/删除/移动/上传接口测试（单用户模式）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖：创建成功（磁盘落盘 + 响应携带重扫后 file_tree + 子目录场景）、
重复创建 409、白名单外后缀拒绝、父目录缺失、路径穿越防护、超长内容
413；删除成功（磁盘移除 + 树同步，editor-file-upload-design §4.4 放开
为任意非隐藏后缀）、删除不存在 404、主文档删除保护、目录递归删除与
子树主文件保护（editor-file-move-design 决策 Q9/Q10）、项目根本身
删除拒绝（「.」解析即根）、隐藏路径直调删除拒绝（D9）；移动
（move-file：文件/目录通用、冲突 409、into_self、
父目录缺失、隐藏路径拒绝、主文件记录跟随改写 §4.2/决策 Q3）；新建
目录（create-dir：根/子目录成功、重复 409、父缺失 400、非法名/隐藏名
拒绝）；上传（§4：任意后缀成功、超限 413、同名 409、父目录缺失、
隐藏/点开头/控制字符文件名拒绝）；产物黑名单不进 file_tree（含
.synctex.gz 双后缀）；raw-file 对非白名单后缀仍拒绝（§3-1 回归）；以及
refresh_file_tree 对 GET /file-tree 缓存的刷新效果。
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


def _upload(client, env, file_path, data: bytes):
    return client.post(
        "/api/project/upload-file",
        params={"file_path": file_path, "workspace_id": env["ws"]},
        files={"file": ("upload.bin", data, "application/octet-stream")},
    )


def _move(client, env, from_path, to_path):
    return client.post(
        "/api/project/move-file",
        json={"from_path": from_path, "to_path": to_path, "workspace_id": env["ws"]},
    )


def _create_dir(client, env, dir_path):
    return client.post(
        "/api/project/create-dir",
        json={"dir_path": dir_path, "workspace_id": env["ws"]},
    )


def _set_main(client, env, file_path):
    return client.post(
        "/api/project/set-main-file",
        params={"workspace_id": env["ws"]},
        json={"file_path": file_path},
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


def _tree_dirs(tree):
    """递归收集文件树中全部目录相对路径。"""
    dirs = []
    for node in tree:
        if node["is_dir"]:
            dirs.append(node["path"])
            dirs.extend(_tree_dirs(node.get("children", [])))
    return dirs


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

    def test_delete_arbitrary_non_hidden_suffix_succeeds(self, client, env):
        # §4.4 删除白名单放开：资源后缀（.pdf / .xyz）等任意非隐藏后缀可删
        (env["root"] / "figure.pdf").write_bytes(b"%PDF-1.4\n")
        (env["root"] / "data.xyz").write_bytes(b"\x00\x01binary")
        assert _delete(client, env, "figure.pdf").status_code == 200
        assert _delete(client, env, "data.xyz").status_code == 200
        assert not (env["root"] / "figure.pdf").exists()
        assert not (env["root"] / "data.xyz").exists()

    def test_delete_artifact_via_api_succeeds(self, client, env):
        # 附带效果（§4.4）：产物树中不可见但可通过 API 删除（无 UI 入口）
        (env["root"] / "main.aux").write_bytes(b"relax\n")
        resp = _delete(client, env, "main.aux")
        assert resp.status_code == 200, resp.text
        assert not (env["root"] / "main.aux").exists()

    def test_delete_hidden_path_rejected(self, client, env):
        # D9「非隐藏」约束：白名单放开后，API 直调隐藏路径仍须拒绝——
        # 否则 .git 内部文件 / .latexcellence 主文件持久化可经删除端点移除
        (env["root"] / ".git").mkdir()
        (env["root"] / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (env["root"] / ".latexcellence").mkdir()
        (env["root"] / ".latexcellence" / "main_file").write_text(
            "main.tex", encoding="utf-8"
        )
        for hidden in (".git/config", ".latexcellence/main_file"):
            resp = _delete(client, env, hidden)
            assert resp.status_code == 400, resp.text
            assert resp.json()["detail"]["error"] == "invalid_path"
        assert (env["root"] / ".git" / "config").exists()
        assert (env["root"] / ".latexcellence" / "main_file").exists()

    def test_delete_project_root_rejected(self, client, env):
        # 回归：「.」经 path_guard 解析即项目根本身，目录递归删除分支
        # 若不拦截会把整个项目（含 .git / .latexcellence）连根删除；
        # 必须以 400 invalid_path 拒绝且磁盘原样保留
        (env["root"] / "chapters").mkdir()
        for dot in (".", "./"):
            resp = _delete(client, env, dot)
            assert resp.status_code == 400, f"{dot!r}: {resp.text}"
            assert resp.json()["detail"]["error"] == "invalid_path"
        assert (env["root"] / "main.tex").exists()
        assert (env["root"] / "chapters").is_dir()

    def test_delete_directory_recursive(self, client, env):
        # Q9：目录递归删除，嵌套内容整棵随删，树同步移除
        src = env["root"] / "src"
        (src / "inner").mkdir(parents=True)
        (src / "a.tex").write_text("% a\n", encoding="utf-8")
        (src / "inner" / "b.tex").write_text("% b\n", encoding="utf-8")
        resp = _delete(client, env, "src")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_path"] == "src"
        assert not src.exists()
        assert "src/a.tex" not in _tree_paths(data["file_tree"])
        assert "src" not in _tree_dirs(data["file_tree"])

    def test_delete_empty_directory(self, client, env):
        (env["root"] / "empty").mkdir()
        resp = _delete(client, env, "empty")
        assert resp.status_code == 200, resp.text
        assert not (env["root"] / "empty").exists()

    def test_delete_directory_containing_main_file_protected(self, client, env):
        # Q10：子树内含主文件（含更深层级）→ 拒绝递归删除，磁盘原样保留
        src = env["root"] / "src"
        (src / "inner").mkdir(parents=True)
        (src / "inner" / "thesis.tex").write_text("% thesis\n", encoding="utf-8")
        assert _set_main(client, env, "src/inner/thesis.tex").status_code == 200
        resp = _delete(client, env, "src")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "main_file_protected"
        assert str(env["root"]) not in resp.text
        assert (src / "inner" / "thesis.tex").exists()

    def test_delete_hidden_directory_rejected(self, client, env):
        # 隐藏目录本身同样不可经删除端点移除（与 D9 同口径）
        (env["root"] / ".git").mkdir()
        (env["root"] / ".latexcellence").mkdir()
        (env["root"] / ".latexcellence" / "main_file").write_text(
            "main.tex", encoding="utf-8"
        )
        for hidden in (".git", ".latexcellence"):
            resp = _delete(client, env, hidden)
            assert resp.status_code == 400, resp.text
            assert resp.json()["detail"]["error"] == "invalid_path"
        assert (env["root"] / ".git").is_dir()
        assert (env["root"] / ".latexcellence" / "main_file").exists()

    def test_rejects_path_traversal(self, client, env, tmp_path):
        outside = tmp_path.parent / "evil.tex"
        outside.write_text("secret", encoding="utf-8")
        try:
            resp = _delete(client, env, "../evil.tex")
            assert resp.status_code == 400
            assert "secret" not in resp.text
        finally:
            outside.unlink(missing_ok=True)


class TestMovePath:
    """文件树拖拽移动（POST /api/project/move-file，editor-file-move-design
    §4.1）：文件与目录通用（整棵子树随迁）、409 target_exists（决策 Q2）、
    400 into_self、400 parent_not_found、404 file_not_found、隐藏路径拒绝、
    主文件记录跟随改写（§4.2 / 决策 Q3）、响应携带重扫后 file_tree。
    """

    def test_move_file_into_subdirectory(self, client, env):
        (env["root"] / "chapters").mkdir()
        (env["root"] / "draft.tex").write_text("% draft\n", encoding="utf-8")
        resp = _move(client, env, "draft.tex", "chapters/draft.tex")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["from_path"] == "draft.tex"
        assert data["to_path"] == "chapters/draft.tex"
        # 磁盘：旧路径消失、内容随迁新路径
        assert not (env["root"] / "draft.tex").exists()
        assert (env["root"] / "chapters" / "draft.tex").read_text(
            encoding="utf-8"
        ) == "% draft\n"
        # 响应携带重扫后 file_tree（单次往返刷新）
        paths = _tree_paths(data["file_tree"])
        assert "chapters/draft.tex" in paths
        assert "draft.tex" not in paths

    def test_move_file_to_root(self, client, env):
        chapters = env["root"] / "chapters"
        chapters.mkdir()
        (chapters / "deep.tex").write_text("% deep\n", encoding="utf-8")
        resp = _move(client, env, "chapters/deep.tex", "deep.tex")
        assert resp.status_code == 200, resp.text
        assert (env["root"] / "deep.tex").read_text(encoding="utf-8") == "% deep\n"
        assert not (chapters / "deep.tex").exists()
        paths = _tree_paths(resp.json()["file_tree"])
        assert "deep.tex" in paths
        assert "chapters/deep.tex" not in paths

    def test_move_directory_subtree_follows(self, client, env):
        src = env["root"] / "src"
        (src / "inner").mkdir(parents=True)
        (src / "a.tex").write_text("% a\n", encoding="utf-8")
        (src / "inner" / "b.tex").write_text("% b\n", encoding="utf-8")
        (src / "fig.png").write_bytes(b"\x89PNG")
        resp = _move(client, env, "src", "parts")
        assert resp.status_code == 200, resp.text
        assert not src.exists()
        assert (env["root"] / "parts" / "a.tex").read_text(encoding="utf-8") == "% a\n"
        assert (env["root"] / "parts" / "inner" / "b.tex").read_text(
            encoding="utf-8"
        ) == "% b\n"
        assert (env["root"] / "parts" / "fig.png").read_bytes() == b"\x89PNG"
        data = resp.json()
        paths = _tree_paths(data["file_tree"])
        assert "parts/a.tex" in paths
        assert "parts/inner/b.tex" in paths
        assert "src/a.tex" not in paths
        assert "parts" in _tree_dirs(data["file_tree"])
        assert "src" not in _tree_dirs(data["file_tree"])

    def test_move_onto_existing_file_returns_409(self, client, env):
        (env["root"] / "a.tex").write_text("% a\n", encoding="utf-8")
        (env["root"] / "b.tex").write_text("% b\n", encoding="utf-8")
        resp = _move(client, env, "a.tex", "b.tex")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "target_exists"
        assert str(env["root"]) not in resp.text
        # 双方原样保留（拒绝即无磁盘变更）
        assert (env["root"] / "a.tex").read_text(encoding="utf-8") == "% a\n"
        assert (env["root"] / "b.tex").read_text(encoding="utf-8") == "% b\n"

    def test_move_onto_existing_directory_returns_409(self, client, env):
        (env["root"] / "docs").mkdir()
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "docs", "chapters")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "target_exists"
        assert (env["root"] / "docs").is_dir()
        assert (env["root"] / "chapters").is_dir()

    def test_move_into_itself_returns_400_into_self(self, client, env):
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "chapters", "chapters")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "into_self"
        assert (env["root"] / "chapters").is_dir()
        resp = _move(client, env, "main.tex", "main.tex")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "into_self"
        assert (env["root"] / "main.tex").exists()

    def test_move_into_own_subtree_returns_400_into_self(self, client, env):
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "chapters", "chapters/nested")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "into_self"
        assert not (env["root"] / "chapters" / "nested").exists()

    def test_move_missing_parent_returns_400(self, client, env):
        resp = _move(client, env, "main.tex", "no_such_dir/main.tex")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "parent_not_found"
        assert str(env["root"]) not in resp.text
        assert (env["root"] / "main.tex").exists()
        assert not (env["root"] / "no_such_dir").exists()

    def test_move_missing_source_returns_404(self, client, env):
        resp = _move(client, env, "ghost.tex", "elsewhere.tex")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "file_not_found"
        assert detail["message"] == "File not found: ghost.tex"
        assert not (env["root"] / "elsewhere.tex").exists()

    def test_move_rejects_hidden_source(self, client, env):
        git_dir = env["root"] / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n", encoding="utf-8")
        resp = _move(client, env, ".git/config", "config")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert (git_dir / "config").exists()
        assert not (env["root"] / "config").exists()

    def test_move_rejects_hidden_target(self, client, env):
        (env["root"] / "notes.tex").write_text("% notes\n", encoding="utf-8")
        resp = _move(client, env, "notes.tex", ".latexcellence/notes.tex")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert (env["root"] / "notes.tex").exists()
        assert not (env["root"] / ".latexcellence").exists()

    def test_move_main_file_rewrites_record(self, client, env):
        # Q3：主文件被拖走 → 允许移动并自动更新记录（内存 +
        # .latexcellence/main_file 标记，响应带回改写后主文件）
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "main.tex", "chapters/main.tex")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["main_file"] == "chapters/main.tex"
        marker = env["root"] / ".latexcellence" / "main_file"
        assert marker.read_text(encoding="utf-8").strip() == "chapters/main.tex"
        # 重开项目（页面刷新直达 / 后端重启语义）后记录仍然生效
        from backend.core.workspace_manager import get_project_manager_for_workspace

        info = get_project_manager_for_workspace(env["ws"]).open(str(env["root"]))
        assert info.main_file == "chapters/main.tex"

    def test_move_folder_containing_main_file_rewrites_prefix(self, client, env):
        # §4.2 前缀改写：移动主文件的祖先目录，记录按「旧前缀 → 新前缀」改写
        chapters = env["root"] / "chapters"
        chapters.mkdir()
        (chapters / "thesis.tex").write_text("% thesis\n", encoding="utf-8")
        assert _set_main(client, env, "chapters/thesis.tex").status_code == 200
        resp = _move(client, env, "chapters", "parts")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["main_file"] == "parts/thesis.tex"
        marker = env["root"] / ".latexcellence" / "main_file"
        assert marker.read_text(encoding="utf-8").strip() == "parts/thesis.tex"
        assert (env["root"] / "parts" / "thesis.tex").read_text(
            encoding="utf-8"
        ) == "% thesis\n"
        from backend.core.workspace_manager import get_project_manager_for_workspace

        info = get_project_manager_for_workspace(env["ws"]).open(str(env["root"]))
        assert info.main_file == "parts/thesis.tex"

    def test_move_main_file_record_with_dot_prefix_normalizes(self, client, env):
        # 回归：主文件记录可能带「./」前缀（set-main-file / .latexcellence
        # 标记原样存储用户输入），前缀改写必须先规范化——否则切片产出目录
        # 路径，set_main_file 因后缀白名单失败，记录悬空在旧路径
        assert _set_main(client, env, "./main.tex").status_code == 200
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "main.tex", "chapters/main.tex")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["main_file"] == "chapters/main.tex"
        marker = env["root"] / ".latexcellence" / "main_file"
        assert marker.read_text(encoding="utf-8").strip() == "chapters/main.tex"
        assert (env["root"] / "chapters" / "main.tex").is_file()

    def test_move_unrelated_paths_keep_main_record(self, client, env):
        # 主文件不在移动子树内：记录原样保留，响应带回当前主文件
        (env["root"] / "other.tex").write_text("% other\n", encoding="utf-8")
        (env["root"] / "chapters").mkdir()
        resp = _move(client, env, "other.tex", "chapters/other.tex")
        assert resp.status_code == 200, resp.text
        assert resp.json()["main_file"] == "main.tex"


class TestCreateDir:
    """新建文件夹（POST /api/project/create-dir，editor-file-move-design
    §4.1）：成功（根 / 子目录）、重复 409 target_exists、父缺失 400
    parent_not_found、非法名 / 隐藏名拒绝。
    """

    def test_create_dir_at_root(self, client, env):
        resp = _create_dir(client, env, "figures")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["dir_path"] == "figures"
        assert (env["root"] / "figures").is_dir()
        assert "figures" in _tree_dirs(data["file_tree"])

    def test_create_dir_in_subdirectory(self, client, env):
        (env["root"] / "docs").mkdir()
        resp = _create_dir(client, env, "docs/notes")
        assert resp.status_code == 200, resp.text
        assert (env["root"] / "docs" / "notes").is_dir()
        assert "docs/notes" in _tree_dirs(resp.json()["file_tree"])

    def test_create_dir_duplicate_returns_409(self, client, env):
        (env["root"] / "figures").mkdir()
        resp = _create_dir(client, env, "figures")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "target_exists"
        assert str(env["root"]) not in resp.text
        assert (env["root"] / "figures").is_dir()

    def test_create_dir_name_taken_by_file_returns_409(self, client, env):
        resp = _create_dir(client, env, "main.tex")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "target_exists"
        assert (env["root"] / "main.tex").is_file()

    def test_create_dir_missing_parent_returns_400(self, client, env):
        # 单级 mkdir：不做递归创建，父目录缺失即拒绝
        resp = _create_dir(client, env, "no_such/inner")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "parent_not_found"
        assert str(env["root"]) not in resp.text
        assert not (env["root"] / "no_such").exists()

    def test_create_dir_rejects_invalid_names(self, client, env):
        # 与 create-file 同口径消毒：空名 / 控制字符 / 尾分隔符 / 反斜杠分隔符
        for bad in ("", "bad\x0bname", "trailing/", "back\\slash"):
            resp = _create_dir(client, env, bad)
            assert resp.status_code == 400, f"{bad!r}: {resp.text}"
            assert resp.json()["detail"]["error"] == "invalid_path"
        assert list(env["root"].iterdir()) == [env["root"] / "main.tex"]

    def test_create_dir_rejects_hidden_names(self, client, env):
        for bad in (".git", ".latexcellence", "docs/.hidden"):
            resp = _create_dir(client, env, bad)
            assert resp.status_code == 400, f"{bad!r}: {resp.text}"
            assert resp.json()["detail"]["error"] == "invalid_path"
        assert not (env["root"] / ".git").exists()
        assert not (env["root"] / ".latexcellence").exists()
        assert not (env["root"] / "docs").exists()


class TestUploadFile:
    """文件树上传（POST /api/project/upload-file，editor-file-upload-design §4）。

    任意格式 + §3 护栏：20MB 流式上限、文件名消毒、隐藏组件拒绝、
    resolve_project_path 不限后缀、同名冲突 409、错误消息相对路径脱敏。
    """

    PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"

    def test_upload_arbitrary_suffix_returns_rescanned_tree(self, client, env):
        resp = _upload(client, env, "figure.xyz", self.PNG_BYTES)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_path"] == "figure.xyz"
        # 响应携带重扫后的文件树，含任意后缀新资源（单次往返刷新）
        assert "figure.xyz" in _tree_paths(data["file_tree"])
        # 二进制字节保真落盘
        assert (env["root"] / "figure.xyz").read_bytes() == self.PNG_BYTES
        # 上传不产生残留临时文件
        assert {p.name for p in env["root"].iterdir()} == {"main.tex", "figure.xyz"}

    def test_upload_to_subdirectory(self, client, env):
        (env["root"] / "figures").mkdir()
        resp = _upload(client, env, "figures/fig.png", self.PNG_BYTES)
        assert resp.status_code == 200, resp.text
        assert "figures/fig.png" in _tree_paths(resp.json()["file_tree"])
        assert (env["root"] / "figures" / "fig.png").read_bytes() == self.PNG_BYTES

    def test_upload_oversized_rejected_before_write(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        monkeypatch.setattr(editor_service, "MAX_UPLOAD_FILE_BYTES", 16)
        resp = _upload(client, env, "big.xyz", b"x" * 100)
        assert resp.status_code == 413
        detail = resp.json()["detail"]
        assert detail["error"] == "file_too_large"
        assert not (env["root"] / "big.xyz").exists()
        assert {p.name for p in env["root"].iterdir()} == {"main.tex"}

    def test_upload_duplicate_returns_409(self, client, env):
        resp = _upload(client, env, "main.tex", b"overwrite?")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "file_exists"
        # 多用户脱敏约束：错误消息不得携带服务器绝对路径
        assert str(env["root"]) not in resp.text
        assert (
            (env["root"] / "main.tex")
            .read_text(encoding="utf-8")
            .startswith("\\documentclass")
        )

    def test_upload_missing_parent_returns_400(self, client, env):
        resp = _upload(client, env, "no_such_dir/fig.png", self.PNG_BYTES)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "parent_not_found"
        assert str(env["root"]) not in resp.text
        assert not (env["root"] / "no_such_dir").exists()

    def test_upload_rejects_hidden_directory(self, client, env):
        resp = _upload(client, env, ".git/config", b"evil")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert not (env["root"] / ".git").exists()

    def test_upload_rejects_dot_prefixed_filename(self, client, env):
        resp = _upload(client, env, ".hidden.png", self.PNG_BYTES)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert not (env["root"] / ".hidden.png").exists()

    def test_upload_rejects_control_char_filename(self, client, env):
        resp = _upload(client, env, "bad\x0bname.png", self.PNG_BYTES)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert list(env["root"].glob("bad*")) == []

    def test_upload_rejects_empty_filename(self, client, env):
        # 尾分隔符 → 目标文件名为空（§3-6 拒绝空名）
        resp = _upload(client, env, "figures/", self.PNG_BYTES)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"

    def test_upload_rejects_backslash_separator(self, client, env):
        # Windows 分隔符走私（§3-6 拒绝路径分隔符）
        resp = _upload(client, env, "figures\\fig.png", self.PNG_BYTES)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert list(env["root"].glob("*fig*")) == []

    def test_raw_file_still_rejects_non_previewable_suffix(self, client, env):
        # §3-1 回读边界不变：上传成功 ≠ 可被服务回浏览器
        (env["root"] / "data.xyz").write_bytes(b"whatever")
        listed = client.get(
            "/api/project/raw-file",
            params={"file_path": "data.xyz", "workspace_id": env["ws"]},
        )
        assert listed.status_code == 400
        assert listed.json()["detail"]["error"] == "invalid_path"


class TestArtifactBlacklist:
    """文件树扫描产物黑名单（project_manager._scan_directory，决策 D7）：
    收录条件从后缀白名单放开为「非隐藏 + 非编译产物」，.pdf 不进黑名单。"""

    ARTIFACTS = (
        "main.aux",
        "main.log",
        "main.toc",
        "main.fls",
        "main.fdb_latexmk",
        "main.bbl",
        "main.out",
        "notes.bak",
    )

    def test_artifacts_excluded_from_tree_but_resources_kept(self, client, env):
        for name in (*self.ARTIFACTS, "out.synctex.gz", "main.run.xml", "paper.pdf"):
            (env["root"] / name).write_bytes(b"x")
        # 触发一次重扫（上传响应自带重扫后整树）
        resp = _upload(client, env, "figure.xyz", b"data")
        assert resp.status_code == 200, resp.text
        paths = _tree_paths(resp.json()["file_tree"])
        # 双后缀（.synctex.gz / .run.xml）与单后缀产物均不进树
        for name in (*self.ARTIFACTS, "out.synctex.gz", "main.run.xml"):
            assert name not in paths
        # .pdf 不进黑名单（编译产物 PDF 可见现状保留）；资源与源文件保留
        assert "paper.pdf" in paths
        assert "figure.xyz" in paths
        assert "main.tex" in paths
        # 产物仍真实存在于项目（可引用、随下载），只是不进树
        assert (env["root"] / "out.synctex.gz").exists()

    def test_subdirectory_artifacts_excluded_while_dir_kept(self, client, env):
        (env["root"] / "chapters").mkdir()
        (env["root"] / "chapters" / "intro.aux").write_bytes(b"x")
        (env["root"] / "chapters" / "intro.tex").write_text("% intro", encoding="utf-8")
        resp = _upload(client, env, "chapters/fig.svg", b"data")
        assert resp.status_code == 200, resp.text
        paths = _tree_paths(resp.json()["file_tree"])
        assert "chapters/intro.aux" not in paths
        assert "chapters/intro.tex" in paths
        assert "chapters/fig.svg" in paths


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

r"""编辑器跨文件大纲接口测试（单用户模式，editor-experience-design §4.4 阶段四）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络、零写盘。
覆盖：多文件分组（子目录、无条目文件不进响应）、相对路径（多用户脱敏）、
分节口径（星号变体 / 可选参数 / 注释剔除 / verbatim 豁免 / 标题净化）、
项目级缓存命中与保存失效（save-file 后重扫）、非 .tex 排除、隐藏目录跳过、
符号链接逃逸拒绝、空项目、未打开项目的 invalid_workspace 语义。
"""

import os

import pytest

MAIN_TEX = r"""\documentclass{article}
\begin{document}
\section{Intro}
\subsection*{Method [detail]}
% \section{Ghost} commented section must be ignored
\subsubsection{Deep \textbf{Bold} head}
\end{document}
"""

CHAPTER_TEX = r"""% chapter file
\section{Experiments}
\paragraph{Run-in head}
"""

EMPTY_TEX = r"""\documentclass{article}
No section commands here.
"""


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from main import app

    return TestClient(app)


@pytest.fixture
def env(client, tmp_path):
    """已打开临时项目的单用户工作区；结束释放工作区并清空大纲缓存。"""
    from backend.core.workspace_manager import (
        get_project_manager_for_workspace,
        get_workspace_manager,
    )
    from backend.modules.editor import service as editor_service

    ws_id = "editor-outline"
    (tmp_path / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    (tmp_path / "empty.tex").write_text(EMPTY_TEX, encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / "experiments.tex").write_text(
        CHAPTER_TEX, encoding="utf-8"
    )
    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    editor_service._outline_cache.clear()
    yield {"ws": ws_id, "root": tmp_path}
    editor_service._outline_cache.clear()
    get_workspace_manager().remove(ws_id)


def _outline(client, env):
    return client.get("/api/project/outline", params={"workspace_id": env["ws"]})


def _files_by_path(data):
    return {f["file_path"]: f["entries"] for f in data["files"]}


class TestMultiFileOutline:
    def test_files_grouped_with_relative_paths_and_lines(self, client, env):
        resp = _outline(client, env)
        assert resp.status_code == 200
        data = resp.json()
        files = _files_by_path(data)
        # 有条目的文件按相对路径分组（子目录覆盖）
        assert set(files) == {"main.tex", "chapters/experiments.tex"}
        by_title = {e["title"]: e for e in files["main.tex"]}
        assert by_title["Intro"] == {"level": 0, "title": "Intro", "line": 3}
        # 星号变体 + 可选参数 [detail] 跳过后取强制参数
        assert by_title["Method [detail]"]["level"] == 1
        assert by_title["Method [detail]"]["line"] == 4
        # 标题命令净化：\textbf{Bold} → Bold
        assert by_title["Deep Bold head"] == {
            "level": 2,
            "title": "Deep Bold head",
            "line": 6,
        }
        chapter_by_title = {e["title"]: e for e in files["chapters/experiments.tex"]}
        assert chapter_by_title["Run-in head"] == {
            "level": 3,
            "title": "Run-in head",
            "line": 3,
        }

    def test_commented_section_ignored(self, client, env):
        resp = _outline(client, env)
        assert "Ghost" not in resp.text

    def test_file_without_entries_excluded(self, client, env):
        resp = _outline(client, env)
        assert "empty.tex" not in resp.text

    def test_non_tex_files_excluded(self, client, env):
        (env["root"] / "refs.bib").write_text(
            "@article{a,\n title = {fake \\section{Nope}},\n}\n", encoding="utf-8"
        )
        (env["root"] / "style.sty").write_text(
            "\\section{sty fake}\n", encoding="utf-8"
        )
        resp = _outline(client, env)
        assert resp.status_code == 200
        assert "Nope" not in resp.text
        assert "sty fake" not in resp.text
        assert "refs.bib" not in resp.text

    def test_hidden_directory_skipped(self, client, env):
        hidden = env["root"] / ".archive"
        hidden.mkdir()
        (hidden / "old.tex").write_text("\\section{Buried}\n", encoding="utf-8")
        resp = _outline(client, env)
        assert "Buried" not in resp.text

    def test_verbatim_section_ignored(self, client, env):
        (env["root"] / "verb.tex").write_text(
            "\\begin{verbatim}\n\\section{inside}\n\\end{verbatim}\n"
            "\\section{after}\n",
            encoding="utf-8",
        )
        resp = _outline(client, env)
        entries = _files_by_path(resp.json())["verb.tex"]
        assert [e["title"] for e in entries] == ["after"]


class TestCacheAndInvalidation:
    def test_cache_hit_until_save_invalidates(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        calls = {"n": 0}
        real_scan = editor_service._scan_outline_sync

        def counting_scan(project):
            calls["n"] += 1
            return real_scan(project)

        monkeypatch.setattr(editor_service, "_scan_outline_sync", counting_scan)

        first = _outline(client, env)
        assert first.status_code == 200
        # 磁盘直接改文件（绕过 save-file）：缓存命中，结果保持旧扫描
        (env["root"] / "main.tex").write_text(
            MAIN_TEX + "\\section{Fresh From Disk}\n", encoding="utf-8"
        )
        second = _outline(client, env)
        assert second.status_code == 200
        assert "Fresh From Disk" not in second.text
        assert calls["n"] == 1

        # 经 save-file 落盘（编辑器保存单一路径）：缓存失效，重扫命中新分节
        saved = client.post(
            "/api/project/save-file",
            json={
                "file_path": "main.tex",
                "content": MAIN_TEX + "\\section{Saved Section}\n",
                "workspace_id": env["ws"],
            },
        )
        assert saved.status_code == 200
        third = _outline(client, env)
        assert third.status_code == 200
        assert "Saved Section" in third.text
        assert calls["n"] == 2


class TestScanSafety:
    def test_response_contains_no_absolute_paths(self, client, env):
        resp = _outline(client, env)
        assert resp.status_code == 200
        assert str(env["root"]) not in resp.text

    def test_symlink_escaping_project_rejected(self, client, env, tmp_path_factory):
        outside_dir = tmp_path_factory.mktemp("outside")
        outside_file = outside_dir / "leaked.tex"
        outside_file.write_text("\\section{Leaked}\n", encoding="utf-8")
        link = env["root"] / "leaked.tex"
        os.symlink(outside_file, link)
        try:
            resp = _outline(client, env)
            assert resp.status_code == 200
            assert "Leaked" not in resp.text
        finally:
            link.unlink()

    def test_empty_project_returns_empty_files(self, client, tmp_path):
        from backend.core.workspace_manager import (
            get_project_manager_for_workspace,
            get_workspace_manager,
        )
        from backend.modules.editor import service as editor_service

        ws_id = "editor-outline-empty"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(tmp_path))
        editor_service._outline_cache.clear()
        try:
            resp = client.get("/api/project/outline", params={"workspace_id": ws_id})
            assert resp.status_code == 200
            assert resp.json() == {"files": []}
        finally:
            editor_service._outline_cache.clear()
            get_workspace_manager().remove(ws_id)

    def test_unopened_workspace_rejected(self, client):
        from backend.core.workspace_manager import get_workspace_manager

        ws_id = "editor-outline-unbound"
        get_workspace_manager().get_or_create(ws_id)
        try:
            resp = client.get("/api/project/outline", params={"workspace_id": ws_id})
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert detail["error"] == "invalid_workspace"
        finally:
            get_workspace_manager().remove(ws_id)

    def test_missing_workspace_param_rejected(self, client):
        resp = client.get("/api/project/outline")
        assert resp.status_code == 422

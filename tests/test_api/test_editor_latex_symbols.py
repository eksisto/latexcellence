r"""编辑器引用符号扫描接口测试（单用户模式，editor-productivity-plan §5.2 阶段四）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖：\label 提取（含 1 基行号、跨文件）、\cite 可选参数与多 key 拆分、
bib 条目 key/标题提取（bib_parser 复用）、注释行剔除与转义百分号放行、
隐藏目录跳过、符号链接逃逸拒绝（resolve_project_path 白名单）、响应无
服务器绝对路径（多用户脱敏约束）、未打开项目的 invalid_workspace 语义。
"""

import os

import pytest

MAIN_TEX = r"""\documentclass{article}
\begin{document}
\section{Intro}\label{sec:intro}
As shown in \cite[p.~5]{smith2020, lee2019} and \cite{doe2021}.
Natbib \citep{ross2014} and \citet[see][p.~3]{watts2018}; all \nocite{*}.
% \label{ghost} commented label must be ignored
Escaped percent 100\% keeps \label{sec:pct}.
\end{document}
"""

CHAPTER_TEX = r"""% chapter file
\subsection{Method}\label{sec:method}
See \cref{sec:intro}.
"""

REFS_BIB = """@article{smith2020,
  title = {A {Study} of Latex},
  author = {Smith, J.},
  year = {2020},
}

@book{lee2019,
  title = {Deep Trees},
  year = {2019},
}
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

    ws_id = "editor-latex-symbols"
    (tmp_path / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / "method.tex").write_text(CHAPTER_TEX, encoding="utf-8")
    (tmp_path / "refs.bib").write_text(REFS_BIB, encoding="utf-8")
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    yield {"ws": ws_id, "root": tmp_path}
    get_workspace_manager().remove(ws_id)


def _scan(client, env):
    return client.get("/api/project/latex-symbols", params={"workspace_id": env["ws"]})


class TestLabelExtraction:
    def test_labels_with_relative_path_and_one_based_line(self, client, env):
        resp = _scan(client, env)
        assert resp.status_code == 200
        labels = resp.json()["labels"]
        by_key = {item["key"]: item for item in labels}
        assert by_key["sec:intro"]["file_path"] == "main.tex"
        assert by_key["sec:intro"]["line"] == 3
        # 跨文件命中：子目录文件同样被扫描
        assert by_key["sec:method"]["file_path"] == "chapters/method.tex"
        assert by_key["sec:method"]["line"] == 2

    def test_commented_label_ignored_and_escaped_percent_kept(self, client, env):
        resp = _scan(client, env)
        keys = {item["key"] for item in resp.json()["labels"]}
        assert "ghost" not in keys
        assert "sec:pct" in keys


class TestCiteExtraction:
    def test_bib_entries_first_with_title_then_tex_cited_keys(self, client, env):
        resp = _scan(client, env)
        cites = resp.json()["cites"]
        by_key = {item["key"]: item for item in cites}
        # bib 条目：key + 净化后标题 + kind=bib
        assert by_key["smith2020"]["kind"] == "bib"
        assert by_key["smith2020"]["file_path"] == "refs.bib"
        assert by_key["smith2020"]["title"] == "A Study of Latex"
        # .tex 已引用但 bib 未录入的 key：kind=tex、只带文件出处
        assert by_key["doe2021"]["kind"] == "tex"
        assert by_key["doe2021"]["file_path"] == "main.tex"
        assert "title" not in by_key["doe2021"]
        # bib 与 .tex 双重出现的 key 只保留 bib 条目一份
        assert sum(1 for item in cites if item["key"] == "smith2020") == 1

    def test_optional_param_and_multi_key_cite_split(self, client, env):
        resp = _scan(client, env)
        keys = {item["key"] for item in resp.json()["cites"]}
        # \cite[p.~5]{smith2020, lee2019}：可选参数被跳过、多 key 拆分
        assert {"smith2020", "lee2019", "doe2021"} <= keys

    def test_natbib_variants_and_wildcard_excluded(self, client, env):
        resp = _scan(client, env)
        cites = resp.json()["cites"]
        keys = {item["key"] for item in cites}
        # natbib 家族（\citep/\citet，含堆叠可选参数 [see][p.~3]）同样拆 key
        assert {"ross2014", "watts2018"} <= keys
        for item in cites:
            if item["key"] in {"ross2014", "watts2018"}:
                assert item["kind"] == "tex"
                assert item["file_path"] == "main.tex"
        # \nocite{*} 通配（"引用全部"）不是真实 key，不得进候选
        assert "*" not in keys


class TestScanSafety:
    def test_response_contains_no_absolute_paths(self, client, env):
        resp = _scan(client, env)
        assert resp.status_code == 200
        assert str(env["root"]) not in resp.text

    def test_hidden_directory_skipped(self, client, env):
        hidden = env["root"] / ".archive"
        hidden.mkdir()
        (hidden / "old.tex").write_text(r"\label{buried}", encoding="utf-8")
        resp = _scan(client, env)
        assert "buried" not in resp.text

    def test_oversized_file_skipped_before_read(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        big = env["root"] / "big.tex"
        big.write_text("\\label{bigfile}\n" * 20, encoding="utf-8")
        # 压低上限：字节预检在读入前生效，超限文件整个跳过
        monkeypatch.setattr(editor_service, "MAX_SYMBOL_SCAN_CHARS", 10)
        resp = _scan(client, env)
        assert resp.status_code == 200
        assert "bigfile" not in resp.text

    def test_symlink_escaping_project_rejected(self, client, env, tmp_path_factory):
        outside_dir = tmp_path_factory.mktemp("outside")
        outside_file = outside_dir / "leaked.tex"
        outside_file.write_text(r"\label{leaked}", encoding="utf-8")
        link = env["root"] / "leaked.tex"
        os.symlink(outside_file, link)
        try:
            resp = _scan(client, env)
            assert resp.status_code == 200
            assert "leaked" not in resp.text
        finally:
            link.unlink()

    def test_unopened_workspace_rejected(self, client):
        from backend.core.workspace_manager import get_workspace_manager

        ws_id = "editor-latex-symbols-unbound"
        get_workspace_manager().get_or_create(ws_id)
        try:
            resp = client.get(
                "/api/project/latex-symbols", params={"workspace_id": ws_id}
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert detail["error"] == "invalid_workspace"
        finally:
            get_workspace_manager().remove(ws_id)

    def test_missing_workspace_param_rejected(self, client):
        resp = client.get("/api/project/latex-symbols")
        assert resp.status_code == 422

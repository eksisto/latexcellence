r"""编辑器一键格式化接口测试（单用户模式，editor-productivity-design §5.2 / 决策 Q8，阶段五）。

纯转换端点：Mock format_latex_source（latex_formatter 统一入口），无真实
latexindent 子进程、无 LLM、无网络。
覆盖：格式化结果透传与 changed 标记、原文一致（changed=False）、空白源码、
大小上限 413 content_too_long、未打开项目 invalid_workspace、缺少
workspace_id 422、格式化意外异常 500 format_failed、请求体携带 file_path
被忽略（不涉路径）、零写盘（磁盘文件内容不变）。
"""

import pytest

RAW_SOURCE = (
    "\\begin{document}\n\\begin{itemize}\n\\item x\n\\end{itemize}\n\\end{document}\n"
)
FORMATTED_SOURCE = (
    "\\begin{document}\n"
    "    \\begin{itemize}\n"
    "        \\item x\n"
    "    \\end{itemize}\n"
    "\\end{document}\n"
)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture
def env(client, tmp_path):
    """已打开临时项目的单用户工作区；结束释放工作区，避免跨用例残留。"""
    from backend.core.workspace_manager import get_workspace_manager

    ws_id = "editor-format-latex"
    disk_file = tmp_path / "main.tex"
    disk_file.write_text(RAW_SOURCE, encoding="utf-8")
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    yield {"ws": ws_id, "root": tmp_path, "disk_file": disk_file}
    get_workspace_manager().remove(ws_id)


@pytest.fixture
def mock_format(monkeypatch):
    """Mock service 层引用的 latex_formatter 统一入口（无真实子进程）。"""
    from backend.modules.editor import service as editor_service

    calls = {"sources": []}

    async def fake_format_latex_source(source: str) -> str:
        calls["sources"].append(source)
        return FORMATTED_SOURCE

    monkeypatch.setattr(editor_service, "format_latex_source", fake_format_latex_source)
    return calls


def _format(client, env, payload, ws=None):
    return client.post(
        "/api/project/format-latex",
        params={"workspace_id": ws if ws is not None else env["ws"]},
        json=payload,
    )


class TestPureConversion:
    def test_formatted_content_returned_with_changed_flag(
        self, client, env, mock_format
    ):
        resp = _format(client, env, {"content": RAW_SOURCE})
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == FORMATTED_SOURCE
        assert data["changed"] is True
        # 转换输入即请求体内容（不读磁盘、不涉路径）
        assert mock_format["sources"] == [RAW_SOURCE]

    def test_unchanged_result_reports_changed_false(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        async def identity(source: str) -> str:
            return source

        monkeypatch.setattr(editor_service, "format_latex_source", identity)
        resp = _format(client, env, {"content": FORMATTED_SOURCE})
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == FORMATTED_SOURCE
        assert data["changed"] is False

    def test_blank_content_passthrough(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        called = {"n": 0}

        async def identity(source: str) -> str:
            called["n"] += 1
            return source

        monkeypatch.setattr(editor_service, "format_latex_source", identity)
        resp = _format(client, env, {"content": "  \n "})
        assert resp.status_code == 200
        assert resp.json()["changed"] is False

    def test_zero_write_to_disk(self, client, env, mock_format):
        """纯转换约束：格式化不触碰项目磁盘文件。"""
        resp = _format(client, env, {"content": RAW_SOURCE})
        assert resp.status_code == 200
        assert env["disk_file"].read_text(encoding="utf-8") == RAW_SOURCE

    def test_file_path_field_ignored(self, client, env, mock_format):
        """不涉路径约束：请求体携带 file_path 等路径字段被忽略，不产生路径校验。"""
        resp = _format(
            client,
            env,
            {"content": RAW_SOURCE, "file_path": "../../etc/passwd"},
        )
        assert resp.status_code == 200
        assert resp.json()["changed"] is True


class TestGuards:
    def test_content_too_long_rejected_with_413(self, client, env, monkeypatch):
        from backend.modules.editor import service as editor_service

        monkeypatch.setattr(editor_service, "MAX_EDITABLE_CHARS", 10)
        resp = _format(client, env, {"content": "a" * 11})
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "content_too_long"

    def test_unopened_workspace_rejected(self, client):
        from backend.core.workspace_manager import get_workspace_manager

        ws_id = "editor-format-latex-unbound"
        get_workspace_manager().get_or_create(ws_id)
        try:
            resp = _format(client, {"ws": None}, {"content": RAW_SOURCE}, ws=ws_id)
            assert resp.status_code == 400
            assert resp.json()["detail"]["error"] == "invalid_workspace"
        finally:
            get_workspace_manager().remove(ws_id)

    def test_missing_workspace_param_rejected(self, client):
        resp = client.post("/api/project/format-latex", json={"content": "x"})
        assert resp.status_code == 422

    def test_missing_content_rejected(self, client, env):
        resp = client.post(
            "/api/project/format-latex",
            params={"workspace_id": env["ws"]},
            json={},
        )
        assert resp.status_code == 422

    def test_format_failure_maps_to_stable_error(self, client, env, monkeypatch):
        """格式化意外异常收敛为 500 format_failed（三语 errors 命名空间消费）。"""
        from backend.modules.editor import service as editor_service

        async def boom(source: str) -> str:
            raise RuntimeError("formatter exploded")

        monkeypatch.setattr(editor_service, "format_latex_source", boom)
        resp = _format(client, env, {"content": RAW_SOURCE})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "format_failed"
        # 错误消息不含内部细节 / 服务器路径
        assert "formatter exploded" not in detail["message"]

"""编辑器整文件读写接口测试（单用户模式，editor-plan §5.1 阶段一）。

纯文件操作：无 LLM 参与、不 Mock LLM、无网络。
覆盖：打开读取、正常保存、MD5 冲突 409（含相对路径脱敏）、内容损坏特征
拦截（配平中间态放行）、保存后 undo 回退恢复原文；以及 review 回归：
CRLF 字节保真（content 与 md5 对应同一份磁盘字节）、非 UTF-8 稳定错误码
invalid_encoding、内容校验卸载线程池（事件循环保护）。
"""

import hashlib
import threading

import pytest

MAIN_TEX = r"""\documentclass{article}
\begin{document}
Hello world.
\end{document}
"""


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture
def env(client, tmp_path):
    """已打开临时项目的单用户工作区；结束释放工作区，避免跨用例残留。"""
    from backend.core.workspace_manager import get_workspace_manager

    ws_id = "editor-file-io"
    (tmp_path / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    yield {"ws": ws_id, "root": tmp_path}
    get_workspace_manager().remove(ws_id)


def _get(client, env, file_path="main.tex"):
    return client.get(
        "/api/project/file-content",
        params={"file_path": file_path, "workspace_id": env["ws"]},
    )


def _save(client, env, content, base_md5=_md5(MAIN_TEX), file_path="main.tex"):
    body = {"file_path": file_path, "workspace_id": env["ws"], "content": content}
    if base_md5 is not None:
        body["base_md5"] = base_md5
    return client.post("/api/project/save-file", json=body)


class TestOpenFileContent:
    def test_returns_content_and_md5(self, client, env):
        resp = _get(client, env)
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == MAIN_TEX
        assert data["md5"] == _md5(MAIN_TEX)

    def test_rejects_suffix_outside_whitelist(self, client, env):
        (env["root"] / "notes.txt").write_text("plain", encoding="utf-8")
        resp = _get(client, env, "notes.txt")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "invalid_path"
        # 多用户脱敏约束：错误消息不得携带服务器绝对路径
        assert str(env["root"]) not in resp.text

    def test_missing_file_returns_404(self, client, env):
        resp = _get(client, env, "no_such.tex")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "file_not_found"
        # 错误消息使用相对路径
        assert detail["message"] == "File not found: no_such.tex"

    def test_rejects_path_traversal(self, client, env, tmp_path):
        outside = tmp_path.parent / "evil.tex"
        outside.write_text("secret", encoding="utf-8")
        try:
            resp = _get(client, env, "../evil.tex")
            assert resp.status_code == 400
            assert "secret" not in resp.text
        finally:
            outside.unlink(missing_ok=True)


class TestSaveFile:
    def test_save_writes_file_and_returns_new_md5(self, client, env):
        new_content = MAIN_TEX.replace("Hello world.", "Hola mundo.")
        resp = _save(client, env, new_content)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_path"] == "main.tex"
        assert data["md5"] == _md5(new_content)
        assert (env["root"] / "main.tex").read_text(encoding="utf-8") == new_content

    def test_save_without_base_md5_skips_optimistic_lock(self, client, env):
        new_content = MAIN_TEX.replace("Hello world.", "Edited.")
        resp = _save(client, env, new_content, base_md5=None)
        assert resp.status_code == 200
        assert (env["root"] / "main.tex").read_text(encoding="utf-8") == new_content

    def test_conflict_returns_structured_409(self, client, env):
        stale_md5 = _md5("outdated")
        resp = _save(client, env, "New content.\n", base_md5=stale_md5)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "file_conflict"
        assert detail["file_path"] == "main.tex"
        assert detail["expected_md5"] == stale_md5
        assert detail["current_md5"] == _md5(MAIN_TEX)
        # 错误消息使用相对路径，不得拼接项目绝对路径（多用户脱敏规则）
        assert str(env["root"]) not in resp.text
        # 冲突时磁盘内容保持不变
        assert (env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX

    def test_conflict_message_has_no_absolute_path(self, client, env):
        resp = _save(client, env, "X.\n", base_md5="deadbeef")
        assert resp.status_code == 409
        assert str(env["root"]) not in resp.json()["detail"]["message"]

    def test_rejects_corrupted_control_character(self, client, env):
        corrupted = "Broken:\x0bvertical tab is a corruption signature\n"
        resp = _save(client, env, corrupted)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_content"
        assert (env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX

    def test_rejects_garbage_placeholder(self, client, env):
        resp = _save(client, env, "value = [object Object]\n")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_content"

    def test_allows_tab_and_crlf_whitespace(self, client, env):
        # \t / \r 是合法 LaTeX 空白（宽松口径），CRLF 行尾不得误拦
        content = "Line one\r\n\tIndented line.\r\n"
        resp = _save(client, env, content)
        assert resp.status_code == 200, resp.text
        # 以字节比对：绕开 read_text 的通用换行归一化，验证 \r 原样落盘
        raw = (env["root"] / "main.tex").read_bytes().decode("utf-8")
        assert raw == content

    def test_allows_unbalanced_mid_edit_state(self, client, env):
        # 编辑中间态合法：不做括号/$ 配平检查（editor-plan §5.1 关键约束）
        current = MAIN_TEX
        for partial in ("Half opened brace: {", r"Unclosed math: $x + y"):
            new_content = partial + "\n"
            resp = _save(client, env, new_content, base_md5=_md5(current))
            assert resp.status_code == 200, resp.text
            current = new_content

    def test_rejects_non_whitelisted_suffix(self, client, env):
        resp = _save(client, env, "plain", file_path="notes.txt")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_path"
        assert not (env["root"] / "notes.txt").exists()

    def test_save_then_undo_restores_original(self, client, env):
        new_content = MAIN_TEX.replace("Hello world.", "Replaced by editor.")
        saved = _save(client, env, new_content)
        assert saved.status_code == 200

        undo_resp = client.post(
            "/api/project/undo",
            params={"workspace_id": env["ws"]},
            json={"file_path": "main.tex"},
        )
        assert undo_resp.status_code == 200, undo_resp.text
        assert undo_resp.json()["undone"] is True

        # undo 后重拉文件内容：原文完整恢复
        reopened = _get(client, env)
        assert reopened.status_code == 200
        assert reopened.json()["content"] == MAIN_TEX
        assert reopened.json()["md5"] == _md5(MAIN_TEX)

    def test_undo_redo_roundtrip(self, client, env):
        new_content = MAIN_TEX.replace("Hello world.", "Roundtrip.")
        assert _save(client, env, new_content).status_code == 200
        params = {"workspace_id": env["ws"]}
        assert (
            client.post(
                "/api/project/undo", params=params, json={"file_path": "main.tex"}
            ).status_code
            == 200
        )
        redo_resp = client.post(
            "/api/project/redo", params=params, json={"file_path": "main.tex"}
        )
        assert redo_resp.status_code == 200
        assert redo_resp.json()["redone"] is True
        assert (env["root"] / "main.tex").read_text(encoding="utf-8") == new_content


class TestByteFidelity:
    """review 回归：编辑器读写面对换行符字节保真。

    safe_read/read_text 的通用换行归一化会把 \\r\\n 改写为 \\n，导致
    {content, md5} 不一致、"打开即保存"把 CRLF 文件整体翻转为 LF。
    """

    CRLF_TEX = "First line.\r\n\tSecond indented.\r\nThird.\r\n"

    def test_open_returns_content_matching_md5_bytes(self, client, env):
        (env["root"] / "crlf.tex").write_bytes(self.CRLF_TEX.encode("utf-8"))
        resp = _get(client, env, "crlf.tex")
        assert resp.status_code == 200
        data = resp.json()
        # \r\n 原样返回，md5 与磁盘真实字节一致
        assert data["content"] == self.CRLF_TEX
        assert data["md5"] == _md5(self.CRLF_TEX)

    def test_noop_save_preserves_crlf_bytes_exactly(self, client, env):
        """打开→原样保存的完整 API 往返：磁盘字节不得有任何改动。

        回归锚点：修复前 GET 返回经通用换行归一化的 LF 文本，客户端
        原样存回会把整个 CRLF 文件静默翻转为 LF。"""
        path = env["root"] / "crlf.tex"
        path.write_bytes(self.CRLF_TEX.encode("utf-8"))
        opened = _get(client, env, "crlf.tex")
        assert opened.status_code == 200
        data = opened.json()
        # 客户端未做任何编辑：把接口返回的 content/md5 原样传回
        resp = _save(
            client, env, data["content"], base_md5=data["md5"], file_path="crlf.tex"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["md5"] == data["md5"]
        # 打开即保存（内容原样传回）不得改动任何磁盘字节
        assert path.read_bytes() == self.CRLF_TEX.encode("utf-8")


class TestInvalidEncoding:
    """review 回归：非 UTF-8 文件返回稳定错误码 invalid_encoding（非
    invalid_path），错误消息仅含相对路径。"""

    GBK_BYTES = b"\xd6\xd0\xce\xc4"  # GBK 编码的"中文"

    def test_open_non_utf8_reports_invalid_encoding(self, client, env):
        (env["root"] / "gbk.tex").write_bytes(self.GBK_BYTES)
        resp = _get(client, env, "gbk.tex")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "invalid_encoding"
        assert "gbk.tex" in detail["message"]
        # 多用户脱敏约束：错误消息不得携带服务器绝对路径
        assert str(env["root"]) not in resp.text

    def test_save_to_non_utf8_target_rejected_before_write(self, client, env):
        path = env["root"] / "gbk.tex"
        path.write_bytes(self.GBK_BYTES)
        resp = _save(client, env, "New content.\n", base_md5=None, file_path="gbk.tex")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_encoding"
        # 校验失败发生在落盘前：目标文件字节保持不变
        assert path.read_bytes() == self.GBK_BYTES


class TestEventLoopProtection:
    """review 回归：整文件损坏特征扫描（最大约 2MB 文本的 CPU 密集操作）
    必须卸载到线程池执行，不得阻塞事件循环（AGENTS.md 异步规范）。"""

    @pytest.mark.asyncio
    async def test_corruption_check_runs_off_event_loop(self, monkeypatch):
        from fastapi import HTTPException

        from backend.modules.editor import service as editor_service

        seen = {}

        def fake_check(_value):
            seen["thread_ident"] = threading.get_ident()
            return "corrupted"

        monkeypatch.setattr(editor_service, "check_latex_corruption", fake_check)
        with pytest.raises(HTTPException) as exc_info:
            await editor_service.save_file_content(
                None,
                {},
                file_path="main.tex",
                content="anything",
                base_md5=None,
                actor_user_id=None,
            )
        assert exc_info.value.status_code == 400
        # 校验在工作线程执行（事件循环所在线程 != 执行线程）
        assert seen["thread_ident"] != threading.get_ident()

"""编辑器侧 AI 助手测试（editor-plan §5.2 / §7，阶段四）。

覆盖：
    - build_editor_context 自适应上下文注入（小文件全文 / 大文件摘要+
      光标邻域+选区 / 手动"纳入全文"开关）——service 层纯函数直测
    - extract_suggestions 建议提取（latex 片段、diff 块、非建议围栏忽略、
      未闭合围栏丢弃）
    - POST /api/writing-chat/editor/send SSE 端点（**Mock LLM，严禁真实网络**）：
      token 流、done 携带结构化建议、会话持久化与消息级标记初始态
    - editor.assistant_enabled=false 时服务端拒绝助手端点（决策 Q26）
    - POST /api/writing-chat/editor/apply-suggestion 消息级应用标记记录
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import get_config
from backend.core.llm_client import StreamChunk, UsageInfo
from backend.core.workspace_manager import get_workspace_manager
from backend.modules.writing_chat.context import ConversationContext
from backend.modules.writing_chat.models import ChatMessage

_LLM_CLIENT_PATH = "backend.modules.writing_chat.service._deps.get_llm_client"

MAIN_TEX = r"""\documentclass{article}
\begin{document}
Hello world.
\end{document}
"""


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture
def temp_session_dir(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "writing_chat_sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(
        get_config().features.writing_chat,
        "sessions_dir",
        str(sessions_dir),
    )
    return sessions_dir


@pytest.fixture
def env(client, tmp_path):
    """已打开临时项目的单用户工作区；结束释放工作区。"""
    from backend.core.workspace_manager import get_project_manager_for_workspace

    ws_id = "editor-assistant"
    (tmp_path / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    pm = get_project_manager_for_workspace(ws_id)
    pm.open(str(tmp_path))
    yield {"ws": ws_id, "root": tmp_path}
    get_workspace_manager().remove(ws_id)


def _stream(chunks: list[StreamChunk]):
    async def gen():
        for c in chunks:
            yield c

    return gen()


def _mock_llm(chunks: list[StreamChunk]):
    """构造 Mock LLM 客户端：纯流式与工具流式均返回预置分片（无网络）。

    项目已打开时 project_tools 默认启用，编辑器助手走 chat_stream_with_tools
    路径；两条路径都挂同一份分片以覆盖配置差异。
    """
    m_client = MagicMock()
    m_client.chat_stream_with_usage = MagicMock(return_value=_stream(chunks))
    m_client.chat_stream_with_tools = MagicMock(return_value=_stream(chunks))
    m_llm = MagicMock()
    m_llm.return_value = m_client
    return m_llm, m_client


def _parse_sse(text: str) -> list[dict]:
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            events.append(json.loads(frame[len("data:") :]))
    return events


def _send_request(client, env, body: dict):
    return client.post(
        "/api/writing-chat/editor/send",
        params={"workspace_id": env["ws"]},
        json={
            "file_path": "main.tex",
            "buffer": MAIN_TEX,
            "cursor_line": 3,
            "selection": "",
            "user_message": "把问候改成西班牙语",
            **body,
        },
    )


# ── 纯函数：上下文注入构建逻辑（editor-plan §7.3 / 决策 Q24） ───────────────


class TestBuildEditorContext:
    def test_small_file_includes_full_buffer(self):
        from backend.modules.writing_chat.service.editor_chat import (
            build_editor_context,
        )

        buffer = "alpha\nbeta\ngamma\n"
        ctx = build_editor_context("main.tex", buffer, cursor_line=2)
        assert '<buffer_content full="true">' in ctx
        assert "1: alpha" in ctx and "3: gamma" in ctx
        assert '<cursor line="2"/>' in ctx
        assert 'source="main.tex"' in ctx
        # 小文件不出现截断说明
        assert "<context_note>" not in ctx

    def test_large_file_outline_plus_cursor_window_and_selection(self):
        from backend.modules.writing_chat.service.editor_chat import (
            CURSOR_CONTEXT_RADIUS,
            build_editor_context,
        )

        total = 1000
        lines = [f"filler line {i}" for i in range(1, total + 1)]
        lines[199] = r"\section{Intro}"
        lines[799] = r"\subsection{Method}"
        buffer = "\n".join(lines)
        ctx = build_editor_context(
            "big.tex", buffer, cursor_line=400, selection="$E=mc^2$"
        )
        assert '<buffer_content full="false"' in ctx
        # 光标前后各约 50 行窗口
        start = 400 - CURSOR_CONTEXT_RADIUS
        end = 400 + CURSOR_CONTEXT_RADIUS
        assert f'from_line="{start}" to_line="{end}"' in ctx
        assert f"{start}: filler line {start}" in ctx
        assert f"{end}: filler line {end}" in ctx
        # 窗口外的行不携带（各留一行边界外样本）
        assert f"filler line {start - 1}" not in ctx
        assert f"filler line {end + 1}" not in ctx
        # 章节结构摘要携带行号与标题
        assert r"L200: \section {Intro}" in ctx
        assert r"L800: \subsection {Method}" in ctx
        # 大文件时选区仍单独携带；附截断说明
        assert "<selection>" in ctx and "$E=mc^2$" in ctx
        assert "<context_note>" in ctx

    def test_include_full_file_overrides_large_file(self):
        from backend.modules.writing_chat.service.editor_chat import (
            build_editor_context,
        )

        buffer = "\n".join(f"line {i}" for i in range(1000))
        ctx = build_editor_context(
            "big.tex", buffer, cursor_line=500, include_full_file=True
        )
        assert '<buffer_content full="true">' in ctx
        assert "context_note" not in ctx

    def test_cursor_out_of_range_is_clamped(self):
        from backend.modules.writing_chat.service.editor_chat import (
            build_editor_context,
        )

        buffer = "\n".join(f"line {i}" for i in range(1000))
        ctx = build_editor_context("big.tex", buffer, cursor_line=99999)
        assert '<cursor line="1000"/>' in ctx


class TestExtractSuggestions:
    def test_extracts_latex_snippet_and_diff_block(self):
        from backend.modules.writing_chat.service.editor_chat import (
            extract_suggestions,
        )

        content = (
            "Try this:\n"
            "```latex\n\\textbf{Hola mundo}\n```\n"
            "And:\n"
            "```diff\n@@ -3 +3 @@\n-Hello world.\n+Hola mundo.\n```\n"
        )
        sugg = extract_suggestions(content)
        assert [s["kind"] for s in sugg] == ["snippet", "diff"]
        assert sugg[0]["language"] == "latex"
        assert sugg[0]["content"] == "\\textbf{Hola mundo}"
        assert sugg[0]["index"] == 0
        assert sugg[1]["content"].startswith("@@ -3 +3 @@")
        assert "+Hola mundo." in sugg[1]["content"]

    def test_ignores_non_suggestion_fences(self):
        from backend.modules.writing_chat.service.editor_chat import (
            extract_suggestions,
        )

        content = "```python\nprint('hi')\n```\ntext\n```\nbare fence\n```\n"
        assert extract_suggestions(content) == []

    def test_drops_unclosed_fence(self):
        from backend.modules.writing_chat.service.editor_chat import (
            extract_suggestions,
        )

        content = "```latex\n\\alpha\n"
        assert extract_suggestions(content) == []

    def test_empty_content(self):
        from backend.modules.writing_chat.service.editor_chat import (
            extract_suggestions,
        )

        assert extract_suggestions("") == []
        assert extract_suggestions(None) == []


# ── SSE 端点（Mock LLM） ────────────────────────────────────────────────────


class TestEditorSendEndpoint:
    def test_send_streams_tokens_done_with_suggestions(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        reply = (
            "改为西班牙语：\n"
            "```latex\nHola mundo.\n```\n"
            "整体替换可用：\n"
            "```diff\n@@ -3 +3 @@\n-Hello world.\n+Hola mundo.\n```\n"
        )
        chunks = [
            StreamChunk(content=reply, usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=20, completion_tokens=5, total_tokens=25),
            ),
        ]
        m_llm, _ = _mock_llm(chunks)
        with patch(_LLM_CLIENT_PATH, m_llm):
            resp = _send_request(client, env, {})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        assert types[-1] == "done"
        done = events[-1]
        # 结构化建议随 done 返回：snippet + diff 各一块
        assert [s["kind"] for s in done["suggestions"]] == ["snippet", "diff"]
        assert done["tokens"] == 25
        session_id = done["session_id"]

        # 会话按项目 scope 隔离持久化；消息级应用标记初始为 False
        ctx = ConversationContext.load_from_disk(session_id)
        assert ctx is not None
        assert len(ctx.messages) == 2
        assistant_msg = ctx.messages[-1]
        assert assistant_msg.suggestion_applied is False
        assert [s.kind for s in assistant_msg.suggestions] == ["snippet", "diff"]
        assert ctx.messages[0].referenced_file == "main.tex"

    def test_session_continues_on_second_message(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        chunks = [
            StreamChunk(content="ok", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            ),
        ]
        # 每次调用注入全新异步生成器（generator 不可复用，耗尽后二次迭代为空）
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_usage = MagicMock(
            side_effect=lambda **_: _stream(chunks)
        )
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(chunks)
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            first = _send_request(client, env, {})
            session_id = _parse_sse(first.text)[-1]["session_id"]
            second = _send_request(client, env, {"session_id": session_id})
        assert _parse_sse(second.text)[-1]["session_id"] == session_id
        # 第二次调用复用同一会话：流式方法（工具路径或纯流式）共消费两次
        total_calls = (
            m_client.chat_stream_with_usage.call_count
            + m_client.chat_stream_with_tools.call_count
        )
        assert total_calls == 2

    def test_no_api_key_yields_error_event(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "")
        resp = _send_request(client, env, {})
        events = _parse_sse(resp.text)
        assert events[0]["type"] == "error"

    def test_tool_stream_failure_no_silent_retry(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """工具分支流失败：单次 error 事件后立即终止，严禁静默重发 LLM。

        回归（阶段四评审）：曾出现流失败落入"轮次用尽"兜底分支——对已失败
        的流静默重试一次；重试成功的内容又因 llm_failed 被整体丢弃并删除
        user 消息，留下无提问的孤儿工具消息污染持久化会话。
        """
        import json as _json

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=RuntimeError("boom from provider")
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            resp = _send_request(client, env, {})

        events = _parse_sse(resp.text)
        # 仅一条 error，且没有 done；未发生任何静默重试
        assert [e["type"] for e in events] == ["error"]
        assert "boom from provider" in events[0]["message"]
        assert m_client.chat_stream_with_tools.call_count == 1
        assert m_client.chat_stream_with_usage.call_count == 0

        # user 消息已从持久化会话移除（scope 子目录下唯一会话文件无任何消息）
        session_files = list(temp_session_dir.rglob("*.json"))
        assert len(session_files) == 1
        data = _json.loads(session_files[0].read_text(encoding="utf-8"))
        assert data["messages"] == []

    def test_oversized_user_message_yields_content_too_long(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """R3：user_message 超限 → 结构化 content_too_long（非裸 422）。"""
        from backend.modules.writing_chat.models import EDITOR_USER_MESSAGE_MAX_CHARS

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        oversized = "x" * (EDITOR_USER_MESSAGE_MAX_CHARS + 1)
        resp = _send_request(client, env, {"user_message": oversized})
        assert resp.status_code == 200  # SSE 已建立，错误经事件通道下发
        events = _parse_sse(resp.text)
        assert len(events) == 1
        payload = events[0]
        assert payload["type"] == "error"
        message = payload["message"]
        assert isinstance(message, dict)
        assert message["error"] == "content_too_long"
        # 不产生任何会话落盘副作用
        assert list(temp_session_dir.rglob("*.json")) == []

    def test_oversized_buffer_yields_content_too_long(
        self, env, temp_session_dir, monkeypatch
    ):
        from fastapi import HTTPException

        from backend.modules.writing_chat.models import (
            EDITOR_SELECTION_MAX_CHARS,
            EditorSendRequest,
        )
        from backend.modules.writing_chat.service import get_writing_chat_service

        req = EditorSendRequest(
            file_path="main.tex",
            buffer="x",
            selection="y" * (EDITOR_SELECTION_MAX_CHARS + 1),
            user_message="hi",
        )
        with pytest.raises(HTTPException) as exc_info:
            # editor_send 是 async generator：手动驱动首个 yield 触发入口校验
            async def _drive():
                async for _ in get_writing_chat_service().editor_send(req):
                    pass

            asyncio.run(_drive())
        assert exc_info.value.status_code == 413
        assert exc_info.value.detail["error"] == "content_too_long"


# ── 开关把关（决策 Q26） ────────────────────────────────────────────────────


class TestAssistantGate:
    def test_assistant_disabled_rejects_endpoints(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().features.editor, "enabled", True)
        monkeypatch.setattr(get_config().features.editor, "assistant_enabled", False)

        resp = _send_request(client, env, {})
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "assistant_disabled"

        resp2 = client.post(
            "/api/writing-chat/editor/apply-suggestion",
            json={"session_id": "whatever:x", "message_id": "m"},
        )
        assert resp2.status_code == 403
        assert resp2.json()["detail"]["error"] == "assistant_disabled"

    def test_editor_disabled_rejects_assistant_endpoint(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().features.editor, "enabled", False)
        monkeypatch.setattr(get_config().features.editor, "assistant_enabled", True)
        resp = _send_request(client, env, {})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "editor_disabled"


# ── apply-suggestion 标记记录 ───────────────────────────────────────────────


class TestApplySuggestion:
    def _make_session_with_suggestion(self) -> tuple[str, str]:
        ctx = ConversationContext.create_session(file_path="main.tex", scope="tests")
        ctx.append_user_message("改写")
        assistant = ctx.append_assistant_message(
            content="```latex\nHola\n```",
            suggestions=[
                {"kind": "snippet", "language": "latex", "content": "Hola", "index": 0}
            ],
        )
        ctx.save_to_disk()
        return ctx.session_id, assistant.id

    def _apply(self, client, session_id, message_id, **extra):
        return client.post(
            "/api/writing-chat/editor/apply-suggestion",
            json={"session_id": session_id, "message_id": message_id, **extra},
        )

    def test_marks_message_applied_and_persists(self, client, temp_session_dir):
        session_id, message_id = self._make_session_with_suggestion()
        resp = self._apply(client, session_id, message_id)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["suggestion_applied"] is True
        # 消息级旧行为不产生 index 记录
        assert data["applied_suggestions"] == []

        loaded = ConversationContext.load_from_disk(session_id)
        assert loaded.messages[-1].suggestion_applied is True

    def test_unknown_message_returns_404(self, client, temp_session_dir):
        session_id, _ = self._make_session_with_suggestion()
        resp = self._apply(client, session_id, "no-such")
        assert resp.status_code == 404

    def test_unknown_session_returns_404(self, client, temp_session_dir):
        resp = client.post(
            "/api/writing-chat/editor/apply-suggestion",
            json={"session_id": "deadbeef:000000", "message_id": "m"},
        )
        assert resp.status_code == 404


class TestApplySuggestionByIndex:
    """R6：index 级建议应用标记——部分应用不算整体已应用，全覆盖才置位。"""

    def _make_session_with_two_suggestions(self) -> tuple[str, str]:
        ctx = ConversationContext.create_session(file_path="main.tex", scope="tests")
        ctx.append_user_message("改写")
        assistant = ctx.append_assistant_message(
            content="```latex\nHola\n```\n```diff\n-x\n+y\n```",
            suggestions=[
                {"kind": "snippet", "language": "latex", "content": "Hola", "index": 0},
                {"kind": "diff", "language": "diff", "content": "-x\n+y", "index": 1},
            ],
        )
        ctx.save_to_disk()
        return ctx.session_id, assistant.id

    def _apply(self, client, body: dict):
        return client.post("/api/writing-chat/editor/apply-suggestion", json=body)

    def test_partial_index_application_does_not_mark_message(
        self, client, temp_session_dir
    ):
        session_id, message_id = self._make_session_with_two_suggestions()
        resp = self._apply(
            client,
            {"session_id": session_id, "message_id": message_id, "suggestion_index": 0},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # 仅覆盖 index 0/2：消息级标记不得置位
        assert data["suggestion_applied"] is False
        assert data["applied_suggestions"] == [0]

        loaded = ConversationContext.load_from_disk(session_id)
        msg = loaded.messages[-1]
        assert msg.applied_suggestions == [0]
        assert msg.suggestion_applied is False

    def test_full_coverage_marks_message_applied(self, client, temp_session_dir):
        session_id, message_id = self._make_session_with_two_suggestions()
        first = self._apply(
            client,
            {"session_id": session_id, "message_id": message_id, "suggestion_index": 1},
        )
        assert first.json()["applied_suggestions"] == [1]
        assert first.json()["suggestion_applied"] is False

        second = self._apply(
            client,
            {"session_id": session_id, "message_id": message_id, "suggestion_index": 0},
        )
        assert second.status_code == 200, second.text
        data = second.json()
        # applied_suggestions 覆盖全部建议 index → 整体置位
        assert sorted(data["applied_suggestions"]) == [0, 1]
        assert data["suggestion_applied"] is True

        loaded = ConversationContext.load_from_disk(session_id)
        msg = loaded.messages[-1]
        assert msg.suggestion_applied is True
        assert sorted(msg.applied_suggestions) == [0, 1]

    def test_duplicate_index_is_deduplicated(self, client, temp_session_dir):
        session_id, message_id = self._make_session_with_two_suggestions()
        for _ in range(2):
            resp = self._apply(
                client,
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "suggestion_index": 1,
                },
            )
        assert resp.json()["applied_suggestions"] == [1]

    def test_index_without_matching_suggestions_still_records(
        self, client, temp_session_dir
    ):
        """越界 index 不崩溃：记录照常持久化，但不会覆盖全部建议。"""
        session_id, message_id = self._make_session_with_two_suggestions()
        resp = self._apply(
            client,
            {"session_id": session_id, "message_id": message_id, "suggestion_index": 9},
        )
        assert resp.status_code == 200
        assert resp.json()["suggestion_applied"] is False
        assert resp.json()["applied_suggestions"] == [9]

    def test_legacy_no_index_marks_immediately(self, client, temp_session_dir):
        """不带 suggestion_index 的旧客户端请求保持旧契约（直接整体置位）。"""
        session_id, message_id = self._make_session_with_two_suggestions()
        resp = self._apply(client, {"session_id": session_id, "message_id": message_id})
        assert resp.json()["suggestion_applied"] is True
        assert resp.json()["applied_suggestions"] == []

    def test_old_session_data_without_field_loads_with_default(self):
        """旧会话 JSON 缺 applied_suggestions 字段 → 默认空列表（向后兼容）。"""
        legacy = ChatMessage.model_validate({"id": "m1", "role": "assistant"})
        assert legacy.applied_suggestions == []
        assert legacy.suggestion_applied is False


# ── SSE 兜底异常脱敏（R5） ──────────────────────────────────────────────────


def _raising_async_gen(exc: Exception):
    async def gen():
        raise exc
        yield  # pragma: no cover

    return gen()


class TestSSEInternalErrorFraming:
    """通用异常 → internal_error 结构化错误；str(e) 严禁泄漏给客户端。"""

    def test_editor_send_unexpected_error_masked(
        self, client, env, temp_session_dir, monkeypatch
    ):
        monkeypatch.setattr(get_config().features.editor, "enabled", True)
        monkeypatch.setattr(get_config().features.editor, "assistant_enabled", True)
        m_service = MagicMock()
        m_service.editor_send = MagicMock(
            return_value=_raising_async_gen(RuntimeError("secret /home/x/path"))
        )
        monkeypatch.setattr(
            "backend.modules.writing_chat.router.get_writing_chat_service",
            lambda: m_service,
        )

        resp = _send_request(client, env, {})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert len(events) == 1
        payload = events[0]
        assert payload["type"] == "error"
        assert payload["message"]["error"] == "internal_error"
        assert payload["message"]["message"] == "An unexpected server error occurred"
        # 原始异常文本（含路径）不得出现在任何下发字节中
        assert "secret" not in resp.text
        assert "/home/x/path" not in resp.text

    def test_send_endpoint_unexpected_error_masked(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """/send 与 /editor/send 共用同一兜底语义。"""
        m_service = MagicMock()
        m_service.send = MagicMock(
            return_value=_raising_async_gen(RuntimeError("secret /home/x/path"))
        )
        monkeypatch.setattr(
            "backend.modules.writing_chat.router.get_writing_chat_service",
            lambda: m_service,
        )

        resp = client.post(
            "/api/writing-chat/send",
            params={"workspace_id": env["ws"]},
            json={"file_path": "main.tex", "user_message": "hi"},
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert events[-1]["type"] == "error"
        assert events[-1]["message"]["error"] == "internal_error"
        assert "secret" not in resp.text
        assert "/home/x/path" not in resp.text

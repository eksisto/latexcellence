"""编辑器侧 AI 助手测试（editor-design §5.2 / §7，阶段四；阶段五a 扩）。

覆盖：
    - build_editor_context 自适应上下文注入（小文件全文 / 大文件摘要+
      光标邻域+选区 / 手动"纳入全文"开关）——service 层纯函数直测
    - extract_suggestions 建议提取（latex 片段、diff 块、非建议围栏忽略、
      未闭合围栏丢弃）
    - POST /api/writing-chat/editor/send SSE 端点（**Mock LLM，严禁真实网络**）：
      token 流、done 携带结构化建议、会话持久化与消息级标记初始态
    - editor.assistant_enabled=false 时服务端拒绝助手端点（决策 Q26）
    - POST /api/writing-chat/editor/apply-suggestion 消息级应用标记记录
    - 阶段五a：工具白名单执行前硬校验（幻觉/越权工具名被拒；联网/学术
      工具在源开关关闭时执行期同口径复核拒绝）、
      _get_editor_tools 三源汇聚（联网/学术/项目）与按源独立容错、
      网络访问灰度开关双重把关（开关关闭时工具不可达）、
      DDGChallengeError 显式分支（captcha_challenge + done(captcha_pending)
      终流，本轮工具消息不持久化）
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── 纯函数：上下文注入构建逻辑（editor-design §7.3 / 决策 Q24） ───────────────


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


class TestToolFailureText:
    """工具失败信息构造口的脱敏语义（审查遗留项 C2）。"""

    _MOD = "backend.modules.writing_chat.service.editor_chat"
    _ROOT = "/srv/latexcellence/data/users/u1/projects/p7q8"

    def _patch(self, monkeypatch, *, enabled: bool, root_path=None) -> None:
        from types import SimpleNamespace

        cfg = SimpleNamespace(multi_user=SimpleNamespace(enabled=enabled))
        monkeypatch.setattr(f"{self._MOD}.get_config", lambda: cfg)
        if root_path is None:
            monkeypatch.setattr(
                f"{self._MOD}.get_project_manager_for_workspace",
                lambda ws: (_ for _ in ()).throw(RuntimeError("workspace gone")),
            )
        else:
            monkeypatch.setattr(
                f"{self._MOD}.get_project_manager_for_workspace",
                lambda ws: SimpleNamespace(root_path=root_path),
            )

    def test_multi_user_rewrites_project_prefix(self, monkeypatch):
        from backend.modules.writing_chat.service.editor_chat import tool_failure_text

        self._patch(monkeypatch, enabled=True, root_path=self._ROOT)
        exc = FileNotFoundError(
            f"[Errno 2] {self._ROOT}/sections/intro.tex: no such file"
        )
        out = tool_failure_text("read_file_content", exc, "ws-1")
        assert "/srv/latexcellence" not in out
        assert "./sections/intro.tex" in out
        # 保留异常类型与详情形态，模型仍可自纠
        assert "FileNotFoundError" in out

    def test_multi_user_unresolvable_root_degrades_to_stable_text(self, monkeypatch):
        from backend.modules.writing_chat.service.editor_chat import tool_failure_text

        self._patch(monkeypatch, enabled=True, root_path=None)
        out = tool_failure_text(
            "list_files", RuntimeError("boom /home/linus/secret"), "ws-1"
        )
        assert out == "Tool execution failed."
        assert "/home/linus" not in out

    def test_multi_user_empty_workspace_degrades_to_stable_text(self, monkeypatch):
        from backend.modules.writing_chat.service.editor_chat import tool_failure_text

        self._patch(monkeypatch, enabled=True, root_path=self._ROOT)
        out = tool_failure_text("search_across_files", OSError("disk full /etc"), "")
        assert out == "Tool execution failed."

    def test_single_user_keeps_raw_detail(self, monkeypatch):
        from backend.modules.writing_chat.service.editor_chat import tool_failure_text

        self._patch(monkeypatch, enabled=False)
        exc = FileNotFoundError("main.tex: missing")
        out = tool_failure_text("read_file_content", exc, "")
        # 单用户本地模式零行为变化：保留原始格式
        assert out == f"Tool execution failed: {exc}"


# ── 阶段五a：工具白名单 / 多源汇聚 / DDG 挑战 ─────────────────────────────

_EDITOR_CHAT_MOD = "backend.modules.writing_chat.service.editor_chat"

_WEB_TOOL_NAMES = {"web_search", "open_page", "find_in_page"}
_SCHOLARLY_TOOL_NAMES = {
    "search_scholarly_works",
    "get_work_details",
    "find_related_works",
    "get_full_text_or_sections",
    "search_research_data_resources",
}
_PROJECT_TOOL_NAMES = {"list_files", "read_file_content", "search_across_files"}


def _tool_schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def _tool_source(names) -> MagicMock:
    m = MagicMock()
    m.get_tools_schema = MagicMock(return_value=[_tool_schema(n) for n in names])
    return m


def _enable_network_switches(monkeypatch, *, web: bool, scholarly: bool) -> None:
    """精确挂点在 schema 收集层（editor_chat._get_editor_tools）的开关矩阵。"""
    fc = get_config().features.function_calling
    monkeypatch.setattr(fc.web_search, "enabled", web)
    monkeypatch.setattr(get_config().features.writing_chat, "web_search_enabled", web)
    monkeypatch.setattr(fc.scholarly_research, "enabled", scholarly)
    monkeypatch.setattr(fc.scholarly_research.providers.openalex, "enabled", scholarly)


def _make_function_tool_call(
    call_id: str = "call_1", name: str = "web_search", args: dict | None = None
):
    """构造 openai tool_call 对象（镜像 test_writing_chat 的先例）。"""
    import json

    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall,
        Function,
    )

    return ChatCompletionMessageToolCall(
        id=call_id,
        function=Function(name=name, arguments=json.dumps(args or {"query": "test"})),
        type="function",
    )


def _drive_editor_send(service, req, workspace_id, *, resolve=None, modified_args=None):
    """直接驱动 service.editor_send 异步生成器（单用户 Event 确认路径）。

    确认流在流内等待（SSE 生成器挂起于 _await_tool_confirmation），而
    asyncio.Event 非线程安全、不能从 TestClient 的其他线程置位，故在
    同一事件循环内以伴随任务自动决议：

        - resolve=None：不决议，等待 TTL（配合 TTL monkeypatch 测过期）；
        - resolve="confirm"：pending 登记后立即确认（亚秒级，验证
          "先登记后 yield"——确认先于 tool_pending 事件抵达消费方也
          必须成功）；
        - resolve="cancel"：pending 登记后立即取消。

    返回 (events, ordering)：ordering 依序记录 ("register", pid) 与
    ("yield", event_type)，供登记先于事件下发的时序断言。
    """
    import asyncio

    async def scenario():
        ordering: list[tuple] = []
        events: list[dict] = []
        pending_ids: list[str] = []
        registered = asyncio.Event()
        real_register = service._register_tool_confirmation

        async def spy_register(pid, tool_name, tool_args):
            ordering.append(("register", pid))
            result = await real_register(pid, tool_name, tool_args)
            pending_ids.append(pid)
            registered.set()
            return result

        service._register_tool_confirmation = spy_register
        resolver_task = None
        if resolve is not None:

            async def resolver():
                await registered.wait()
                pid = pending_ids[-1]
                if resolve == "confirm":
                    assert service.confirm_pending_tool(pid, modified_args) is True
                else:
                    assert service.cancel_pending_tool(pid) is True

            resolver_task = asyncio.ensure_future(resolver())
        try:
            async for event in service.editor_send(req, workspace_id):
                ordering.append(("yield", event.get("type")))
                events.append(event)
        finally:
            service._register_tool_confirmation = real_register
            if resolver_task is not None:
                await resolver_task
        return events, ordering

    return asyncio.run(scenario())


def _mock_registry(result_text: str = "TOOL RESULT", *, side_effect=None):
    """构造 registry Mock：execute 为 AsyncMock（editor_chat 内 await），
    结果对象带 format_for_llm；side_effect 用于异常路径（DDG 挑战等）。"""
    m_registry = MagicMock()
    m_registry.execute = AsyncMock(
        return_value=MagicMock(format_for_llm=MagicMock(return_value=result_text)),
        side_effect=side_effect,
    )
    return m_registry


class TestEditorToolSources:
    """_get_editor_tools 三源汇聚 + 按源独立容错 + 开关前置（阶段五a）。"""

    def _service(self):
        from backend.modules.writing_chat.service import get_writing_chat_service

        return get_writing_chat_service()

    def _patch_sources(
        self,
        monkeypatch,
        *,
        web=None,
        scholarly=None,
        project=None,
        project_open=True,
    ):
        """web/scholarly/project 为 None 表示该源抛异常；否则为工具名集合。"""
        if web is None:

            def _boom():
                raise RuntimeError("search manager unavailable")

            monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_search_manager", _boom)
        elif web is not False:
            monkeypatch.setattr(
                f"{_EDITOR_CHAT_MOD}.get_search_manager", lambda: _tool_source(web)
            )

        if scholarly is None:

            def _boom():
                raise RuntimeError("research http client not initialized")

            monkeypatch.setattr(
                "backend.utils.scholarly_research.get_research_manager", _boom
            )
        elif scholarly is not False:
            monkeypatch.setattr(
                "backend.utils.scholarly_research.get_research_manager",
                lambda: _tool_source(scholarly),
            )

        if project is not False:
            from types import SimpleNamespace

            monkeypatch.setattr(
                f"{_EDITOR_CHAT_MOD}.get_project_manager_for_workspace",
                lambda ws: SimpleNamespace(is_open=project_open),
            )
            monkeypatch.setattr(
                f"{_EDITOR_CHAT_MOD}.get_project_tools_manager",
                lambda: _tool_source(project or _PROJECT_TOOL_NAMES),
            )

    def test_all_sources_converged_with_switches_on(self, monkeypatch):
        self._enable_all(monkeypatch)
        self._patch_sources(
            monkeypatch,
            web=_WEB_TOOL_NAMES,
            scholarly=_SCHOLARLY_TOOL_NAMES,
            project=_PROJECT_TOOL_NAMES,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _WEB_TOOL_NAMES | _SCHOLARLY_TOOL_NAMES | _PROJECT_TOOL_NAMES

    def _enable_all(self, monkeypatch):
        _enable_network_switches(monkeypatch, web=True, scholarly=True)
        monkeypatch.setattr(
            get_config().features.function_calling.project_tools, "enabled", True
        )

    def test_web_tools_absent_when_writing_chat_gate_closed(self, monkeypatch):
        """全局 web_search.enabled 开而 Writing Chat 灰度总闸关 → 联网源不注入。"""
        self._enable_all(monkeypatch)
        monkeypatch.setattr(
            get_config().features.writing_chat, "web_search_enabled", False
        )
        self._patch_sources(
            monkeypatch,
            web=_WEB_TOOL_NAMES,
            scholarly=_SCHOLARLY_TOOL_NAMES,
            project=_PROJECT_TOOL_NAMES,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _SCHOLARLY_TOOL_NAMES | _PROJECT_TOOL_NAMES

    def test_scholarly_tools_absent_when_openalex_disabled(self, monkeypatch):
        self._enable_all(monkeypatch)
        monkeypatch.setattr(
            get_config().features.function_calling.scholarly_research.providers.openalex,
            "enabled",
            False,
        )
        self._patch_sources(
            monkeypatch,
            web=_WEB_TOOL_NAMES,
            scholarly=_SCHOLARLY_TOOL_NAMES,
            project=_PROJECT_TOOL_NAMES,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _WEB_TOOL_NAMES | _PROJECT_TOOL_NAMES

    def test_all_switches_closed_tools_unreachable(self, monkeypatch):
        """三源开关全关 → 返回空列表 → 调用方走纯流式分支（工具不可达）。"""
        _enable_network_switches(monkeypatch, web=False, scholarly=False)
        monkeypatch.setattr(
            get_config().features.function_calling.project_tools, "enabled", False
        )
        # 源实现仍会被 imports，但开关关闭即短路；兜底 patch 避免 import 副作用
        self._patch_sources(monkeypatch, project=False)
        assert self._service()._get_editor_tools("") == []

    def test_source_failure_degrades_independently(self, monkeypatch):
        """单一源故障（含学术源 httpx 客户端未初始化）不连坐其余源。"""
        self._enable_all(monkeypatch)
        self._patch_sources(
            monkeypatch,
            web=None,  # 搜索源抛异常
            scholarly=_SCHOLARLY_TOOL_NAMES,
            project=_PROJECT_TOOL_NAMES,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _SCHOLARLY_TOOL_NAMES | _PROJECT_TOOL_NAMES

    def test_scholarly_failure_degrades_independently(self, monkeypatch):
        self._enable_all(monkeypatch)
        self._patch_sources(
            monkeypatch,
            web=_WEB_TOOL_NAMES,
            scholarly=None,  # get_research_manager 抛 RuntimeError
            project=_PROJECT_TOOL_NAMES,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _WEB_TOOL_NAMES | _PROJECT_TOOL_NAMES

    def test_workspace_gate_only_constrains_project_source(self, monkeypatch):
        """联网/学术源不依赖 workspace；项目源仅在项目打开时注入。"""
        self._enable_all(monkeypatch)
        self._patch_sources(
            monkeypatch,
            web=_WEB_TOOL_NAMES,
            scholarly=_SCHOLARLY_TOOL_NAMES,
            project=_PROJECT_TOOL_NAMES,
            project_open=False,
        )
        names = {s["function"]["name"] for s in self._service()._get_editor_tools("ws")}
        assert names == _WEB_TOOL_NAMES | _SCHOLARLY_TOOL_NAMES


class TestEditorToolWhitelist:
    """执行前白名单硬校验（阶段五a 既有缺陷必修，Mock LLM 无网络）。"""

    def test_hallucinated_write_tool_rejected_before_execution(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """幻觉调用 edit_paragraph 不得执行：不触达 registry、不推 tool_status，
        注入模型可自纠的失败 tool result 后流程继续到最终回复。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = MagicMock()
        m_registry.execute = MagicMock()
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(
                            call_id="call_evil", name="edit_paragraph"
                        ),
                    ],
                )
            ],
            [
                StreamChunk(content="cannot edit directly", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=10, completion_tokens=3, total_tokens=13
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        m_client.chat_stream_with_usage = MagicMock()
        with patch(_LLM_CLIENT_PATH, m_llm):
            resp = _send_request(client, env, {})

        m_registry.execute.assert_not_called()
        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        assert types == ["round_start", "token", "done"]
        assert not any(e.get("tool") == "edit_paragraph" for e in events)

        # 会话持久化 tool_calls/tool 配对完整（被拒调用也有 tool result），
        # 流程继续到最终 assistant 回复
        session_id = events[-1]["session_id"]
        ctx = ConversationContext.load_from_disk(session_id)
        roles = [m.role for m in ctx.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert ctx.messages[-1].content == "cannot edit directly"
        tool_msg = ctx.messages[2]
        assert "not available to the editor assistant" in tool_msg.content

    def test_whitelisted_web_tool_executes(self, env, temp_session_dir, monkeypatch):
        """白名单内工具经确认后照常执行：registry 触达、pending/tool_status/
        tool_result 依序成对（阶段五b 起联网工具先经确认流）。"""
        from backend.modules.writing_chat.models import EditorSendRequest
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        # registry.execute 须为 AsyncMock（editor_chat 内 await）：同步
        # MagicMock 不可 await，会让本用例静默走失败注入路径、既断言不到
        # "照常执行"原意图，也测不到结果入会话
        m_registry = _mock_registry(result_text="SEARCH RESULTS")
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(call_id="call_ws", name="web_search"),
                    ],
                )
            ],
            [
                StreamChunk(content="answer based on search", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=10, completion_tokens=3, total_tokens=13
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        req = EditorSendRequest(
            file_path="main.tex", buffer=MAIN_TEX, user_message="查证"
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                req,
                env["ws"],
                resolve="confirm",
            )

        assert m_registry.execute.call_count == 1
        assert m_registry.execute.call_args.args[0] == "web_search"

        types = [e["type"] for e in events]
        assert types == [
            "tool_pending",
            "tool_status",
            "tool_result",
            "round_start",
            "token",
            "done",
        ]
        assert events[0]["tool"] == "web_search"
        assert events[-1]["type"] == "done"

        # 成功执行路径的结果真正注入会话（非失败兜底文本）
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        roles = [m.role for m in ctx.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert ctx.messages[2].content == "SEARCH RESULTS"

    def test_network_tools_blocked_when_switches_closed(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """源开关全关时白名单内联网/学术工具同样拒绝执行（审查加固回归）：
        ToolRegistry.execute 只查运行时 enabled 标志，若执行前不复核源
        开关，schema 关闭后模型幻觉调用仍会直达执行器产生真实网络请求。
        """
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        _enable_network_switches(monkeypatch, web=False, scholarly=False)
        m_registry = MagicMock()
        m_registry.execute = MagicMock()
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(call_id="call_ws", name="web_search"),
                        _make_function_tool_call(
                            call_id="call_sr", name="search_scholarly_works"
                        ),
                    ],
                )
            ],
            [
                StreamChunk(content="answered without network", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=10, completion_tokens=3, total_tokens=13
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            resp = _send_request(client, env, {})

        # 两个幻觉调用均未触达 registry（无任何真实网络/检索出口）
        m_registry.execute.assert_not_called()
        events = _parse_sse(resp.text)
        assert not any(
            e.get("tool") in ("web_search", "search_scholarly_works") for e in events
        )
        assert [e["type"] for e in events] == ["round_start", "token", "done"]

        # 会话持久化 tool_calls/tool 配对完整：两个被拒调用各有一条失败
        # tool result（tool_calls 不悬空），流程继续到最终 assistant 回复
        session_id = events[-1]["session_id"]
        ctx = ConversationContext.load_from_disk(session_id)
        tool_msgs = [m for m in ctx.messages if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert all(
            "not available to the editor assistant" in m.content for m in tool_msgs
        )
        assert ctx.messages[-1].content == "answered without network"

    def test_web_tool_blocked_when_writing_chat_gate_closed_alone(
        self, client, env, temp_session_dir, monkeypatch
    ):
        """全局 web_search.enabled 开而 Writing Chat 灰度总闸关：执行期
        复核与 schema 收集层同口径（AND），幻觉调用不被放行。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        _enable_network_switches(monkeypatch, web=False, scholarly=True)
        m_registry = MagicMock()
        m_registry.execute = MagicMock()
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(call_id="call_ws", name="open_page"),
                    ],
                )
            ],
            [
                StreamChunk(content="no page access", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=10, completion_tokens=3, total_tokens=13
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            resp = _send_request(client, env, {})

        m_registry.execute.assert_not_called()
        events = _parse_sse(resp.text)
        assert [e["type"] for e in events] == ["round_start", "token", "done"]


class TestEditorDDGChallenge:
    """DDGChallengeError 显式分支：captcha 终流事件（阶段五a，Mock LLM）。"""

    def test_challenge_terminates_stream_with_captcha_pending(
        self, env, temp_session_dir, monkeypatch
    ):
        from backend.utils.web_search import DDGChallengeError

        from backend.modules.writing_chat.models import EditorSendRequest
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        challenge = DDGChallengeError(
            challenge_id="cid123",
            images=[{"hash": "h1", "base64": "Zm9v"}],
            query="latex tutorial",
            submission_url="https://duckduckgo.com/challenge",
            form_data={"k": "v"},
            engine_name="DuckDuckGo",
            instruction="Pick the ducks",
        )
        m_registry = MagicMock()
        m_registry.execute = MagicMock(side_effect=challenge)
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(call_id="call_ddg", name="web_search"),
                    ],
                )
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        m_client.chat_stream_with_usage = MagicMock()
        req = EditorSendRequest(
            file_path="main.tex", buffer=MAIN_TEX, user_message="查证"
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                req,
                env["ws"],
                resolve="confirm",
            )

        types = [e["type"] for e in events]
        assert types == ["tool_pending", "tool_status", "captcha_challenge", "done"]

        captcha = events[2]
        assert captcha["challenge_id"] == "cid123"
        assert captcha["images"] == [{"hash": "h1", "base64": "Zm9v"}]
        assert captcha["query"] == "latex tutorial"
        assert captcha["engine_name"] == "DuckDuckGo"
        assert captcha["instruction"] == "Pick the ducks"

        done = events[-1]
        assert done["captcha_pending"] is True
        assert done["message_id"] == ""
        assert done["session_id"]

        # 本轮工具消息不持久化：会话仅剩此前已落盘的 user 消息（无 assistant/
        # tool 残留，与前端"删占位"语义对齐）
        ctx = ConversationContext.load_from_disk(done["session_id"])
        assert len(ctx.messages) == 1
        assert ctx.messages[0].role == "user"

    def test_general_tool_failure_still_yields_tool_result(
        self, env, temp_session_dir, monkeypatch
    ):
        """通用失败路径不携带 captcha 语义（DDG 分支仅在真实挑战时出现）。"""
        from backend.modules.writing_chat.models import EditorSendRequest
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = MagicMock()
        m_registry.execute = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(
                            call_id="call_fail", name="web_search"
                        ),
                    ],
                )
            ],
            [
                StreamChunk(content="recovered", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=5, completion_tokens=1, total_tokens=6
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        req = EditorSendRequest(
            file_path="main.tex", buffer=MAIN_TEX, user_message="查证"
        )
        with patch(_LLM_CLIENT_PATH, m_llm):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                req,
                env["ws"],
                resolve="confirm",
            )

        types = [e["type"] for e in events]
        assert "captcha_challenge" not in types
        assert types[-1] == "done"
        assert events[-1].get("captcha_pending") is None


# ── 阶段五b：联网/学术工具确认流 ──────────────────────────────────────────


class TestEditorToolConfirmation:
    """编辑器侧联网/学术工具逐次确认（阶段五b，editor-experience-design §4.5/§5）。

    确认流为流内等待：service.editor_send 生成器挂起于 pending 等待处，
    confirm/cancel 端点决议后续流。asyncio.Event 非线程安全，决议在
    同一事件循环内经伴随任务注入（_drive_editor_send），全程 Mock LLM
    与 registry，无真实网络。
    """

    def _make_two_round_llm(self, tool_name: str, args: dict | None = None):
        rounds = [
            [
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[
                        _make_function_tool_call(
                            call_id="call_conf", name=tool_name, args=args
                        ),
                    ],
                )
            ],
            [
                StreamChunk(content="answered with available info", usage=None),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=8, completion_tokens=2, total_tokens=10
                    ),
                ),
            ],
        ]
        m_llm, m_client = _mock_llm([])
        m_client.chat_stream_with_tools = MagicMock(
            side_effect=lambda **_: _stream(rounds.pop(0))
        )
        return m_llm

    def _req(self):
        from backend.modules.writing_chat.models import EditorSendRequest

        return EditorSendRequest(
            file_path="main.tex", buffer=MAIN_TEX, user_message="查证一下"
        )

    def test_confirm_executes_tool_and_injects_result(
        self, env, temp_session_dir, monkeypatch
    ):
        """确认后工具真正执行：结果作为 tool result 注入并驱动后续模型轮次。"""
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = _mock_registry(result_text="PAPERS")
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        with patch(
            _LLM_CLIENT_PATH,
            self._make_two_round_llm(
                "search_scholarly_works", {"query": "latex citations"}
            ),
        ):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                self._req(),
                env["ws"],
                resolve="confirm",
                modified_args={"query": "modified query"},
            )

        # 确认后执行且使用（可能被前端修改后的）参数
        assert m_registry.execute.call_count == 1
        assert m_registry.execute.call_args.args[0] == "search_scholarly_works"
        assert m_registry.execute.call_args.args[1]["query"] == "modified query"

        types = [e["type"] for e in events]
        assert types == [
            "tool_pending",
            "tool_status",
            "tool_result",
            "round_start",
            "token",
            "done",
        ]
        pending = events[0]
        assert pending["pending_id"]
        assert pending["tool"] == "search_scholarly_works"
        assert pending["label"] == "searching_scholarly"
        assert pending["args"] == {"query": "latex citations"}

        # 结果注入会话：assistant(tool_calls) + tool(执行结果) + 最终回复
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        roles = [m.role for m in ctx.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert ctx.messages[2].content == "PAPERS"

    def test_register_before_yield_subsecond_confirm_no_404(
        self, env, temp_session_dir, monkeypatch
    ):
        """先登记后 yield：登记先于 tool_pending 事件下发，亚秒级确认
        不落"查无此 pending"404（Writing Chat 既有次序缺陷的回归断言）。"""
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = _mock_registry(result_text="OK")
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        with patch(_LLM_CLIENT_PATH, self._make_two_round_llm("web_search")):
            events, ordering = _drive_editor_send(
                get_writing_chat_service(),
                self._req(),
                env["ws"],
                resolve="confirm",
            )

        # 登记必然先于 tool_pending 事件下发（同协程顺序执行，可精确断言）
        first_register = ordering.index(next(t for t in ordering if t[0] == "register"))
        first_pending_yield = ordering.index(("yield", "tool_pending"))
        assert first_register < first_pending_yield

        # 决议在登记后立即发生（早于 tool_pending 抵达消费方），仍成功执行
        types = [e["type"] for e in events]
        assert "tool_cancelled" not in types
        assert types[-1] == "done"
        assert m_registry.execute.call_count == 1

    def test_cancel_injects_cancel_semantics_and_model_continues(
        self, env, temp_session_dir, monkeypatch
    ):
        """拒绝后模型收到取消语义：不执行、注入取消 tool result，模型
        基于已有信息继续回答。"""
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = MagicMock()
        m_registry.execute = MagicMock()
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        with patch(
            _LLM_CLIENT_PATH,
            self._make_two_round_llm("web_search", {"query": "who said"}),
        ):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                self._req(),
                env["ws"],
                resolve="cancel",
            )

        m_registry.execute.assert_not_called()

        types = [e["type"] for e in events]
        assert types == [
            "tool_pending",
            "tool_cancelled",
            "round_start",
            "token",
            "done",
        ]
        cancelled = events[1]
        assert cancelled["tool"] == "web_search"
        assert cancelled["pending_id"] == events[0]["pending_id"]

        # 取消语义 tool result 持久化：tool_call 配对完整、模型不悬空，
        # 并明确知晓用户拒绝了本次检索
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        roles = [m.role for m in ctx.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        cancel_msg = ctx.messages[2]
        assert "user declined" in cancel_msg.content
        assert "web_search" in cancel_msg.content
        assert "Do not retry" in cancel_msg.content
        assert ctx.messages[-1].content == "answered with available info"

    def test_ttl_expiry_equals_cancel(self, env, temp_session_dir, monkeypatch):
        """TTL 过期等同取消：等待侧超时 → tool_cancelled + 取消语义注入。"""
        from backend.modules.writing_chat.service import get_writing_chat_service
        from backend.modules.writing_chat.service.state import StateMixin

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        monkeypatch.setattr(StateMixin, "_TOOL_CONFIRM_TTL_SECONDS", 0.05)
        m_registry = MagicMock()
        m_registry.execute = MagicMock()
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        with patch(
            _LLM_CLIENT_PATH,
            self._make_two_round_llm("open_page", {"url": "https://example.com"}),
        ):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                self._req(),
                env["ws"],
                resolve=None,
            )

        m_registry.execute.assert_not_called()
        types = [e["type"] for e in events]
        assert types == [
            "tool_pending",
            "tool_cancelled",
            "round_start",
            "token",
            "done",
        ]

        # 过期后 pending 条目已被清理，迟到的 confirm/cancel 返回 False
        # （端点侧将 404，前端静默清卡）
        service = get_writing_chat_service()
        pending_id = events[0]["pending_id"]
        assert service.confirm_pending_tool(pending_id) is False
        assert service.cancel_pending_tool(pending_id) is False

    def test_double_resolve_first_wins(self, env, temp_session_dir, monkeypatch):
        """双 confirm / confirm+cancel 竞态先到先得（单用户 Event 幂等）。"""
        import asyncio

        from backend.modules.writing_chat.service import get_writing_chat_service
        from backend.modules.writing_chat.service.state import StateMixin

        monkeypatch.setattr(StateMixin, "_TOOL_CONFIRM_TTL_SECONDS", 5)
        service = get_writing_chat_service()

        async def scenario():
            # 直接走两阶段登记（编辑器同款路径），不进入完整对话流
            await service._register_tool_confirmation(
                "pid-race-1", "web_search", {"query": "q"}
            )
            assert service.confirm_pending_tool("pid-race-1") is True
            assert service.confirm_pending_tool("pid-race-1") is False
            assert service.cancel_pending_tool("pid-race-1") is False

            await service._register_tool_confirmation(
                "pid-race-2", "web_search", {"query": "q"}
            )
            assert service.confirm_pending_tool("pid-race-2") is True
            assert service.cancel_pending_tool("pid-race-2") is False

            await service._register_tool_confirmation(
                "pid-race-3", "web_search", {"query": "q"}
            )
            assert service.cancel_pending_tool("pid-race-3") is True
            assert service.cancel_pending_tool("pid-race-3") is False
            assert service.confirm_pending_tool("pid-race-3") is False

        asyncio.run(scenario())

    def test_readonly_tools_bypass_confirmation(
        self, env, temp_session_dir, monkeypatch
    ):
        """项目只读工具维持直通：无 pending、无登记，直接执行（回归保障）。"""
        from backend.modules.writing_chat.service import get_writing_chat_service

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = MagicMock()
        m_registry.execute = MagicMock(
            return_value=MagicMock(format_for_llm=MagicMock(return_value="FILE BODY"))
        )
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        service = get_writing_chat_service()
        # 竞态用例可能在单例上残留已决议条目；本用例只关心"直通路径
        # 不登记"，先清空再断言
        service._pending_confirms.clear()
        with patch(
            _LLM_CLIENT_PATH,
            self._make_two_round_llm("read_file_content", {"file_path": "main.tex"}),
        ):
            events, ordering = _drive_editor_send(
                service,
                self._req(),
                env["ws"],
            )

        assert m_registry.execute.call_count == 1
        types = [e["type"] for e in events]
        assert "tool_pending" not in types
        assert "tool_cancelled" not in types
        assert ("yield", "tool_status") in ordering
        assert not any(t[0] == "register" for t in ordering)
        assert service._pending_confirms == {}

    def test_scholarly_and_web_groups_all_require_confirmation(
        self, env, temp_session_dir, monkeypatch
    ):
        """确认流覆盖联网 4 + 学术 5 全部九个工具（确认粒度=逐次）。"""
        from backend.modules.writing_chat.service import get_writing_chat_service
        from backend.modules.writing_chat.service.editor_chat import (
            _CONFIRMABLE_TOOL_NAMES,
            _EDITOR_TOOL_NAMES,
            _SCHOLARLY_TOOL_NAMES,
            _WEB_TOOL_NAMES,
        )

        assert _CONFIRMABLE_TOOL_NAMES == _WEB_TOOL_NAMES | _SCHOLARLY_TOOL_NAMES
        assert len(_CONFIRMABLE_TOOL_NAMES) == 9
        # 只读三件套不在确认分组
        for name in ("list_files", "read_file_content", "search_across_files"):
            assert name not in _CONFIRMABLE_TOOL_NAMES
            assert name in _EDITOR_TOOL_NAMES

        # 行为抽验一个学术分组工具（其余由分组恒等断言覆盖）
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        m_registry = _mock_registry(result_text="DATA")
        monkeypatch.setattr(f"{_EDITOR_CHAT_MOD}.get_tool_registry", lambda: m_registry)

        with patch(
            _LLM_CLIENT_PATH,
            self._make_two_round_llm("get_work_details", {"work_id": "W10.1234"}),
        ):
            events, _ = _drive_editor_send(
                get_writing_chat_service(),
                self._req(),
                env["ws"],
                resolve="confirm",
            )
        assert events[0]["type"] == "tool_pending"
        assert events[0]["tool"] == "get_work_details"

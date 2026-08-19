"""
写作助手模块集成测试。

覆盖：
    - Pydantic 模型校验（ChatMessage / EditOp）
    - ConversationContext：创建/追加/删除/压缩
    - WritingChatService.send（mock chat_stream_with_tools / chat_stream_with_usage）：token 累计、压缩触发、user 删除回滚
    - WritingChatService.generate_edit（mock chat）：分隔符协议提取、校验失败重试、original 后端自填、附挂
    - WritingChatService.apply_edit：写回 .tex
    - 删除消息（含 summary 还原快照）在父子组件中保持 token 计数一致
    - 会话磁盘持久化往返
"""

import time
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import get_config
from backend.core.llm_client import StreamChunk, UsageInfo
from backend.core import workspace_manager as wm_mod
from backend.modules.writing_chat.context import ConversationContext
from backend.modules.writing_chat.models import (
    ChatMessage,
    EditOp,
    GenerateEditRequest,
    ApplyEditRequest,
    DeleteMessageRequest,
    ChatSendRequest,
)
from backend.modules.writing_chat.service import WritingChatService

_LLM_CLIENT_PATH = "backend.modules.writing_chat.service._deps.get_llm_client"


# ── fixtures ──────────────────────────────────────────────────────────────


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
def temp_project(tmp_path):
    root = tmp_path / "tex_project"
    root.mkdir()
    main_content = (
        r"\documentclass{article}" + "\n"
        r"\begin{document}" + "\n"
        r"\section{Introduction}"
        + "\n"
        + "\n"
        + r"This method is not very good for the purpose."
        + "\n"
        + r"It has many weaknesses in practical application."
        + "\n"
        + "\n"
        + r"\section{Method}"
        + "\n"
        + r"\textbf{Bold claim} verified by $x^2$."
        + "\n"
        + r"\end{document}"
    )
    (root / "main.tex").write_text(main_content, encoding="utf-8")
    return root


@pytest.fixture
def open_project(temp_project):
    pm = wm_mod.get_project_manager_for_workspace("test-ws")
    pm.open(str(temp_project))
    yield pm
    pm._project_info = None
    pm._file_md5 = {}


@pytest.fixture
def service():
    return WritingChatService()


# ── Model tests ────────────────────────────────────────────────────────────


class TestModels:
    def test_chat_message_minimal(self):
        m = ChatMessage(id="x", role="user")
        assert m.tokens == 0
        assert m.edit_op is None
        assert m.compressed is False
        assert m.snapshot is None
        assert m.search_results == []
        assert m.search_query == ""
        assert m.deep_read_pages == 0
        assert m.rewritten_queries == []

    def test_chat_message_with_snapshot_roundtrip(self):
        inner = ChatMessage(id="u1", role="user", content="hi", tokens=5)
        summary = ChatMessage(
            id="s1",
            role="summary",
            content="compressed chat",
            tokens=10,
            compressed=True,
            snapshot=[inner],
        )
        dumped = summary.model_dump(mode="json")
        loaded = ChatMessage.model_validate(dumped)
        assert loaded.compressed is True
        assert loaded.snapshot is not None
        assert len(loaded.snapshot) == 1
        assert loaded.snapshot[0].content == "hi"

    def test_chat_message_scholarly_results_roundtrip(self):
        from backend.modules.writing_chat.models import ScholarlyResultSummary

        message = ChatMessage(
            id="a1",
            role="assistant",
            scholarly_results=[ScholarlyResultSummary(id="W123", title="A paper")],
            scholarly_query="paper",
            scholarly_total=10,
        )

        loaded = ChatMessage.model_validate(message.model_dump(mode="json"))
        assert loaded.scholarly_results[0].id == "W123"
        assert loaded.scholarly_query == "paper"
        assert loaded.scholarly_total == 10

    def test_chat_message_search_results_roundtrip(self):
        from backend.modules.writing_chat.models import SearchResultSummary

        message = ChatMessage(
            id="a2",
            role="assistant",
            content="hi",
            search_results=[
                SearchResultSummary(
                    index=1,
                    title="LaTeX 入门教程",
                    snippet="LaTeX 是一个高质量的排版系统。",
                    url="https://example.com/latex-intro",
                )
            ],
            search_query="latex 公式写法",
            deep_read_pages=2,
            rewritten_queries=["q1", "q2"],
        )

        loaded = ChatMessage.model_validate(message.model_dump(mode="json"))
        assert loaded.search_results[0].url == "https://example.com/latex-intro"
        assert loaded.search_results[0].index == 1
        assert loaded.search_query == "latex 公式写法"
        assert loaded.deep_read_pages == 2
        assert loaded.rewritten_queries == ["q1", "q2"]

    def test_edit_op_defaults(self):
        op = EditOp(
            file_path="main.tex",
            paragraph_index=0,
            original_latex="a",
            new_latex="b",
        )
        assert op.applied is False
        assert op.diff_text == ""


# ── ConversationContext tests ─────────────────────────────────────────────


class TestContext:
    def test_create_and_persist_roundtrip(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("帮我分析第三段的引言")
        ctx.append_assistant_message("好的，第三段主要讨论...", tokens=120)
        ctx.save_to_disk()

        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert loaded is not None
        assert loaded.cumulative_tokens == 120
        assert len(loaded.messages) == 2
        assert loaded.messages[0].role == "user"
        assert loaded.messages[1].role == "assistant"

    def test_list_sessions_ordered(self, temp_session_dir, open_project):
        ctx1 = ConversationContext.create_session(scope="test")
        time.sleep(0.01)
        ctx2 = ConversationContext.create_session(scope="test")
        ctx1.append_user_message("a")
        ctx1.save_to_disk()
        ctx2.append_user_message("bb")
        ctx2.save_to_disk()
        metas = ConversationContext.list_sessions()
        assert len(metas) == 2
        # 后创建的 last_updated_at 更大，应排在前
        assert metas[0]["id"] == ctx2.session_id

    def test_list_sessions_empty_title_fallback(self, temp_session_dir, open_project):
        """空会话标题应返回空字符串，由前端按用户语言显示“无标题”文案。"""
        ctx = ConversationContext.create_session(scope="test")
        ctx.save_to_disk()

        metas = ConversationContext.list_sessions()
        assert len(metas) == 1
        assert metas[0]["id"] == ctx.session_id
        assert metas[0]["message_count"] == 0
        assert metas[0]["title"] == ""

    def test_delete_message_reduces_tokens(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        u = ctx.append_user_message("q")
        u.tokens = 50
        ctx.append_assistant_message("a", tokens=20)
        # 手动覆盖以模拟压缩后场景
        ctx.cumulative_tokens = 50 + 20
        ctx.save_to_disk()

        result = ctx.delete_message(ctx.messages[0].id)
        assert result["removed"] is True
        assert result["restored"] is False
        assert ctx.cumulative_tokens == 20
        assert len(ctx.messages) == 1

    def test_delete_summary_restores_snapshot(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("hello")
        ctx.append_assistant_message("hi", tokens=10)
        ctx.append_user_message("more")
        ctx.append_assistant_message("more answer", tokens=15)
        # 模拟压缩：snapshot 保留原 4 条消息、替换为 1 条 summary
        snapshot = ctx.snapshot_for_compression()
        ctx.replace_with_summary("summary text", summary_tokens=8)
        assert ctx.cumulative_tokens == 8
        assert len(ctx.messages) == 1
        assert ctx.messages[0].compressed is True

        result = ctx.delete_message(ctx.messages[0].id)
        assert result["restored"] is True
        assert result["restored_count"] == 4
        assert len(ctx.messages) == 4
        assert ctx.messages[0].content == "hello"

    def test_delete_regular_message(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("q")
        ctx.append_assistant_message("a", tokens=20)
        assert ctx.cumulative_tokens == 20
        result = ctx.delete_message(ctx.messages[1].id)
        assert result["restored"] is False
        assert result["removed"] is True
        assert ctx.cumulative_tokens == 0

    def test_should_compress_threshold(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        ctx.cumulative_tokens = 10
        assert not ctx.should_compress()
        ctx.cumulative_tokens = 10_000_000
        assert ctx.should_compress()

    def test_split_for_compression_keeps_recent_turns(
        self, temp_session_dir, open_project, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 3)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(scope="test")
        for i in range(5):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)

        to_summarize, to_keep = ctx.split_for_compression()
        # 5 轮，保留最近 3 轮，最早 2 轮进入摘要
        assert len(to_summarize) == 4
        assert [m.content for m in to_summarize] == ["q0", "a0", "q1", "a1"]
        assert len(to_keep) == 6
        assert to_keep[0].content == "q2"
        assert to_keep[-1].content == "a4"

    def test_split_for_compression_never_splits_tool_pairs(
        self, temp_session_dir, open_project, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 2)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("q0")
        ctx.append_assistant_message("a0", tokens=5)
        ctx.append_user_message("q1")
        asst = ctx.append_assistant_message("", tokens=5)
        asst.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": "{}"},
            }
        ]
        ctx.append_tool_messages(
            [
                {"role": "tool", "tool_call_id": "call_1", "content": "TOOL_DATA"},
            ]
        )
        ctx.append_assistant_message("a1", tokens=5)
        ctx.append_user_message("q2")
        ctx.append_assistant_message("a2", tokens=5)

        to_summarize, to_keep = ctx.split_for_compression()
        # 保留最近 2 轮（q2/a2、q1 的完整工具轮），最早 q0/a0 摘要
        assert len(to_summarize) == 2
        assert [m.content for m in to_summarize] == ["q0", "a0"]
        kept_roles = [m.role for m in to_keep]
        assert kept_roles == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
            "assistant",
        ]
        assert to_keep[0].content == "q1"
        assert to_keep[-1].content == "a2"

    def test_replace_with_summary_keeps_recent_messages(
        self, temp_session_dir, open_project, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 2)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(scope="test")
        for i in range(4):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)
        to_summarize, to_keep = ctx.split_for_compression()

        summary_msg = ctx.replace_with_summary(
            "summary", summary_tokens=7, keep_messages=to_keep
        )
        assert summary_msg.compressed is True
        assert [m.role for m in ctx.messages] == [
            "summary",
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert [m.content for m in ctx.messages[1:]] == ["q2", "a2", "q3", "a3"]
        # snapshot 只含被替换的旧消息（嵌套快照语义）
        assert [m.content for m in summary_msg.snapshot] == ["q0", "a0", "q1", "a1"]
        # cumulative = summary tokens + 保留消息按字符数/3.5 的估算
        # （4 条消息各 2 字符：int(2/3.5)+1 = 1，合计 4）
        assert ctx.cumulative_tokens == 7 + 4

    def test_replace_with_summary_ignores_inflated_message_tokens(
        self, temp_session_dir, open_project, monkeypatch
    ):
        """保留消息 tokens 字段存的是历史请求 total 的分摊值，不能作为增量累加。"""
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 1)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(scope="test")
        for i in range(3):
            ctx.append_user_message(f"用户问题{i}")
            ctx.append_assistant_message(f"助手回答{i}", tokens=100_000)

        to_summarize, to_keep = ctx.split_for_compression()
        assert len(to_summarize) == 4
        assert len(to_keep) == 2

        ctx.replace_with_summary("summary", summary_tokens=7, keep_messages=to_keep)
        # 按字符数/3.5 估算保留消息，而不是累加 100000 级别的历史 total
        expected_kept = sum(
            ConversationContext._estimate_message_tokens(m) for m in to_keep
        )
        assert ctx.cumulative_tokens == 7 + expected_kept
        assert ctx.cumulative_tokens < 1000

    def test_should_compress_dynamic_window(
        self, temp_session_dir, open_project, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 0)
        monkeypatch.setattr(cfg, "compression_headroom_tokens", 30000)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 20000)
        monkeypatch.setattr(cfg, "summary_token_budget", 6000)
        monkeypatch.setattr(get_config().llm, "max_tokens", 8192)

        ctx = ConversationContext.create_session(scope="test")
        # 128000 - 8192 - 30000 - 20000 - 6000 = 63808
        ctx.cumulative_tokens = 63807
        assert not ctx.should_compress(window_tokens=128000)
        ctx.cumulative_tokens = 63808
        assert ctx.should_compress(window_tokens=128000)
        # 固定阈值仍可直接覆盖
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 100)
        ctx.cumulative_tokens = 99
        assert not ctx.should_compress()
        ctx.cumulative_tokens = 100
        assert ctx.should_compress()


# ── Context XML envelope tests ─────────────────────────────────────────────


class TestContextXmlEnvelope:
    """测试 build_file_context_text 的 XML 信封格式和检索策略。"""

    def test_xml_envelope_basic_structure(self, open_project):
        """XML 信封包含 <documents> → <document> → <document_content> → <paragraph>。"""
        from backend.modules.writing_chat.context import (
            _documents_envelope,
        )

        result = _documents_envelope(
            "main.tex",
            [(0, "Hello"), (1, "World")],
            total_paragraphs=2,
            truncated=False,
        )
        assert result.startswith("<documents>")
        assert result.endswith("</documents>")
        assert '<document source="main.tex">' in result
        assert "<total_paragraphs>2</total_paragraphs>" in result
        assert "<document_content>" in result
        assert "</document_content>" in result
        assert '<paragraph index="0">' in result
        assert '<paragraph index="1">' in result
        assert "Hello" in result
        assert "World" in result

    def test_truncation_note_present_when_truncated(self, open_project):
        """截断时包含 <context_note>。"""
        from backend.modules.writing_chat.context import (
            _documents_envelope,
        )

        result = _documents_envelope(
            "main.tex",
            [(0, "A"), (1, "B")],
            total_paragraphs=20,
            truncated=True,
        )
        assert "<context_note>" in result
        assert "18 of 20" in result
        assert "truncated" in result.lower() or "Truncated" in result
        assert "read_file_content" in result

    def test_no_truncation_note_when_not_truncated(self, open_project):
        """未截断时无 <context_note>。"""
        from backend.modules.writing_chat.context import (
            _documents_envelope,
        )

        result = _documents_envelope(
            "main.tex",
            [(0, "A"), (1, "B")],
            total_paragraphs=2,
            truncated=False,
        )
        assert "<context_note>" not in result

    def test_error_state_in_envelope(self, open_project):
        """加载失败时信封包含错误信息。"""
        from backend.modules.writing_chat.context import (
            _documents_envelope,
        )

        result = _documents_envelope(
            "missing.tex",
            [],
            0,
            error="file not found",
        )
        assert "<context_note>Error loading file:" in result
        assert "file not found" in result

    def test_xml_attr_escaping(self, open_project):
        """文件路径中的特殊字符被正确转义。"""
        from backend.modules.writing_chat.context import (
            _documents_envelope,
        )

        result = _documents_envelope(
            'path with "quotes" & <brackets>',
            [(0, "text")],
            total_paragraphs=1,
        )
        assert "&quot;" in result
        assert "&amp;" in result
        assert "&lt;" in result

    def test_anchor_selection_radius(self, open_project):
        """指定段落时取 ±ANCHOR_RADIUS 范围内的段落。"""
        from backend.modules.writing_chat.context import (
            _select_anchor,
        )

        paras = [(i, f"para{i}") for i in range(20)]
        selected, indices = _select_anchor(paras, 10, radius=2)
        assert indices == {8, 9, 10, 11, 12}
        assert len(selected) == 5

    def test_anchor_selection_at_boundary(self, open_project):
        """锚点在边界时不越界。"""
        from backend.modules.writing_chat.context import (
            _select_anchor,
        )

        paras = [(i, f"para{i}") for i in range(5)]
        selected, indices = _select_anchor(paras, 0, radius=2)
        assert indices == {0, 1, 2}
        selected2, indices2 = _select_anchor(paras, 4, radius=2)
        assert indices2 == {2, 3, 4}

    def test_sample_selection_head_middle_tail(self, open_project):
        """无指定段落时从 head/middle/tail 采样。"""
        from backend.modules.writing_chat.context import (
            _select_sample,
        )

        paras = [(i, f"para{i}") for i in range(30)]
        selected, indices, truncated = _select_sample(paras, 3, 3, 3)
        assert truncated is True
        assert len(selected) <= 9
        # head 段：0, 1, 2
        assert 0 in indices and 1 in indices and 2 in indices
        # tail 段：27, 28, 29
        assert 27 in indices and 28 in indices and 29 in indices

    def test_sample_selection_small_file_no_truncation(self, open_project):
        """段落总数 <= head+middle+tail 时不截断。"""
        from backend.modules.writing_chat.context import (
            _select_sample,
        )

        paras = [(i, f"para{i}") for i in range(5)]
        selected, indices, truncated = _select_sample(paras, 3, 3, 3)
        assert truncated is False
        assert len(selected) == 5

    def test_paragraph_index_minus1_returns_empty(self, open_project):
        """paragraph_index=-1 返回空字符串。"""
        result = ConversationContext.build_file_context_text(
            "main.tex", -1, workspace_id="test-ws"
        )
        assert result == ""

    def test_paragraph_index_specific_returns_anchored_paras(self, open_project):
        """指定段落返回锚点 ±2 范围的段落 XML。"""
        result = ConversationContext.build_file_context_text(
            "main.tex", 1, workspace_id="test-ws"
        )
        assert "<documents>" in result
        assert '<paragraph index="1">' in result
        # 应该包含附近的段落 (0, 1, 2)
        assert '<paragraph index="0">' in result
        assert '<paragraph index="2">' in result


# ── Service.send tests (mock chat_stream_with_usage) ──────────────────────


def _stream(chunks: list[StreamChunk]):
    async def gen() -> AsyncIterator[StreamChunk]:
        for c in chunks:
            yield c

    return gen()


class TestServiceSend:
    @pytest.mark.asyncio
    async def test_send_streams_tokens_and_persists(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        chunks = [
            StreamChunk(content="Hello", usage=None),
            StreamChunk(content=" world", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=12, completion_tokens=3, total_tokens=15),
            ),
        ]
        with patch(_LLM_CLIENT_PATH) as m_llm:
            m_client = MagicMock()
            m_client.chat_stream_with_tools = MagicMock(return_value=_stream(chunks))

            m_llm.return_value = m_client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="帮我分析这段",
            )
            events = []
            async for ev in service.send(req):
                events.append(ev)

        types = [e["type"] for e in events]
        assert "token" in types
        assert types[-1] == "done"
        session_id = events[-1]["session_id"]
        ctx = ConversationContext.load_from_disk(session_id)
        assert ctx is not None
        assert len(ctx.messages) == 2
        assert ctx.messages[-1].content == "Hello world"
        assert ctx.cumulative_tokens == 15

    @pytest.mark.asyncio
    async def test_send_triggers_compression(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        # 先准备一条已超阈值的会话（含多条历史消息以便通过压缩最小消息数阈值）
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 100)
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 3)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(5):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=20)
        ctx.cumulative_tokens = 500  # 模拟已超阈值
        ctx.save_to_disk()

        # mock 压缩 LLM 返回纯文本 summary + 真实 usage
        summary_response = "压缩摘要内容"

        async def fake_chat_with_usage(messages, **kwargs):
            return summary_response, UsageInfo(
                prompt_tokens=100, completion_tokens=12, total_tokens=112
            )

        chunks = [
            StreamChunk(content="reply", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            ),
        ]
        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_with_usage = fake_chat_with_usage
            client.chat_stream_with_tools = MagicMock(return_value=_stream(chunks))

            m_llm.return_value = client

            req = ChatSendRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                user_message="another question",
            )
            events = []
            async for ev in service.send(req):
                events.append(ev)

        types = [e["type"] for e in events]
        assert "compressed" in types
        assert types[-1] == "done"
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        # 压缩后结构应为 [summary, 保留的最近轮次..., 本轮 user, assistant]：
        # 本轮 user 必须以真实 user 角色出现在 summary 之后
        summary_msgs = [
            m for m in loaded.messages if m.role == "summary" and m.compressed
        ]
        assert len(summary_msgs) == 1
        # 保留最近 3 轮（6 条），最早 2 轮（4 条）进入 snapshot；
        # snapshot 不含本轮 user
        assert summary_msgs[0].snapshot is not None
        assert len(summary_msgs[0].snapshot) == 4
        assert all(m.role == "user" for m in summary_msgs[0].snapshot[::2])
        assert all(m.content != "another question" for m in summary_msgs[0].snapshot)
        roles = [m.role for m in loaded.messages]
        assert roles[0] == "summary"
        assert "user" in roles[1:]
        assert loaded.messages[-2].role == "user"
        assert loaded.messages[-2].content == "another question"
        assert loaded.messages[-1].role == "assistant"
        assert loaded.messages[-1].content == "reply"

    @pytest.mark.asyncio
    async def test_send_no_api_key_emits_error(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        cfg = get_config()
        monkeypatch.setattr(cfg.llm, "api_key", "")
        req = ChatSendRequest(file_path="main.tex", user_message="x")
        events = [e async for e in service.send(req)]
        assert any(e.get("type") == "error" for e in events)

    @pytest.mark.asyncio
    async def test_send_tool_loop_overflow_recovery(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """工具循环上下文超限 → 压缩历史 → 重试同一轮，用户消息不被删除。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 100_000_000)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 5)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(7):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)
        ctx.save_to_disk()

        from backend.utils.web_search import SearchResponse, SearchResult

        search_resp = SearchResponse(
            query="q",
            results=[SearchResult(title="t", snippet="s", url="https://e.com/1")],
            engine="duckduckgo",
        )
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=search_resp)

        async def fake_chat_with_usage(messages, **kwargs):
            return "compressed summary", UsageInfo(
                prompt_tokens=10, completion_tokens=3, total_tokens=13
            )

        tool_round = [
            StreamChunk(content=None, usage=None, tool_calls=[_make_tool_call()]),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            ),
        ]
        final_round = [
            StreamChunk(content="final reply", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            ),
        ]

        async def overflow_gen():
            raise RuntimeError("This model's maximum context length is 128000 tokens")
            yield  # pragma: no cover

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    overflow_gen(),
                    _stream(tool_round),
                    _stream(final_round),
                ]
            )
            client.chat_with_usage = fake_chat_with_usage
            m_llm.return_value = client

            req = ChatSendRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                user_message="overflow question",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        types = [e["type"] for e in events]
        assert "error" not in types
        assert "compressed" in types
        assert types[-1] == "done"

        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert loaded.messages[0].role == "summary"
        # 本轮 user 保留在 summary 之后
        user_contents = [m.content for m in loaded.messages if m.role == "user"]
        assert "overflow question" in user_contents
        # 恢复后的工具调用轮完整持久化
        assert any(m.role == "tool" for m in loaded.messages)
        assert loaded.messages[-1].content == "final reply"

    @pytest.mark.asyncio
    async def test_send_overflow_exhaustion_keeps_user_message(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """超限恢复重试耗尽后仍保留本轮用户消息（不静默删除）。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 100_000_000)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.save_to_disk()

        async def overflow_gen():
            raise RuntimeError("This model's maximum context length is 128000 tokens")
            yield  # pragma: no cover

        recovered = (
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "overflow question"},
            ],
            [],
            False,
        )
        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[overflow_gen(), overflow_gen(), overflow_gen()]
            )
            m_llm.return_value = client
            recover_mock = AsyncMock(return_value=recovered)
            service._recover_from_context_overflow = recover_mock

            req = ChatSendRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                user_message="overflow question",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" not in types
        # 前两次超限进入恢复，第三次重试耗尽直接报错
        assert recover_mock.await_count == 2

        loaded = ConversationContext.load_from_disk(ctx.session_id)
        user_contents = [m.content for m in loaded.messages if m.role == "user"]
        assert "overflow question" in user_contents


# ── 压缩细节（阶段 2-5） ──────────────────────────────────────────────────


class TestCompressionDetails:
    @pytest.mark.asyncio
    async def test_maybe_compress_summary_input_excludes_tool_content(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 1)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("q0")
        ctx.append_assistant_message("a0", tokens=5)
        ctx.append_user_message("q1")
        asst = ctx.append_assistant_message("", tokens=5)
        asst.tool_calls = [
            {
                "id": "call_secret",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query":"secret_args"}',
                },
            }
        ]
        ctx.append_tool_messages(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call_secret",
                    "content": "SECRET_TOOL_PAGE 内容很长",
                },
            ]
        )
        ctx.append_assistant_message("a1 final", tokens=5)
        ctx.append_user_message("q2")
        ctx.append_assistant_message("a2", tokens=5)

        captured = {}

        async def fake_chat_with_usage(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return "sum", UsageInfo(
                prompt_tokens=10, completion_tokens=3, total_tokens=13
            )

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_with_usage = fake_chat_with_usage
            m_llm.return_value = client
            ok = await service._maybe_compress(ctx)

        assert ok is True
        assert "q0" in captured["user"]
        assert "q1" in captured["user"]
        assert "SECRET_TOOL_PAGE" not in captured["user"]
        assert "secret_args" not in captured["user"]
        assert ctx.messages[0].role == "summary"
        assert [m.content for m in ctx.messages[1:]] == ["q2", "a2"]
        # snapshot 保留被替换的旧消息（含 tool 对，仅用于还原展示）
        assert len(ctx.messages[0].snapshot) == 6

    @pytest.mark.asyncio
    async def test_maybe_compress_fallback_on_empty_summary(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 2)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(5):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)

        async def fake_chat_with_usage(messages, **kwargs):
            return "", None

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_with_usage = fake_chat_with_usage
            m_llm.return_value = client
            ok = await service._maybe_compress(ctx)

        assert ok is True  # 确定性兜底而非跳过
        assert ctx.messages[0].role == "summary"
        assert "compacted" in ctx.messages[0].content
        assert [m.content for m in ctx.messages[1:]] == ["q3", "a3", "q4", "a4"]

    @pytest.mark.asyncio
    async def test_maybe_compress_overflow_retry_expands_keep(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 3)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(10):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)

        calls = {"n": 0}

        async def fake_chat_with_usage(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "This model's maximum context length is 128000 tokens"
                )
            return "sum", UsageInfo(
                prompt_tokens=1, completion_tokens=2, total_tokens=3
            )

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_with_usage = fake_chat_with_usage
            m_llm.return_value = client
            ok = await service._maybe_compress(ctx)

        assert ok is True
        assert calls["n"] == 2
        # 第一次保留 3 轮，超限后扩大为保留 6 轮 → 摘要 4 轮（8 条）
        assert len(ctx.messages[0].snapshot) == 8
        assert len(ctx.messages) == 13  # summary + 12 条保留消息

    @pytest.mark.asyncio
    async def test_manual_compact_session(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_keep_recent_turns", 3)
        monkeypatch.setattr(cfg, "compression_keep_recent_tokens", 10_000_000)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(5):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=10)
        ctx.save_to_disk()

        async def fake_chat_with_usage(messages, **kwargs):
            return "sum", UsageInfo(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            )

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat_with_usage = fake_chat_with_usage
            m_llm.return_value = client
            result = await service.compact_session(ctx.session_id)

        assert result["compressed"] is True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert loaded.messages[0].role == "summary"
        assert loaded.messages[0].tokens == 5  # 真实 usage.completion_tokens
        assert len(loaded.messages[0].snapshot) == 4
        assert loaded.messages[-1].content == "a4"

    @pytest.mark.asyncio
    async def test_recover_from_context_overflow_trims_before_compression(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """超限恢复优先裁剪旧工具对，只有裁剪无效才走压缩。"""
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("q")
        ctx.save_to_disk()
        tool_messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "old",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "old", "content": "X" * 40000},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "cur",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "cur", "content": "Y" * 40000},
        ]
        service._maybe_compress = AsyncMock(return_value=True)

        result = await service._recover_from_context_overflow(
            ctx, "sys", [], tool_messages
        )

        assert result is not None
        rebuilt, refs, compressed_flag = result
        assert compressed_flag is False
        assert refs == []
        service._maybe_compress.assert_not_called()
        # 旧工具对被裁剪，当前轮（最后一条 tool 消息）完整保留
        tool_contents = [m.get("content") for m in rebuilt if m.get("role") == "tool"]
        assert len(tool_contents) == 1
        assert tool_contents[0].startswith("Y")


# ── Service.generate_edit / apply_edit tests ──────────────────────────────


class TestServiceEdit:
    @pytest.mark.asyncio
    async def test_generate_edit_raw_latex(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        # 选取含 LaTeX 命令的段落，直接发送原始 LaTeX 源码
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "Bold claim" in p.text)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("把这段改得更学术")
        ctx.append_assistant_message("好的，建议...", tokens=10)
        ctx.save_to_disk()

        # mock LLM：按分隔符协议输出改写后的 raw LaTeX（保留 LaTeX 结构）
        new_latex = para.text.replace("verified", "verified (empirically)")

        async def fake_chat(messages, **kwargs):
            return (
                "<<<LATEX>>>\n" + new_latex + "\n<<<END>>>\n"
                "<<<REASON>>>\nmore precise\n<<<END>>>"
            )

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="把这段改得更学术",
            )
            result = await service.generate_edit(req, workspace_id="test-ws")

        assert result["session_id"] == ctx.session_id
        assert result["reason"] == "more precise"
        op = EditOp.model_validate(result["edit_op"])
        assert op.applied is False
        # original 由后端从段落原文填充，不再要求 LLM 回显
        assert op.original_latex == para.text
        # 分隔符块内容直接用作 new_latex，反斜杠原样保留
        assert "empirically" in op.new_latex
        assert r"\textbf" in op.new_latex
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        attached = next(
            m for m in loaded.messages if m.id == result["attached_message_id"]
        )
        assert attached.role == "assistant"
        assert attached.edit_op is not None
        assert attached.edit_op.applied is False

    @pytest.mark.asyncio
    async def test_generate_edit_validation_failure_retries(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        """
        LLM 输出的 LATEX 块含内容损坏（非法控制字符 \\f）→ 宽松校验
        拦截失败 → 回喂重试 → 最终 edit_op 无控制字符。
        """
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "Bold claim" in p.text)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("把这段改得更学术")
        ctx.append_assistant_message("好的，建议...", tokens=10)
        ctx.save_to_disk()

        calls = {"n": 0}

        async def fake_chat(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # 内容损坏：混入非法控制字符（\\f，U+000C）
                return "<<<LATEX>>>\nR^\fho is fine\n<<<END>>>"
            new_latex = para.text.replace("verified", "verified (empirically)")
            return "<<<LATEX>>>\n" + new_latex + "\n<<<END>>>"

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="把这段改得更学术",
            )
            result = await service.generate_edit(req, workspace_id="test-ws")

        assert calls["n"] == 2
        op = EditOp.model_validate(result["edit_op"])
        assert "empirically" in op.new_latex
        assert "\f" not in op.new_latex
        assert r"\textbf" in op.new_latex

    @pytest.mark.asyncio
    async def test_generate_edit_missing_block_retries(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        """LLM 首轮未按分隔符协议输出 → 回喂重试 → 第二轮成功。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "Bold claim" in p.text)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("把这段改得更学术")
        ctx.append_assistant_message("好的，建议...", tokens=10)
        ctx.save_to_disk()

        new_latex = para.text.replace("verified", "verified (empirically)")
        calls = {"n": 0}

        async def fake_chat(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # 未包含 <<<LATEX>>> 块（例如普通 Markdown 回复）
                return "Here is the rewritten paragraph:\n" + new_latex
            return "<<<LATEX>>>\n" + new_latex + "\n<<<END>>>"

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="把这段改得更学术",
            )
            result = await service.generate_edit(req, workspace_id="test-ws")

        assert calls["n"] == 2
        op = EditOp.model_validate(result["edit_op"])
        assert "empirically" in op.new_latex
        assert op.original_latex == para.text

    @pytest.mark.asyncio
    async def test_generate_edit_empty_block_retries(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        """LATEX 块为空 → 协议失败 → 回喂重试。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "Bold claim" in p.text)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("把这段改得更学术")
        ctx.append_assistant_message("好的，建议...", tokens=10)
        ctx.save_to_disk()

        new_latex = para.text.replace("verified", "verified (empirically)")
        calls = {"n": 0}

        async def fake_chat(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "<<<LATEX>>>\n   \n<<<END>>>"
            return "<<<LATEX>>>\n" + new_latex + "\n<<<END>>>"

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="把这段改得更学术",
            )
            result = await service.generate_edit(req, workspace_id="test-ws")

        assert calls["n"] == 2
        op = EditOp.model_validate(result["edit_op"])
        assert "empirically" in op.new_latex

    @pytest.mark.asyncio
    async def test_generate_edit_crlf_paragraph_no_false_positive(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        """
        CRLF 行尾的段落：original 由后端自填（不再经 LLM 回显/校验），
        optimized 走非 JSON 宽松校验（放行 \\r），不触发控制字符误报。
        """
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        from backend.core.project_manager import Paragraph
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        # safe_read 使用通用换行会把文件里的 \r\n 规范化为 \n，因此直接
        # 构造含 CRLF 行尾的内存 Paragraph，聚焦验证 service 层行为。
        para = Paragraph(
            index=0,
            text="First line with CRLF.\r\nSecond line with CRLF.",
            file_path="main.tex",
            line_start=3,
            line_end=4,
        )
        assert "\r" in para.text
        monkeypatch.setattr(pm, "get_paragraphs", lambda file_path: [para])

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("润色这段")
        ctx.append_assistant_message("好的", tokens=5)
        ctx.save_to_disk()

        new_latex = para.text.replace("First line", "First line (revised)")
        calls = {"n": 0}

        async def fake_chat(messages, **kwargs):
            calls["n"] += 1
            # LLM 回显保留了 CRLF 行尾
            return "<<<LATEX>>>\n" + new_latex + "\n<<<END>>>"

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="润色这段",
            )
            result = await service.generate_edit(req, workspace_id="test-ws")

        # 未触发重试，CRLF 不再误报
        assert calls["n"] == 1
        op = EditOp.model_validate(result["edit_op"])
        assert op.original_latex == para.text
        assert "First line (revised)" in op.new_latex
        assert "\r" in op.new_latex

    @pytest.mark.asyncio
    async def test_generate_edit_protocol_failure_raises(
        self, temp_session_dir, temp_project, open_project, service, monkeypatch
    ):
        """LLM 始终不按分隔符协议输出 → 重试耗尽 → RuntimeError。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "Bold claim" in p.text)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("把这段改得更学术")
        ctx.append_assistant_message("好的，建议...", tokens=10)
        ctx.save_to_disk()

        calls = {"n": 0}

        async def fake_chat(messages, **kwargs):
            calls["n"] += 1
            return "Here is the modified text: ..."  # 始终无 LATEX 块

        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            m_llm.return_value = client

            req = GenerateEditRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                paragraph_index=para.index,
                instruction="把这段改得更学术",
            )
            with pytest.raises(RuntimeError, match="Edit generation failed"):
                await service.generate_edit(req, workspace_id="test-ws")

        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_apply_edit_writes_back_to_file(
        self, temp_session_dir, temp_project, open_project, service
    ):
        # 准备 session + 一条带 edit_op 的 assistant 消息
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("instruction")
        am = ctx.append_assistant_message("ok", tokens=5)

        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "not very good" in p.text)
        new_text = para.text.replace("not very good", "suboptimal")
        ctx.attach_edit_op(
            am.id,
            EditOp(
                file_path="main.tex",
                paragraph_index=para.index,
                original_latex=para.text,
                new_latex=new_text,
            ),
        )
        ctx.save_to_disk()

        req = ApplyEditRequest(
            session_id=ctx.session_id,
            edit_message_id=am.id,
        )
        result = await service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "not very good" not in content
        assert "suboptimal" in content

        # 持久化中 edit_op.applied 应为 True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        target = next(m for m in loaded.messages if m.id == am.id)
        assert target.edit_op.applied is True

    @pytest.mark.asyncio
    async def test_apply_edit_already_applied_returns_idempotent(
        self, temp_session_dir, open_project, service
    ):
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("q")
        am = ctx.append_assistant_message("a", tokens=3)
        paragraphs = open_project.get_paragraphs("main.tex")
        para = paragraphs[0]
        ctx.attach_edit_op(
            am.id,
            EditOp(
                file_path="main.tex",
                paragraph_index=para.index,
                original_latex=para.text,
                new_latex=para.text + " edited",
                applied=True,
            ),
        )
        ctx.save_to_disk()

        req = ApplyEditRequest(session_id=ctx.session_id, edit_message_id=am.id)
        result = await service.apply_edit(req)
        assert result == {"applied": True, "already_applied": True}

    @pytest.mark.asyncio
    async def test_apply_edit_with_custom_latex_uses_custom_value(
        self, temp_session_dir, temp_project, open_project, service
    ):
        # 测试当请求携带 new_latex（用户手动编辑内容）时，应优先使用该值
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("instruction")
        am = ctx.append_assistant_message("ok", tokens=5)

        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "not very good" in p.text)
        # edit_op.new_latex 是 LLM 建议的内容
        llm_suggested_text = para.text.replace("not very good", "suboptimal")
        # 用户手动编辑后的内容
        user_custom_text = para.text.replace("not very good", "unsatisfactory")

        ctx.attach_edit_op(
            am.id,
            EditOp(
                file_path="main.tex",
                paragraph_index=para.index,
                original_latex=para.text,
                new_latex=llm_suggested_text,
            ),
        )
        ctx.save_to_disk()

        # 请求中携带用户手动编辑的内容
        req = ApplyEditRequest(
            session_id=ctx.session_id,
            edit_message_id=am.id,
            new_latex=user_custom_text,
        )
        result = await service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        # 应写入用户手动编辑的内容，而非 LLM 建议的内容
        assert "not very good" not in content
        assert "unsatisfactory" in content
        assert "suboptimal" not in content

    @pytest.mark.asyncio
    async def test_apply_edit_without_custom_latex_falls_back_to_edit_op(
        self, temp_session_dir, temp_project, open_project, service
    ):
        # 测试当请求不携带 new_latex 时，应使用 edit_op.new_latex（LLM 建议内容）
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("instruction")
        am = ctx.append_assistant_message("ok", tokens=5)

        paragraphs = pm.get_paragraphs("main.tex")
        para = next(p for p in paragraphs if "not very good" in p.text)
        llm_suggested_text = para.text.replace("not very good", "suboptimal")

        ctx.attach_edit_op(
            am.id,
            EditOp(
                file_path="main.tex",
                paragraph_index=para.index,
                original_latex=para.text,
                new_latex=llm_suggested_text,
            ),
        )
        ctx.save_to_disk()

        # 请求中不携带 new_latex
        req = ApplyEditRequest(
            session_id=ctx.session_id,
            edit_message_id=am.id,
        )
        result = await service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        # 应写入 LLM 建议的内容
        assert "not very good" not in content
        assert "suboptimal" in content


# ── Delete message via service ─────────────────────────────────────────────


class TestServiceMessageOps:
    @pytest.mark.asyncio
    async def test_delete_message(self, temp_session_dir, open_project, service):
        ctx = ConversationContext.create_session(scope="test")
        u = ctx.append_user_message("q")
        ctx.append_assistant_message("a", tokens=10)
        ctx.save_to_disk()

        result = await service.delete_message(
            DeleteMessageRequest(session_id=ctx.session_id, message_id=u.id)
        )
        assert result["removed"] is True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert len(loaded.messages) == 1
        # user 消息 tokens=0，删除后 cumulative_tokens 不变
        assert loaded.cumulative_tokens == 10

    @pytest.mark.asyncio
    async def test_delete_summary_message(
        self, temp_session_dir, open_project, service
    ):
        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("q1")
        ctx.append_assistant_message("a1", tokens=20)
        ctx.replace_with_summary("sum", summary_tokens=5)
        ctx.save_to_disk()
        summary_id = ctx.messages[0].id

        result = await service.delete_message(
            DeleteMessageRequest(session_id=ctx.session_id, message_id=summary_id)
        )
        assert result["restored"] is True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert len(loaded.messages) == 2
        # snapshot 还原后 user(0) + assistant(20)，累计 = 20
        assert loaded.cumulative_tokens == 20


# ── Web search tool integration tests ─────────────────────────────────────


_TOOL_REGISTRY_PATH = (
    "backend.modules.writing_chat.service.conversation.get_tool_registry"
)


def _make_tool_call(call_id: str = "call_ws", query: str = "latex tutorial"):
    """构造 openai tool_call 对象。"""
    import json
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall,
        Function,
    )

    return ChatCompletionMessageToolCall(
        id=call_id,
        function=Function(name="web_search", arguments=json.dumps({"query": query})),
        type="function",
    )


def _make_search_response(query: str = "latex tutorial"):
    """构造 SearchResponse 测试数据。"""
    from backend.utils.web_search import SearchResponse, SearchResult

    return SearchResponse(
        query=query,
        results=[
            SearchResult(
                title="LaTeX 入门教程",
                snippet="LaTeX 是一个高质量的排版系统。",
                url="https://example.com/latex-intro",
            ),
            SearchResult(
                title="Overleaf 文档",
                snippet="Overleaf 是在线 LaTeX 编辑器。",
                url="https://overleaf.com",
            ),
        ],
        engine="duckduckgo",
        total_results=2,
    )


class TestMultiRoundToolStreamConsistency:
    """验证多轮工具调用时，实时流展示与落盘 JSON 内容保持一致。

    - 工具调用前轮次输出的思考片段只做实时展示（可能被后续轮次重置），
      最终落盘的 assistant 内容仅包含最后一轮完整回复
    - 无工具调用的一轮内容即最终回复，不再重复生成
    - max_rounds 用尽时才会额外生成一次最终回复
    - web_search 结果随 assistant 消息落盘，刷新后与实时展示一致
    """

    @pytest.mark.asyncio
    async def test_send_tool_free_round_is_final_answer(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        search_resp = _make_search_response("latex 公式写法")

        # 第 1 轮：模型先输出思考片段，再发起工具调用
        round1_chunks = [
            StreamChunk(content="<thinking>\n1. 核心主张：测试", usage=None),
            StreamChunk(
                content=None,
                usage=None,
                tool_calls=[_make_tool_call(query="latex 公式写法")],
            ),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=3, total_tokens=13),
            ),
        ]
        # 第 2 轮：无工具调用，该轮内容即最终回复（循环在此结束）
        round2_chunks = [
            StreamChunk(
                content=(
                    "2. 维度扫描：无创新\n"
                    "</thinking>\n\n"
                    "### 总体评价\n"
                    "**拒稿**"
                ),
                usage=None,
            ),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=20, completion_tokens=8, total_tokens=28),
            ),
        ]

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=search_resp)

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(round1_chunks),
                    _stream(round2_chunks),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="这篇文章怎么样",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        types = [e["type"] for e in events]
        # 第 2 轮流开始前发出 round_start 重置第 1 轮的思考片段
        assert "round_start" in types
        assert types[-1] == "done"
        # round_start 只出现一次（第 2 轮流开始前），无最终轮重复生成
        assert types.count("round_start") == 1
        # 无工具调用的一轮即最终回复，不应再调用 chat_stream_with_usage 重复生成
        client.chat_stream_with_usage.assert_not_called()
        # round_start 之后流出的 token 才是落盘内容
        last_round_start = len(types) - 1 - types[::-1].index("round_start")
        persisted_tokens = "".join(
            e["content"] for e in events[last_round_start + 1 :] if e["type"] == "token"
        )
        assert persisted_tokens == (
            "2. 维度扫描：无创新\n</thinking>\n\n### 总体评价\n**拒稿**"
        )

        # 落盘内容只包含最终一轮回复，不含第 1 轮的思考片段
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        assert ctx is not None
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert len(final_msgs) == 1
        assert final_msgs[0].content == (
            "2. 维度扫描：无创新\n</thinking>\n\n### 总体评价\n**拒稿**"
        )
        assert "<thinking>\n1. 核心主张：测试" not in final_msgs[0].content
        # 工具调用/结果消息已持久化（assistant+tool_calls / tool 各 1 条）
        tool_msgs = [m for m in ctx.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "LaTeX 入门教程" in tool_msgs[0].content
        # web_search 结果随 assistant 消息落盘，刷新后搜索卡片保持一致
        assert len(final_msgs[0].search_results) == 2
        assert final_msgs[0].search_results[0].title == "LaTeX 入门教程"
        assert final_msgs[0].search_query == "latex 公式写法"

    @pytest.mark.asyncio
    async def test_send_regenerates_final_answer_when_max_rounds_exhausted(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """全部轮次都请求工具调用时，循环结束后才生成一次最终回复并落盘。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        search_resp = _make_search_response("latex 公式写法")

        tool_round_chunks = [
            StreamChunk(content="继续检索", usage=None),
            StreamChunk(
                content=None,
                usage=None,
                tool_calls=[_make_tool_call(query="latex 公式写法")],
            ),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=3, total_tokens=13),
            ),
        ]
        final_chunks = [
            StreamChunk(content="### 总体评价\n**拒稿**", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=20, completion_tokens=8, total_tokens=28),
            ),
        ]

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=search_resp)

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(tool_round_chunks),
                    _stream(tool_round_chunks),
                    _stream(tool_round_chunks),
                    _stream(tool_round_chunks),
                    _stream(tool_round_chunks),
                    _stream(tool_round_chunks),
                ]
            )
            client.chat_stream_with_usage = MagicMock(
                return_value=_stream(final_chunks)
            )
            m_llm.return_value = client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="这篇文章怎么样",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        types = [e["type"] for e in events]
        assert types[-1] == "done"
        # 第 2..6 轮流开始 + 最终回复流开始，共 6 个 round_start
        assert types.count("round_start") == 6
        assert mock_registry.execute.await_count == 6
        client.chat_stream_with_usage.assert_called_once()
        # 最终回复前的 round_start 之后流出的 token 才是落盘内容
        last_round_start = len(types) - 1 - types[::-1].index("round_start")
        persisted_tokens = "".join(
            e["content"] for e in events[last_round_start + 1 :] if e["type"] == "token"
        )
        assert persisted_tokens == "### 总体评价\n**拒稿**"

        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        assert ctx is not None
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert len(final_msgs) == 1
        assert final_msgs[0].content == "### 总体评价\n**拒稿**"
        # 六轮工具结果均持久化
        tool_msgs = [m for m in ctx.messages if m.role == "tool"]
        assert len(tool_msgs) == 6


class TestWebSearchToolEvent:
    """验证 web_search 工具结果事件（tool_result 含 search_results）的构造逻辑。"""

    @pytest.mark.asyncio
    async def test_send_web_search_emits_search_results(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        search_resp = _make_search_response("latex 公式写法")

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=search_resp)

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(
                        [
                            StreamChunk(
                                content=None,
                                usage=None,
                                tool_calls=[_make_tool_call(query="latex 公式写法")],
                            ),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=15,
                                    completion_tokens=5,
                                    total_tokens=20,
                                ),
                            ),
                        ]
                    ),
                    # 第 2 轮无工具调用，内容即最终回复，不再重复生成
                    _stream(
                        [
                            StreamChunk(content="结果如下", usage=None),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=10,
                                    completion_tokens=2,
                                    total_tokens=12,
                                ),
                            ),
                        ]
                    ),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="latex 公式怎么写",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        # 验证事件流包含 tool_status → tool_result（含 search_results）→ token → done
        types = [e["type"] for e in events]
        assert "tool_status" in types
        assert "tool_result" in types
        assert "token" in types
        assert types[-1] == "done"
        # 无工具调用的一轮即最终回复，不应重复生成
        client.chat_stream_with_usage.assert_not_called()

        # 验证 tool_result 事件结构
        tool_event = next(e for e in events if e["type"] == "tool_result")
        assert tool_event["tool"] == "web_search"
        assert tool_event["result_count"] == 2
        assert "search_results" in tool_event
        assert len(tool_event["search_results"]) == 2
        assert tool_event["query"] == "latex 公式写法"

        # 验证每条搜索结果的字段
        first = tool_event["search_results"][0]
        assert first["index"] == 1
        assert first["title"] == "LaTeX 入门教程"
        assert first["snippet"] == "LaTeX 是一个高质量的排版系统。"
        assert first["url"] == "https://example.com/latex-intro"

        # 验证 tool_messages 中的 content 仍为 format_for_llm 格式化文本
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        assert ctx is not None
        # web_search 结果随 assistant 消息落盘
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert len(final_msgs) == 1
        assert final_msgs[0].content == "结果如下"
        assert len(final_msgs[0].search_results) == 2
        assert final_msgs[0].search_results[1].url == "https://overleaf.com"
        assert final_msgs[0].search_query == "latex 公式写法"

    def test_legacy_chat_message_loads_without_evidence_fields(self):
        """旧会话 JSON 不含阶段 5 字段时应自动使用空证据集合。"""
        message = ChatMessage.model_validate(
            {"id": "legacy", "role": "assistant", "content": "旧回复"}
        )
        assert message.evidence_sources == []
        assert message.page_reads == []
        assert message.cited_evidence_ids == []

    def test_evidence_citation_validation_accepts_only_known_ids(self):
        cited, unverified = WritingChatService._validate_evidence_citations(
            "事实 [EVIDENCE doc#chunk-1] [EVIDENCE forged]",
            {"doc#chunk-1"},
        )
        assert cited == ["doc#chunk-1", "forged"]
        assert unverified == ["forged"]

    @pytest.mark.asyncio
    async def test_send_web_search_no_results(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """搜索无结果时，tool_result 不应包含 search_results 字段。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")

        from backend.utils.web_search import SearchResponse

        empty_resp = SearchResponse(
            query="不存在的内容", results=[], engine="duckduckgo"
        )

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=empty_resp)

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(
                        [
                            StreamChunk(
                                content=None,
                                usage=None,
                                tool_calls=[_make_tool_call(query="不存在的内容")],
                            ),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=5,
                                    completion_tokens=1,
                                    total_tokens=6,
                                ),
                            ),
                        ]
                    ),
                    # 第 2 轮无工具调用，内容即最终回复
                    _stream(
                        [
                            StreamChunk(content="无结果", usage=None),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=5,
                                    completion_tokens=1,
                                    total_tokens=6,
                                ),
                            ),
                        ]
                    ),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="不存在的内容",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        tool_event = next(e for e in events if e["type"] == "tool_result")
        assert tool_event["result_count"] == 0
        # search_results 字段存在但为空列表，前端靠 .length > 0 过滤
        assert tool_event["search_results"] == []
        # 无结果时落盘的 search_results 也为空，刷新后不显示搜索卡片
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert final_msgs[0].content == "无结果"
        assert final_msgs[0].search_results == []

    @pytest.mark.asyncio
    async def test_send_citation_to_result_beyond_display_limit_is_verified(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        """引用第 6+ 条搜索结果不应被误报为未验证来源。"""
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")

        from backend.utils.web_search import SearchResponse, SearchResult

        results = [
            SearchResult(
                title=f"来源 {i}",
                snippet=f"摘要 {i}",
                url=f"https://example.com/{i}",
            )
            for i in range(1, 11)
        ]
        search_resp = SearchResponse(
            query="q", results=results, engine="duckduckgo", total_results=10
        )

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=search_resp)

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(
                        [
                            StreamChunk(
                                content=None,
                                usage=None,
                                tool_calls=[_make_tool_call(query="q")],
                            ),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=1,
                                    completion_tokens=1,
                                    total_tokens=2,
                                ),
                            ),
                        ]
                    ),
                    _stream(
                        [
                            StreamChunk(
                                content="参考 [来源](https://example.com/10)",
                                usage=None,
                            ),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=1,
                                    completion_tokens=1,
                                    total_tokens=2,
                                ),
                            ),
                        ]
                    ),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client

            req = ChatSendRequest(
                file_path="main.tex",
                user_message="q",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        types = [e["type"] for e in events]
        assert "citation_note" not in types
        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert final_msgs[0].unverified_citations == []


class TestToolEvidenceBudgetPersistence:
    """工具证据裁剪后，最新证据保留且当轮消息完整持久化。"""

    @pytest.mark.asyncio
    async def test_send_keeps_latest_evidence_and_persists_after_trim(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")

        # 历史会话带一条 30k 字符的工具结果对，作为最旧证据
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("旧问题")
        old_asst = ctx.append_assistant_message(content="", tokens=0)
        old_asst.tool_calls = [
            {
                "id": "hist",
                "type": "function",
                "function": {"name": "web_search", "arguments": "{}"},
            }
        ]
        ctx.append_tool_messages(
            [
                {"role": "assistant", "tool_calls": old_asst.tool_calls},
                {"role": "tool", "tool_call_id": "hist", "content": "H" * 30000},
            ]
        )
        ctx.append_assistant_message(content="旧回复", tokens=0)
        ctx.save_to_disk()

        from backend.utils.web_search import SearchResponse, SearchResult

        big_resp = SearchResponse(
            query="q",
            results=[
                SearchResult(
                    title="大页面",
                    snippet="s",
                    url="https://example.com/big",
                    full_content="Z" * 20000,
                )
            ],
            engine="duckduckgo",
            total_results=1,
        )
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=big_resp)

        def _tool_round(call_id: str) -> list:
            return [
                StreamChunk(content="继续检索", usage=None),
                StreamChunk(
                    content=None,
                    usage=None,
                    tool_calls=[_make_tool_call(call_id=call_id)],
                ),
                StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=1, completion_tokens=1, total_tokens=2
                    ),
                ),
            ]

        final_round_chunks = [
            StreamChunk(content="最终答复", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            ),
        ]

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(_tool_round("cur1")),
                    _stream(_tool_round("cur2")),
                    _stream(final_round_chunks),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client

            req = ChatSendRequest(
                session_id=ctx.session_id,
                file_path="main.tex",
                user_message="新问题",
                web_search_enabled=True,
            )
            events = [e async for e in service.send(req)]

        ctx = ConversationContext.load_from_disk(events[-1]["session_id"])
        assert ctx is not None
        final_msgs = [m for m in ctx.messages if m.role == "assistant" and m.content]
        assert final_msgs[-1].content == "最终答复"
        # 历史大结果对触发裁剪（只影响本轮 LLM 上下文），
        # 历史消息仍保留在会话中，且本轮两轮工具消息必须完整持久化
        tool_msgs = [m for m in ctx.messages if m.role == "tool"]
        assert len(tool_msgs) == 3
        assert {"cur1", "cur2"}.issubset({m.tool_call_id for m in tool_msgs})


class TestScholarlyResearchToolEvent:
    """验证学术检索工具 schema 与前端摘要事件。"""

    def test_enabled_tools_include_scholarly_research(self, service, monkeypatch):
        research_cfg = get_config().features.function_calling.scholarly_research
        monkeypatch.setattr(research_cfg, "enabled", True)
        mock_manager = MagicMock()
        mock_manager.get_tools_schema.return_value = [
            {"function": {"name": name}}
            for name in (
                "search_scholarly_works",
                "get_work_details",
                "find_related_works",
            )
        ]
        monkeypatch.setattr(
            "backend.utils.scholarly_research.get_research_manager",
            lambda: mock_manager,
        )

        names = [
            schema["function"]["name"]
            for schema in service._get_enabled_tools(workspace_id="")
        ]

        assert "search_scholarly_works" in names
        assert "get_work_details" in names
        assert "find_related_works" in names

    @pytest.mark.asyncio
    async def test_send_emits_scholarly_results(
        self, temp_session_dir, open_project, service, monkeypatch
    ):
        import json

        from backend.utils.scholarly_research.models import (
            Author,
            ScholarlyWork,
            SearchScholarlyResult,
        )

        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        monkeypatch.setattr(
            get_config().features.function_calling.scholarly_research,
            "enabled",
            True,
        )
        scholarly_response = SearchScholarlyResult(
            query="retrieval augmented generation",
            results=[
                ScholarlyWork(
                    openalex_id="W123",
                    doi="10.1000/rag",
                    title="Retrieval-Augmented Generation",
                    authors=[Author(name="Ada Lovelace")],
                    year=2025,
                    journal="Journal of Retrieval",
                    work_type="journal_article",
                    citation_count=12,
                    is_oa=True,
                    oa_url="https://example.test/rag.pdf",
                )
            ],
            total_estimated=37,
        )
        tool_call = _make_tool_call()
        tool_call.function.name = "search_scholarly_works"
        tool_call.function.arguments = json.dumps(
            {"query": "retrieval augmented generation"}
        )

        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value=scholarly_response)
        chunks = [
            StreamChunk(content="结果如下", usage=None),
            StreamChunk(
                content=None,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            ),
        ]

        with patch(_LLM_CLIENT_PATH) as m_llm, patch(
            _TOOL_REGISTRY_PATH, return_value=mock_registry
        ):
            client = MagicMock()
            client.chat_stream_with_tools = MagicMock(
                side_effect=[
                    _stream(
                        [
                            StreamChunk(
                                content=None,
                                usage=None,
                                tool_calls=[tool_call],
                            ),
                            StreamChunk(
                                content=None,
                                usage=UsageInfo(
                                    prompt_tokens=15,
                                    completion_tokens=5,
                                    total_tokens=20,
                                ),
                            ),
                        ]
                    ),
                    # 第 2 轮无工具调用，内容即最终回复
                    _stream(chunks),
                ]
            )
            client.chat_stream_with_usage = MagicMock()
            m_llm.return_value = client
            events = [
                event
                async for event in service.send(
                    ChatSendRequest(
                        file_path="main.tex",
                        user_message="检索 RAG 论文",
                    )
                )
            ]

        tool_event = next(event for event in events if event["type"] == "tool_result")
        assert tool_event["total_estimated"] == 37
        assert tool_event["query"] == "retrieval augmented generation"
        assert tool_event["scholarly_results"] == [
            {
                "id": "W123",
                "title": "Retrieval-Augmented Generation",
                "authors": ["Ada Lovelace"],
                "year": 2025,
                "journal": "Journal of Retrieval",
                "citation_count": 12,
                "doi": "10.1000/rag",
                "is_oa": True,
                "oa_url": "https://example.test/rag.pdf",
                "work_type": "journal_article",
            }
        ]
        context = ConversationContext.load_from_disk(events[-1]["session_id"])
        assert context is not None
        assistant = next(
            message
            for message in reversed(context.messages)
            if message.role == "assistant" and message.content == "结果如下"
        )
        assert assistant.scholarly_results[0].id == "W123"
        assert assistant.scholarly_total == 37

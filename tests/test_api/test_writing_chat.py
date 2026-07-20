"""
写作助手 Chat 模块集成测试。

覆盖：
    - Pydantic 模型校验（ChatMessage / EditOp / LlmEditResult）
    - ConversationContext：创建/追加/删除/撤回/压缩
    - WritingChatService.send（mock chat_stream_with_usage）：token 累计、压缩触发、user 删除回滚
    - WritingChatService.generate_edit（mock chat）：marker 一致性、还原、附挂
    - WritingChatService.apply_edit：写回 .tex
    - 删除/撤回消息在父子组件中保持 token 计数一致
    - 会话磁盘持久化往返
"""

import time
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

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
    UndoMessageRequest,
    ChatSendRequest,
)
from backend.modules.writing_chat.service import WritingChatService


_LLM_CLIENT_PATH = "backend.modules.writing_chat.service.get_llm_client"


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
        r"\section{Introduction}" + "\n"
        + "\n"
        + r"This method is not very good for the purpose." + "\n"
        + r"It has many weaknesses in practical application." + "\n"
        + "\n"
        + r"\section{Method}" + "\n"
        + r"\textbf{Bold claim} verified by $x^2$." + "\n"
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

    def test_edit_op_defaults(self):
        op = EditOp(
            file_path="main.tex",
            paragraph_index=0,
            original_latex="a",
            new_latex="b",
        )
        assert op.applied is False
        assert op.diff_text == ""

    def test_llm_edit_result_required_fields(self):
        from pydantic import ValidationError
        from backend.modules.writing_chat.models import LlmEditResult

        with pytest.raises(ValidationError):
            LlmEditResult.model_validate({"paragraph_index": 0})


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

    def test_delete_message_reduces_tokens(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        u = ctx.append_user_message("q")
        u.tokens = 50
        ctx.append_assistant_message("a", tokens=20)
        # 手动覆盖以模拟压缩后场景
        ctx.cumulative_tokens = 50 + 20
        ctx.save_to_disk()

        ok = ctx.delete_message(ctx.messages[0].id)
        assert ok
        assert ctx.cumulative_tokens == 20
        assert len(ctx.messages) == 1

    def test_undo_summary_restores_snapshot(self, temp_session_dir, open_project):
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

        result = ctx.undo_message(ctx.messages[0].id)
        assert result["restored"] is True
        assert result["restored_count"] == 4
        assert len(ctx.messages) == 4
        assert ctx.messages[0].content == "hello"

    def test_undo_regular_message_equivalent_to_delete(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("q")
        ctx.append_assistant_message("a", tokens=20)
        assert ctx.cumulative_tokens == 20
        result = ctx.undo_message(ctx.messages[1].id)
        assert result["restored"] is False
        assert result["removed"] is True
        assert ctx.cumulative_tokens == 0

    def test_should_compress_threshold(self, temp_session_dir, open_project):
        ctx = ConversationContext.create_session(scope="test")
        ctx.cumulative_tokens = 10
        assert not ctx.should_compress()
        ctx.cumulative_tokens = 10_000_000
        assert ctx.should_compress()


# ── Service.send tests (mock chat_stream_with_usage) ──────────────────────


def _stream(chunks: list[StreamChunk]):
    async def gen() -> AsyncIterator[StreamChunk]:
        for c in chunks:
            yield c
    return gen()


class TestServiceSend:
    @pytest.mark.asyncio
    async def test_send_streams_tokens_and_persists(self, temp_session_dir, open_project, service, monkeypatch):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        chunks = [
            StreamChunk(content="Hello", usage=None),
            StreamChunk(content=" world", usage=None),
            StreamChunk(content=None, usage=UsageInfo(prompt_tokens=12, completion_tokens=3, total_tokens=15)),
        ]
        with patch(_LLM_CLIENT_PATH) as m_llm:
            m_client = MagicMock()
            m_client.chat_stream_with_usage = MagicMock(return_value=_stream(chunks))
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
    async def test_send_triggers_compression(self, temp_session_dir, open_project, service, monkeypatch):
        monkeypatch.setattr(get_config().llm, "api_key", "test-key")
        # 先准备一条已超阈值的会话（含多条历史消息以便通过压缩最小消息数阈值）
        cfg = get_config().features.writing_chat
        monkeypatch.setattr(cfg, "compression_threshold_tokens", 100)

        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        for i in range(4):
            ctx.append_user_message(f"q{i}")
            ctx.append_assistant_message(f"a{i}", tokens=20)
        ctx.cumulative_tokens = 500  # 模拟已超阈值
        ctx.save_to_disk()

        # mock 压缩 LLM 返回 summary
        summary_response = {"summary": "压缩摘要内容"}

        async def fake_chat(messages, **kwargs):
            return summary_response

        chunks = [
            StreamChunk(content="reply", usage=None),
            StreamChunk(content=None, usage=UsageInfo(prompt_tokens=10, completion_tokens=2, total_tokens=12)),
        ]
        with patch(_LLM_CLIENT_PATH) as m_llm:
            client = MagicMock()
            client.chat = fake_chat
            client.chat_stream_with_usage = MagicMock(return_value=_stream(chunks))
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
        # 压缩后应至少存在 1 条 summary 消息 + 本轮 user + assistant
        summary_msgs = [m for m in loaded.messages if m.role == "summary" and m.compressed]
        assert len(summary_msgs) == 1
        assert summary_msgs[0].snapshot is not None
        # 压缩前有 4 组 user/assistant 消息 (8) + 本轮新追加的 user 消息 = 9 条
        assert len(summary_msgs[0].snapshot) == 9

    @pytest.mark.asyncio
    async def test_send_no_api_key_emits_error(self, temp_session_dir, open_project, service, monkeypatch):
        cfg = get_config()
        monkeypatch.setattr(cfg.llm, "api_key", "")
        req = ChatSendRequest(file_path="main.tex", user_message="x")
        events = [e async for e in service.send(req)]
        assert any(e.get("type") == "error" for e in events)


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

        # mock LLM：输出改写了 plain 文本但保留了 LaTeX 结构的 raw LaTeX
        new_latex = para.text.replace("verified", "verified (empirically)")
        async def fake_chat(messages, **kwargs):
            return {
                "paragraph_index": para.index,
                "original": para.text,
                "optimized": new_latex,
                "reason": "more precise",
            }
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
        op = EditOp.model_validate(result["edit_op"])
        assert op.applied is False
        # LLM 输出直接用作 new_latex，保留了原始 LaTeX 结构
        assert "empirically" in op.new_latex
        assert r"\textbf" in op.new_latex
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        attached = next(
            m for m in loaded.messages if m.id == result["attached_message_id"]
        )
        assert attached.role == "assistant"
        assert attached.edit_op is not None
        assert attached.edit_op.applied is False

    def test_apply_edit_writes_back_to_file(
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
        result = service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "not very good" not in content
        assert "suboptimal" in content

        # 持久化中 edit_op.applied 应为 True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        target = next(m for m in loaded.messages if m.id == am.id)
        assert target.edit_op.applied is True

    def test_apply_edit_already_applied_returns_idempotent(
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
        result = service.apply_edit(req)
        assert result == {"applied": True, "already_applied": True}

    def test_apply_edit_with_custom_latex_uses_custom_value(
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
        result = service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        # 应写入用户手动编辑的内容，而非 LLM 建议的内容
        assert "not very good" not in content
        assert "unsatisfactory" in content
        assert "suboptimal" not in content

    def test_apply_edit_without_custom_latex_falls_back_to_edit_op(
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
        result = service.apply_edit(req, workspace_id="test-ws")
        assert result["applied"] is True

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        # 应写入 LLM 建议的内容
        assert "not very good" not in content
        assert "suboptimal" in content


# ── Delete / undo message via service ─────────────────────────────────────


class TestServiceMessageOps:
    def test_delete_message(self, temp_session_dir, open_project, service):
        ctx = ConversationContext.create_session(scope="test")
        u = ctx.append_user_message("q")
        ctx.append_assistant_message("a", tokens=10)
        ctx.save_to_disk()

        ok = service.delete_message(
            DeleteMessageRequest(session_id=ctx.session_id, message_id=u.id)
        )
        assert ok is True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert len(loaded.messages) == 1
        # user 消息 tokens=0，删除后 cumulative_tokens 不变
        assert loaded.cumulative_tokens == 10

    def test_undo_summary_message(self, temp_session_dir, open_project, service):
        ctx = ConversationContext.create_session(scope="test")
        ctx.append_user_message("q1")
        ctx.append_assistant_message("a1", tokens=20)
        ctx.replace_with_summary("sum", summary_tokens=5)
        ctx.save_to_disk()
        summary_id = ctx.messages[0].id

        result = service.undo_message(
            UndoMessageRequest(session_id=ctx.session_id, message_id=summary_id)
        )
        assert result["restored"] is True
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert len(loaded.messages) == 2
        # snapshot 还原后 user(0) + assistant(20)，累计 = 20
        assert loaded.cumulative_tokens == 20
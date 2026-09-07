"""writing_chat 会话 id 校验（防止目录穿越）。"""

import json

import pytest

from backend.core.config import get_config
from backend.modules.writing_chat.context import (
    ConversationContext,
    parse_scoped_session_id,
)


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


INVALID_SESSION_IDS = [
    "scope:../evil",
    "scope:..\\evil",
    "scope:evil/name",
    "scope:evil\\name",
    "scope:..",
    "..:id",
    "scope:",
    ":id",
    "scope:name.txt",
    "scope:C:/evil",
    "",
    None,
]


class TestParseScopedSessionId:
    def test_valid_ids(self):
        scope, id_part = parse_scoped_session_id(
            "_noproject:0123456789abcdef0123456789abcdef"
        )
        assert scope == "_noproject"
        assert id_part == "0123456789abcdef0123456789abcdef"
        scope, id_part = parse_scoped_session_id("test:abc123")
        assert scope == "test"
        assert id_part == "abc123"

    @pytest.mark.parametrize("session_id", INVALID_SESSION_IDS)
    def test_invalid_ids_rejected(self, session_id):
        with pytest.raises(ValueError):
            parse_scoped_session_id(session_id)


class TestSessionPathIsolation:
    def test_traversal_load_returns_none(self, temp_session_dir):
        # 在会话目录之外放一个伪造会话文件，穿越 id 不应读到它
        evil = temp_session_dir.parent / "evil.json"
        evil.write_text(
            json.dumps(
                {
                    "id": "scope:../evil",
                    "messages": [],
                    "cumulative_tokens": 0,
                }
            ),
            encoding="utf-8",
        )
        assert ConversationContext.load_from_disk("scope:..\\..\\evil") is None
        assert evil.exists()

    def test_traversal_save_rejected(self, temp_session_dir):
        ctx = ConversationContext(
            session_id="scope:..\\..\\evil",
            created_at=0.0,
            last_updated_at=0.0,
            messages=[],
            cumulative_tokens=0,
            file_path=None,
        )
        with pytest.raises(ValueError):
            ctx.save_to_disk()
        assert not (temp_session_dir.parent / "evil.json").exists()

    def test_traversal_delete_returns_false(self, temp_session_dir):
        evil = temp_session_dir.parent / "evil.json"
        evil.write_text("{}", encoding="utf-8")
        ctx = ConversationContext.load_from_disk("scope:..\\..\\evil")
        assert ctx is None
        assert evil.exists()

    def test_valid_session_roundtrip(self, temp_session_dir):
        ctx = ConversationContext.create_session(file_path="main.tex", scope="test")
        ctx.append_user_message("hi")
        ctx.save_to_disk()
        loaded = ConversationContext.load_from_disk(ctx.session_id)
        assert loaded is not None
        assert loaded.session_id == ctx.session_id
        assert len(loaded.messages) == 1

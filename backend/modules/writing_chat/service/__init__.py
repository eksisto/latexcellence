"""写作助手业务逻辑（service 包，对外导入路径保持不变）。"""

from .compaction import CompactionMixin
from .conversation import ConversationMixin
from .editor_chat import EditorChatMixin
from .edits import EditMixin
from .sessions import SessionMixin
from .state import StateMixin


class WritingChatService(
    ConversationMixin,
    EditMixin,
    CompactionMixin,
    EditorChatMixin,
    SessionMixin,
    StateMixin,
):
    """写作助手服务（单例）。"""


_service: WritingChatService | None = None


def get_writing_chat_service() -> WritingChatService:
    global _service
    if _service is None:
        _service = WritingChatService()
    return _service


__all__ = ["WritingChatService", "get_writing_chat_service"]

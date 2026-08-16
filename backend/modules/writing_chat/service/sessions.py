"""写作助手服务：会话 CRUD 与手动压缩。"""

from typing import Any

from backend.modules.writing_chat.context import ConversationContext


class SessionMixin:
    """会话持久化管理。"""

    def list_sessions(self, workspace_id: str = "") -> list[dict[str, Any]]:
        scope = self._get_scope(workspace_id) if workspace_id else ""
        return ConversationContext.list_sessions(scope)

    def load_session(self, session_id: str) -> ConversationContext | None:
        return ConversationContext.load_from_disk(session_id)

    async def delete_session(self, session_id: str) -> bool:
        async with self._session_lock(session_id):
            ctx = ConversationContext.load_from_disk(session_id)
            if ctx is None:
                return False
            result = ctx.delete_from_disk()
            self._session_locks.pop(session_id, None)
            return result

    async def rename_session(self, session_id: str, title: str) -> dict:
        async with self._session_lock(session_id):
            ctx = ConversationContext.load_from_disk(session_id)
            if ctx is None:
                raise ValueError(f"Session not found: {session_id}")
            ctx.rename(title)
            return {"status": "ok", "title": ctx.title}

    async def compact_session(self, session_id: str) -> dict:
        """
        手动压缩会话（复用自动压缩逻辑，不追加 user 消息）。

        消息过少或压缩失败时返回 compressed=False，不改变会话结构。
        """
        async with self._session_lock(session_id):
            ctx = ConversationContext.load_from_disk(session_id)
            if ctx is None:
                raise ValueError(f"Session not found: {session_id}")
            compressed = await self._maybe_compress(ctx)
            ctx.save_to_disk()
            return {
                "status": "ok",
                "compressed": compressed,
                "session_id": ctx.session_id,
                "message_count": len(ctx.messages),
                "cumulative_tokens": ctx.cumulative_tokens,
            }

    # ── 发送消息（RS1，SSE 流式） ────────────────────────────────────────

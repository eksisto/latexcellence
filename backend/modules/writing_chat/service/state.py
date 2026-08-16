"""写作助手服务状态：会话锁、工具确认与澄清请求。"""

import asyncio
import time
from typing import Any

from backend.core.logger import get_logger


class StateMixin:
    """会话级状态与用户交互等待。"""

    def __init__(self):
        self._logger = get_logger("writing_chat.service")
        # 会话级写锁：同一 session 的 send / 编辑 / 压缩等操作互斥，
        # 防止双标签页并发 save_to_disk 最后写者胜导致丢消息。
        self._session_locks: dict[str, asyncio.Lock] = {}
        # 待用户确认的工具调用（写操作：edit_paragraph / undo_last_edit）
        # key: pending_id, value: {event: asyncio.Event, confirmed: bool | None, tool_name: str, ...}
        self._pending_confirms: dict[str, dict[str, Any]] = {}
        # 待用户回复的澄清请求（request_clarification）
        # key: clarify_id, value: {event: asyncio.Event, answered: bool, answer: str, created_at: float}
        self._pending_clarifications: dict[str, dict[str, Any]] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """获取指定会话的串行化锁（会话删除时由 delete_session 清理）。"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def _request_tool_confirmation(
        self,
        pending_id: str,
        tool_name: str,
        tool_args: dict,
    ) -> dict | None:
        """
        发出工具确认请求，等待前端用户确认。

        返回：
            - None: 超时或被拒绝
            - dict: 确认后的参数（可能已被前端修改）
        """
        self._cleanup_stale_pending()
        event = asyncio.Event()
        self._pending_confirms[pending_id] = {
            "event": event,
            "confirmed": None,
            "tool_name": tool_name,
            "tool_args": dict(tool_args),
            "created_at": time.time(),
        }
        try:
            await asyncio.wait_for(event.wait(), timeout=120)
        except asyncio.TimeoutError:
            self._pending_confirms.pop(pending_id, None)
            return None

        pending = self._pending_confirms.pop(pending_id, {})
        if not pending.get("confirmed"):
            return None
        return pending.get("tool_args", tool_args)

    def _cleanup_stale_pending(self, max_age: float = 120) -> None:
        """清理超过 TTL 的待确认请求。"""
        now = time.time()
        stale = [
            pid
            for pid, p in self._pending_confirms.items()
            if now - p.get("created_at", 0) > max_age
        ]
        for pid in stale:
            p = self._pending_confirms.pop(pid, None)
            if p and not p.get("event").is_set():
                p["confirmed"] = False
                p["event"].set()

    def confirm_pending_tool(
        self, pending_id: str, modified_args: dict | None = None
    ) -> bool:
        """确认待定工具调用。"""
        pending = self._pending_confirms.get(pending_id)
        if not pending or pending["event"].is_set():
            return False
        if modified_args:
            pending["tool_args"] = modified_args
        pending["confirmed"] = True
        pending["event"].set()
        return True

    def cancel_pending_tool(self, pending_id: str) -> bool:
        """取消待定工具调用。"""
        pending = self._pending_confirms.get(pending_id)
        if not pending or pending["event"].is_set():
            return False
        pending["confirmed"] = False
        pending["event"].set()
        return True

    # ── 澄清请求（request_clarification） ─────────────────────────────

    async def _request_clarification(
        self,
        clarify_id: str,
    ) -> str:
        """
        发出澄清请求，等待前端用户回复。

        返回：
            - str: 用户的回复内容。超时或取消时返回提示文本。
        """
        self._cleanup_stale_clarifications()
        event = asyncio.Event()
        self._pending_clarifications[clarify_id] = {
            "event": event,
            "answered": False,
            "answer": "",
            "created_at": time.time(),
        }
        try:
            await asyncio.wait_for(event.wait(), timeout=120)
        except asyncio.TimeoutError:
            self._pending_clarifications.pop(clarify_id, None)
            return "（用户未在时限内回复，请根据已有信息自行判断并继续）"

        pending = self._pending_clarifications.pop(clarify_id, {})
        if not pending.get("answered"):
            return "（用户已取消此澄清请求，请根据已有信息自行判断并继续）"
        return pending.get("answer", "")

    def submit_clarification(self, clarify_id: str, answer: str) -> bool:
        """提交澄清请求的回复。"""
        pending = self._pending_clarifications.get(clarify_id)
        if not pending or pending["event"].is_set():
            return False
        pending["answer"] = answer
        pending["answered"] = True
        pending["event"].set()
        return True

    def cancel_clarification(self, clarify_id: str) -> bool:
        """取消待定的澄清请求。"""
        pending = self._pending_clarifications.get(clarify_id)
        if not pending or pending["event"].is_set():
            return False
        pending["answered"] = False
        pending["event"].set()
        return True

    def _cleanup_stale_clarifications(self, max_age: float = 120) -> None:
        """清理超过 TTL 的待定澄清请求。"""
        now = time.time()
        stale = [
            cid
            for cid, c in self._pending_clarifications.items()
            if now - c.get("created_at", 0) > max_age
        ]
        for cid in stale:
            c = self._pending_clarifications.pop(cid, None)
            if c and not c.get("event").is_set():
                c["answered"] = False
                c["event"].set()

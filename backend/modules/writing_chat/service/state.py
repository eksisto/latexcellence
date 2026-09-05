"""写作助手服务状态：会话锁、工具确认与澄清请求。

用户交互等待的双模式实现（v2 P7 多实例收口）：

- 单用户模式（默认）：进程内 dict + asyncio.Event，行为与历史版本完全一致；
- 多用户模式：pending_interactions 表 + 短间隔轮询（``PENDING_POLL_INTERVAL_
  SECONDS``）替代 Event——发起（SSE 等待侧）与应答（confirm-tool / clarify
  HTTP 请求）可落在不同副本，跨实例可续。

会话级写锁 ``_session_locks`` 保持进程内 asyncio.Lock：同一副本内互斥；
跨副本的写冲突由会话文件的原子写与最后写入者语义兜底（方案 §12.1 未将
其列入外置清单）。
"""

import asyncio
import time
from typing import Any

from backend.core.logger import get_logger
from backend.core.multiuser import pending_interactions


class StateMixin:
    """会话级状态与用户交互等待。"""

    # 待确认交互的统一 TTL（秒）：DB 行 expires_at 与单用户 Event 等待
    # 超时共用；超时 / 过期一律等同用户拒绝（调用方注入取消语义 tool
    # result，模型侧永不悬空）
    _TOOL_CONFIRM_TTL_SECONDS = 120

    def __init__(self):
        self._logger = get_logger("writing_chat.service")
        # 会话级写锁：同一 session 的 send / 编辑 / 压缩等操作互斥，
        # 防止双标签页并发 save_to_disk 最后写者胜导致丢消息。
        self._session_locks: dict[str, asyncio.Lock] = {}
        # 单用户模式的待定交互存储（多用户模式走 pending_interactions 表）
        # key: pending_id, value: {event: asyncio.Event, confirmed: bool | None, tool_name: str, ...}
        self._pending_confirms: dict[str, dict[str, Any]] = {}
        # key: clarify_id, value: {event: asyncio.Event, answered: bool, answer: str, created_at: float}
        self._pending_clarifications: dict[str, dict[str, Any]] = {}

    def _db_pending_available(self) -> bool:
        """pending_interactions 表是否可用（多用户模式且库已初始化）。"""
        return pending_interactions.available()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """获取指定会话的串行化锁（会话删除时由 delete_session 清理）。"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    # ── 工具调用确认 ─────────────────────────────────────────────────

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
        await self._register_tool_confirmation(pending_id, tool_name, tool_args)
        return await self._await_tool_confirmation(pending_id, tool_name, tool_args)

    async def _register_tool_confirmation(
        self,
        pending_id: str,
        tool_name: str,
        tool_args: dict,
    ) -> None:
        """登记待确认交互（不等待决议）。

        编辑器确认流（阶段五b）要求"先登记后 yield"：``tool_pending``
        事件携带的 pending_id 一旦到达前端，用户可能在亚秒内点击确认，
        若登记发生在 yield 之后（Writing Chat 主对话的既有次序），
        confirm 端点将查无此 pending 而误报 404。拆为登记 / 等待两阶段
        后，调用方可先登记、再推事件、后等待；Writing Chat 的
        ``_request_tool_confirmation`` 组合两者，行为与历史版本一致。
        """
        if self._db_pending_available():
            await asyncio.to_thread(
                pending_interactions.create,
                pending_id,
                pending_interactions.KIND_CONFIRM,
                {"tool_name": tool_name, "tool_args": dict(tool_args)},
                self._TOOL_CONFIRM_TTL_SECONDS,
            )
            return
        self._cleanup_stale_pending()
        self._pending_confirms[pending_id] = {
            "event": asyncio.Event(),
            "confirmed": None,
            "tool_name": tool_name,
            "tool_args": dict(tool_args),
            "created_at": time.time(),
        }

    async def _await_tool_confirmation(
        self,
        pending_id: str,
        tool_name: str,
        tool_args: dict,
    ) -> dict | None:
        """等待已登记（:meth:`_register_tool_confirmation`）的待确认交互决议。

        返回：
            - None: 取消 / 等待超时 / pending 行过期（三者同义）
            - dict: 确认后的参数（可能已被前端修改）
        """
        if self._db_pending_available():
            resolved = await self._poll_db_pending(
                pending_id,
                pending_interactions.KIND_CONFIRM,
                self._TOOL_CONFIRM_TTL_SECONDS,
            )
            if resolved is not None and resolved.get("status") == "confirmed":
                args = resolved.get("tool_args")
                return args if isinstance(args, dict) else tool_args
            return None
        entry = self._pending_confirms.get(pending_id)
        if entry is None:
            return None
        try:
            await asyncio.wait_for(
                entry["event"].wait(), timeout=self._TOOL_CONFIRM_TTL_SECONDS
            )
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
        if self._db_pending_available():
            update = {"tool_args": modified_args} if modified_args else None
            return pending_interactions.try_resolve(
                pending_id, pending_interactions.KIND_CONFIRM, "confirmed", update
            )
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
        if self._db_pending_available():
            return pending_interactions.try_resolve(
                pending_id, pending_interactions.KIND_CONFIRM, "cancelled"
            )
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
        if self._db_pending_available():
            resolved = await self._wait_db_pending(
                pending_interactions.KIND_CLARIFY,
                clarify_id,
                {},
                timeout=120,
            )
            if resolved is not None and resolved.get("status") == "answered":
                return resolved.get("answer", "")
            if resolved is not None and resolved.get("status") == "cancelled":
                return "（用户已取消此澄清请求，请根据已有信息自行判断并继续）"
            return "（用户未在时限内回复，请根据已有信息自行判断并继续）"
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
        if self._db_pending_available():
            return pending_interactions.try_resolve(
                clarify_id,
                pending_interactions.KIND_CLARIFY,
                "answered",
                {"answer": answer},
            )
        pending = self._pending_clarifications.get(clarify_id)
        if not pending or pending["event"].is_set():
            return False
        pending["answer"] = answer
        pending["answered"] = True
        pending["event"].set()
        return True

    def cancel_clarification(self, clarify_id: str) -> bool:
        """取消待定的澄清请求。"""
        if self._db_pending_available():
            return pending_interactions.try_resolve(
                clarify_id, pending_interactions.KIND_CLARIFY, "cancelled"
            )
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

    # ── 多用户模式：DB 待定交互轮询等待 ───────────────────────────────

    async def _wait_db_pending(
        self,
        kind: str,
        interaction_id: str,
        payload: dict,
        timeout: float,
    ) -> dict | None:
        """登记待定交互并轮询直至决议/超时。

        返回决议 payload dict（含 status）；超时返回 None。轮询间隔取
        ``PENDING_POLL_INTERVAL_SECONDS``（人机交互亚秒延迟无感，避免
        Event 无法跨进程的缺陷），查询为单行主键读，开销可忽略。
        """
        await asyncio.to_thread(
            pending_interactions.create,
            interaction_id,
            kind,
            payload,
            timeout,
        )
        return await self._poll_db_pending(interaction_id, kind, timeout)

    async def _poll_db_pending(
        self,
        interaction_id: str,
        kind: str,
        timeout: float,
    ) -> dict | None:
        """轮询已登记（调用方负责 create）的待定交互直至决议/超时。

        与 :meth:`_wait_db_pending` 的等待语义一致；拆出供"先登记后
        yield"的两阶段调用方（编辑器确认流）复用。过期行按已决议处理
        （fetch_resolved 返回 status=expired），与等待超时同义。
        """
        deadline = time.monotonic() + timeout
        poll = pending_interactions.PENDING_POLL_INTERVAL_SECONDS
        while True:
            await asyncio.sleep(poll)
            resolved = await asyncio.to_thread(
                pending_interactions.fetch_resolved, interaction_id, kind
            )
            if resolved is not None:
                if resolved.get("status") == "expired":
                    return None  # 与等待超时同义
                return resolved
            if time.monotonic() >= deadline:
                await asyncio.to_thread(
                    pending_interactions.delete, interaction_id, kind
                )
                return None

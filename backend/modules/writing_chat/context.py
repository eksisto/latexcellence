"""
写作助手 Chat 的会话上下文管理器。

职责：
    1. 在磁盘上持久化每个会话（一个 session_id 一个 JSON 文件，复用 atomic_write）
    2. 维护会话中的 messages 列表与累计 token 计数（cumulative_tokens）
    3. 提供 to_llm_messages()：将本会话转为发送给 LLM 的 messages 数组
    4. 触发上下文压缩：累计 token 超阈值时把旧消息打包给 LLM 生成摘要并替换
    5. 支持删除单条消息，以及撤回消息（若是 summary 消息则还原 snapshot）
    6. 加载文件段落作为上下文锚点（发送原始 LaTeX 源码，不用 marker）

设计约定：
    - 会话文件存于 config.features.writing_chat.sessions_dir
    - 文件名：{session_id}.json
    - 不在 __init__ 中缓存配置；方法内实时 get_config()（AGENTS.md）
    - 使用 pathlib 与 encoding="utf-8"（AGENTS.md）
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.file_ops import atomic_write, safe_read
from backend.modules.writing_chat.models import (
    ChatMessage,
    LlmSummaryResult,
)


class ConversationContext:
    """
    单次会话的上下文。

    用法：
        ctx = ConversationContext.create_session(file_path="main.tex")
        ctx.append_user_message("帮我分析第三段")
        ctx.append_assistant_message("...")
        ctx.save_to_disk()

        ctx = ConversationContext.load_from_disk(session_id)
    """

    def __init__(
        self,
        session_id: str,
        created_at: float,
        last_updated_at: float,
        messages: list[ChatMessage],
        cumulative_tokens: int,
        file_path: Optional[str],
        title: str = "",
    ):
        self.session_id = session_id
        self.created_at = created_at
        self.last_updated_at = last_updated_at
        self.messages: list[ChatMessage] = messages
        self.cumulative_tokens = cumulative_tokens
        self.file_path = file_path
        self.title = title
        self._logger = get_logger("writing_chat.context")

    # ── 工厂方法 ─────────────────────────────────────────────────────────────

    @classmethod
    def create_session(cls, file_path: Optional[str] = None) -> "ConversationContext":
        session_id = uuid.uuid4().hex
        now = time.time()
        return cls(
            session_id=session_id,
            created_at=now,
            last_updated_at=now,
            messages=[],
            cumulative_tokens=0,
            file_path=file_path,
            title="",
        )

    @classmethod
    def load_from_disk(cls, session_id: str) -> Optional["ConversationContext"]:
        """加载已有会话。不存在返回 None。"""
        path = cls._session_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(safe_read(path))
        except Exception as e:
            get_logger("writing_chat.context").error(
                f"Failed to load session {session_id}: {e}"
            )
            return None
        # Pydantic nested 重建 ChatMessage.snapshot 必须保留
        messages = [ChatMessage.model_validate(m) for m in data.get("messages", [])]
        return cls(
            session_id=data["id"],
            created_at=data.get("created_at", 0.0),
            last_updated_at=data.get("last_updated_at", 0.0),
            messages=messages,
            cumulative_tokens=data.get("cumulative_tokens", 0),
            file_path=data.get("file_path"),
            title=data.get("title", ""),
        )

    # ── 磁盘持久化 ──────────────────────────────────────────────────────────

    @staticmethod
    def _sessions_dir() -> Path:
        cfg = get_config()
        d = Path(cfg.features.writing_chat.sessions_dir)
        if not d.is_absolute():
            d = Path.cwd() / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _session_path(cls, session_id: str) -> Path:
        return cls._sessions_dir() / f"{session_id}.json"

    def save_to_disk(self) -> None:
        self.last_updated_at = time.time()
        data = {
            "id": self.session_id,
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
            "cumulative_tokens": self.cumulative_tokens,
            "file_path": self.file_path,
            "title": self.title,
            "messages": [m.model_dump(mode="json") for m in self.messages],
        }
        path = self._session_path(self.session_id)
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))

    def delete_from_disk(self) -> bool:
        path = self._session_path(self.session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    @classmethod
    def list_sessions(cls) -> list[dict[str, Any]]:
        """列出所有持久化会话的元信息，按 last_updated_at 降序。"""
        d = cls._sessions_dir()
        metas: list[dict[str, Any]] = []
        for f in d.glob("*.json"):
            try:
                data = json.loads(safe_read(f))
                metas.append({
                    "id": data["id"],
                    "created_at": data.get("created_at", 0.0),
                    "last_updated_at": data.get("last_updated_at", 0.0),
                    "message_count": len(data.get("messages", [])),
                    "cumulative_tokens": data.get("cumulative_tokens", 0),
                    "file_path": data.get("file_path"),
                    "title": data.get("title") or _derive_title(data.get("messages", [])),
                })
            except Exception:
                continue
        metas.sort(key=lambda m: m["last_updated_at"], reverse=True)
        return metas

    def rename(self, new_title: str) -> None:
        """重命名会话，持久化到磁盘。"""
        self.title = new_title.strip()
        self.save_to_disk()

    # ── 消息操作 ────────────────────────────────────────────────────────────

    def append_user_message(
        self,
        content: str,
        referenced_file: Optional[str] = None,
        referenced_paragraph: Optional[int] = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            id=uuid.uuid4().hex,
            role="user",
            content=content,
            tokens=0,  # 由 service 在收到 LLM usage 后统一更新
            referenced_file=referenced_file,
            referenced_paragraph=referenced_paragraph,
            created_at=time.time(),
        )
        self.messages.append(msg)
        self.last_updated_at = time.time()
        return msg

    def append_assistant_message(
        self,
        content: str,
        tokens: int = 0,
        edit_op: Optional[Any] = None,
    ) -> ChatMessage:
        """
        追加 assistant 回复消息。

        tokens 含义为本轮对话消耗的总 token 数（含完整上下文 prompt + 生成 completion，
        即 LLM usage.total_tokens）。这代表"当前对话上下文窗口的实际大小"，
        会直接覆盖 cumulative_tokens（取较大值，避免某些边缘场景下倒退）。
        """
        msg = ChatMessage(
            id=uuid.uuid4().hex,
            role="assistant",
            content=content,
            tokens=tokens,
            edit_op=edit_op,
            created_at=time.time(),
        )
        self.messages.append(msg)
        if tokens and tokens > self.cumulative_tokens:
            self.cumulative_tokens = tokens
        self.last_updated_at = time.time()
        return msg

    def update_last_message_tokens(self, tokens: int) -> None:
        """按 LLM usage 反推上条 user + assistant 共消耗的 tokens，平均分配到两条消息上用于记账。"""
        if not self.messages or tokens <= 0:
            return
        # 把本轮 total_tokens 视作"当前上下文窗口大小"，
        # 并把它视作 cumulative_tokens 的新基准（而非累加），
        # 因为每次 LLM 调用都会把整个 messages 数组发送出去。
        if tokens > self.cumulative_tokens:
            self.cumulative_tokens = tokens

    def set_assistant_content(self, message_id: str, content: str) -> None:
        for m in self.messages:
            if m.id == message_id:
                m.content = content
                break

    def mark_edit_applied(self, message_id: str) -> bool:
        for m in self.messages:
            if m.id == message_id and m.edit_op is not None:
                m.edit_op.applied = True
                self.last_updated_at = time.time()
                return True
        return False

    def attach_edit_op(self, message_id: str, edit_op: Any) -> bool:
        for m in self.messages:
            if m.id == message_id:
                m.edit_op = edit_op
                self.last_updated_at = time.time()
                return True
        return False

    def delete_message(self, message_id: str) -> bool:
        """删除消息；token 计数同步递减（不低于 0）。"""
        for i, m in enumerate(self.messages):
            if m.id == message_id:
                self.cumulative_tokens = max(0, self.cumulative_tokens - m.tokens)
                self.messages.pop(i)
                # summary 消息删除时丢弃其 snapshot
                self.last_updated_at = time.time()
                return True
        return False

    def undo_message(self, message_id: str) -> dict[str, Any]:
        """
        撤回一条消息。

        - 若该消息是 summary（compressed=True），则用其 snapshot 还原原始消息
        - 否则等同于删除该消息
        返回：
            { "restored": bool, "restored_count": int, "removed": bool }
        """
        idx = None
        for i, m in enumerate(self.messages):
            if m.id == message_id:
                idx = i
                break
        if idx is None:
            return {"restored": False, "restored_count": 0, "removed": False}
        msg = self.messages[idx]
        if msg.compressed and msg.snapshot:
            # 还原快照
            snapshot_msgs = msg.snapshot
            self.cumulative_tokens = max(0, self.cumulative_tokens - msg.tokens)
            for sm in snapshot_msgs:
                self.cumulative_tokens += sm.tokens
            self.messages[idx : idx + 1] = snapshot_msgs
            self.last_updated_at = time.time()
            return {
                "restored": True,
                "restored_count": len(snapshot_msgs),
                "removed": False,
            }
        # 普通消息：等同删除
        ok = self.delete_message(message_id)
        return {
            "restored": False,
            "restored_count": 0,
            "removed": ok,
        }

    # ── 压缩触发 ────────────────────────────────────────────────────────────

    def should_compress(self) -> bool:
        cfg = get_config()
        return self.cumulative_tokens >= cfg.features.writing_chat.compression_threshold_tokens

    def to_llm_messages(self) -> list[dict]:
        """
        将本会话转为 OpenAI messages 数组。

        转换规则：
            - 跳过 compressed summary 之外的 compressed 消息（理论上 summary 自身应该展示）
            - summary 消息以 assistant role 出现，content 为摘要内容前缀
            - 携带 edit_op 但未 apply 的 assistant 消息：仅保留 reply 文本（不暴露内部结构）
            - 已 apply 的 edit_op 对应的消息保留原文
        """
        result: list[dict] = []
        for m in self.messages:
            role = m.role
            if role == "summary":
                result.append({
                    "role": "assistant",
                    "content": f"[Conversation Summary]\n{m.content}",
                })
            else:
                result.append({"role": role, "content": m.content or ""})
        return result

    def snapshot_for_compression(self) -> list[ChatMessage]:
        """
        对当前 messages 做深拷贝，作为压缩前的快照保存。
        """
        import copy
        return copy.deepcopy(self.messages)

    def replace_with_summary(
        self, summary_text: str, summary_tokens: int
    ) -> ChatMessage:
        """
        用一条 summary 消息替换当前所有 messages。
        snapshot 字段保留原始消息的深拷贝。
        压缩后 cumulative_tokens 直接置为 summary_tokens，因为下次 LLM 调用
        上下文窗口就是这条 summary（加上未来加入的新消息）。
        """
        snapshot = self.snapshot_for_compression()
        summary_msg = ChatMessage(
            id=uuid.uuid4().hex,
            role="summary",
            content=summary_text,
            tokens=summary_tokens,
            compressed=True,
            snapshot=snapshot,
            created_at=time.time(),
        )
        self.messages = [summary_msg]
        self.cumulative_tokens = summary_tokens
        self.last_updated_at = time.time()
        return summary_msg

    # ── 上下文锚点 ─────────────────────────────────────────────────────────

    @staticmethod
    def build_file_context_text(file_path: str, paragraph_index: Optional[int], workspace_id: str = "") -> str:
        """
        构造文件段落上下文文本（原始 LaTeX 源码，无 marker 剥离）。

        若 paragraph_index 为 None，加载全文段落拼接（最多 12 段，超过则截断并标注省略）。
        若指定 paragraph_index，则只返回该段及其上下文相邻段。
        """
        from backend.core.workspace_manager import get_project_manager_for_workspace

        pm = get_project_manager_for_workspace(workspace_id)
        try:
            paragraphs = pm.get_paragraphs(file_path)
        except Exception as e:
            return f"(failed to load paragraphs: {e})"
        raw_paras: list[tuple[int, str]] = [
            (p.index, p.text) for p in paragraphs
        ]

        if paragraph_index is not None:
            selected = [
                (i, t) for (i, t) in raw_paras
                if i == paragraph_index
                or i == paragraph_index - 1
                or i == paragraph_index + 1
            ]
        else:
            if len(raw_paras) > 12:
                selected = raw_paras[:12]
                omitted = len(raw_paras) - 12
                note = f"\n... ({omitted} paragraphs omitted) ..."
            else:
                selected = raw_paras
                note = ""
            return _format_paragraphs(selected) + note

        return _format_paragraphs(selected)


def _format_paragraphs(items: list[tuple[int, str]]) -> str:
    lines = []
    for idx, text in items:
        lines.append(f"[Paragraph {idx}]\n{text}")
    return "\n\n".join(lines)


def _derive_title(messages: list[dict]) -> str:
    """从会话的第一条 user 消息中提取前 40 个字符作为标题。"""
    for m in messages:
        if m.get("role") == "user":
            c = (m.get("content") or "").strip().replace("\n", " ")
            return c[:40] if c else "(no title)"
    return "(no title)"
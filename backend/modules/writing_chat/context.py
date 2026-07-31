"""
写作助手 Chat 的会话上下文管理器。

职责：
    1. 在磁盘上持久化每个会话，按项目路径隔离（相同项目 = 共享会话）
    2. 维护会话中的 messages 列表与累计 token 计数（cumulative_tokens）
    3. 提供 to_llm_messages()：将本会话转为发送给 LLM 的 messages 数组
    4. 触发上下文压缩：累计 token 超阈值时把旧消息打包给 LLM 生成摘要并替换
    5. 支持删除单条消息（若是 summary 消息则还原其 snapshot）
    6. 加载文件段落作为上下文锚点（发送原始 LaTeX 源码，不用 marker）

设计约定：
    - 会话文件存于 config.features.writing_chat.sessions_dir
    - 按项目路径 hash 隔离：{sessions_dir}/{scope}/{uuid}.json
    - session_id 格式：{scope}:{uuid}，嵌入 scope 信息便于路由
    - 不在 __init__ 中缓存配置；方法内实时 get_config()（AGENTS.md）
    - 使用 pathlib 与 encoding="utf-8"（AGENTS.md）
"""

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.file_ops import atomic_write, safe_read
from backend.modules.writing_chat.models import (
    ChatMessage,
)

_SCOPE_SEPARATOR = ":"
_NOPROJECT_SCOPE = "_noproject"


def compute_scope(project_root_path: str) -> str:
    """将项目根路径哈希为稳定的 scope 标识符。"""
    if not project_root_path:
        return _NOPROJECT_SCOPE
    resolved = str(Path(project_root_path).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def parse_scoped_session_id(session_id: str) -> tuple[str, str]:
    """解析 scoped session_id，返回 (scope, id_part)。"""
    scope, _, id_part = session_id.partition(_SCOPE_SEPARATOR)
    if not scope or not id_part:
        raise ValueError(f"Invalid scoped session_id: {session_id!r}")
    return scope, id_part


def build_scoped_session_id(scope: str, id_part: str | None = None) -> str:
    """构造 scoped session_id。"""
    if id_part is None:
        id_part = uuid.uuid4().hex
    return f"{scope}{_SCOPE_SEPARATOR}{id_part}"


class ConversationContext:
    """
    单次会话的上下文。

    用法：
        ctx = ConversationContext.create_session(file_path="main.tex", scope=scope)
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
        file_path: str | None,
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
    def create_session(
        cls,
        file_path: str | None = None,
        scope: str = "",
    ) -> "ConversationContext":
        uuid_part = uuid.uuid4().hex
        session_id = build_scoped_session_id(scope, uuid_part)
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
    def load_from_disk(cls, session_id: str) -> "ConversationContext" | None:
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
    def _sessions_dir(scope: str = "") -> Path:
        cfg = get_config()
        d = Path(cfg.features.writing_chat.sessions_dir)
        if not d.is_absolute():
            d = Path.cwd() / d
        if scope:
            d = d / scope
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _session_path(cls, session_id: str) -> Path:
        scope, id_part = parse_scoped_session_id(session_id)
        return cls._sessions_dir(scope) / f"{id_part}.json"

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
    def list_sessions(cls, scope: str = "") -> list[dict[str, Any]]:
        """
        列出持久化会话的元信息，按 last_updated_at 降序。

        参数：
            scope: 为 "" 时列出所有 scope 下的会话；
                   非空时仅列出该 scope 下的会话。
        """
        metas: list[dict[str, Any]] = []

        if scope:
            dirs_to_scan = [cls._sessions_dir(scope)]
        else:
            d = cls._sessions_dir()
            dirs_to_scan = [entry for entry in d.iterdir() if entry.is_dir()]

        for d in dirs_to_scan:
            for f in d.glob("*.json"):
                try:
                    data = json.loads(safe_read(f))
                    session_id = data.get("id", "")
                    metas.append(
                        {
                            "id": session_id,
                            "created_at": data.get("created_at", 0.0),
                            "last_updated_at": data.get("last_updated_at", 0.0),
                            "message_count": len(data.get("messages", [])),
                            "cumulative_tokens": data.get("cumulative_tokens", 0),
                            "file_path": data.get("file_path"),
                            "title": data.get("title")
                            or _derive_title(data.get("messages", [])),
                        }
                    )
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
        referenced_file: str | None = None,
        referenced_paragraph: int | None = None,
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
        edit_op: Any | None = None,
        scholarly_results: list[dict[str, Any]] | None = None,
        scholarly_query: str = "",
        scholarly_total: int = 0,
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
            scholarly_results=scholarly_results or [],
            scholarly_query=scholarly_query,
            scholarly_total=scholarly_total,
            created_at=time.time(),
        )
        self.messages.append(msg)
        if tokens and tokens > self.cumulative_tokens:
            self.cumulative_tokens = tokens
        self.last_updated_at = time.time()
        return msg

    def append_tool_messages(self, tool_msg_dicts: list[dict]) -> None:
        """
        将 LLM 工具调用循环产生的 tool 相关消息持久化到上下文。

        tool_msg_dicts 中的每条 dict 应为以下两种格式之一：
            - assistant 含 tool_calls: {"role": "assistant", "content": ..., "tool_calls": [...]}
            - tool 结果:           {"role": "tool", "tool_call_id": "...", "content": "..."}
        """
        for msg_dict in tool_msg_dicts:
            role = msg_dict.get("role", "")
            if role == "tool":
                msg = ChatMessage(
                    id=uuid.uuid4().hex,
                    role="tool",
                    content=msg_dict.get("content", ""),
                    tool_call_id=msg_dict.get("tool_call_id", ""),
                    tokens=0,
                    created_at=time.time(),
                )
                self.messages.append(msg)
            elif role == "assistant" and "tool_calls" in msg_dict:
                msg = ChatMessage(
                    id=uuid.uuid4().hex,
                    role="assistant",
                    content=msg_dict.get("content") or "",
                    tool_calls=msg_dict["tool_calls"],
                    tokens=0,
                    created_at=time.time(),
                )
                self.messages.append(msg)
        self.last_updated_at = time.time()

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

    def delete_message(self, message_id: str) -> dict[str, Any]:
        """
        删除一条消息。

        - 若该消息是 summary（compressed=True），则用其 snapshot 还原原始消息
        - 否则移除该消息；token 计数同步递减（不低于 0）
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
        # 普通消息：直接移除
        self.cumulative_tokens = max(0, self.cumulative_tokens - msg.tokens)
        self.messages.pop(idx)
        self.last_updated_at = time.time()
        return {"restored": False, "restored_count": 0, "removed": True}

    # ── 压缩触发 ────────────────────────────────────────────────────────────

    def should_compress(self) -> bool:
        cfg = get_config()
        return (
            self.cumulative_tokens
            >= cfg.features.writing_chat.compression_threshold_tokens
        )

    def to_llm_messages(self) -> list[dict]:
        """
        将本会话转为 OpenAI messages 数组。

        转换规则：
            - summary 消息以 assistant role 出现，content 为摘要内容前缀
            - assistant 消息携带 tool_calls 时，注入 tool_calls 字段
            - tool 角色消息注入 tool_call_id
            - 其他消息按 role/content 原样传递
        """
        result: list[dict] = []
        for m in self.messages:
            role = m.role
            if role == "summary":
                result.append(
                    {
                        "role": "assistant",
                        "content": f"[Conversation Summary]\n{m.content}",
                    }
                )
            elif role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }
                )
            elif role == "assistant" and m.tool_calls:
                result.append(
                    {
                        "role": "assistant",
                        "content": m.content or "",
                        "tool_calls": m.tool_calls,
                    }
                )
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
    def build_file_context_text(
        file_path: str, paragraph_index: int | None, workspace_id: str = ""
    ) -> str:
        """
        构造文件段落上下文文本（原始 LaTeX 源码，无 marker 剥离）。

        若 paragraph_index 为 None，加载全文段落拼接（最多 12 段，超过则截断并标注省略）。
        若指定 paragraph_index，则只返回该段及其上下文相邻段。
        若 paragraph_index 为 -1，返回空字符串（表示不注入上下文内容）。
        """
        # -1 表示显式关闭上下文锚点，不注入任何文件内容
        if paragraph_index == -1:
            return ""

        from backend.core.workspace_manager import get_project_manager_for_workspace

        pm = get_project_manager_for_workspace(workspace_id)
        try:
            paragraphs = pm.get_paragraphs(file_path)
        except Exception as e:
            return f"(failed to load paragraphs: {e})"
        raw_paras: list[tuple[int, str]] = [(p.index, p.text) for p in paragraphs]

        if paragraph_index is not None:
            selected = [
                (i, t)
                for (i, t) in raw_paras
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

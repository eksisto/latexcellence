"""
写作助手的会话上下文管理器。

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

import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.writing_chat.models import (
    ChatMessage,
)
from backend.utils.file_ops import atomic_write, safe_read

_SCOPE_SEPARATOR = ":"
_NOPROJECT_SCOPE = "_noproject"
# scope 是项目路径哈希或 _noproject；id_part 是 uuid4().hex。
# 两者都必须是单层安全目录/文件名，禁止路径分隔符、'..' 等穿越字符。
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ID_PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# list_sessions() 文件级元数据缓存：path -> (mtime, meta_dict)
# 仅缓存轻量字段（不含 messages），由 save/delete 操作主动失效。
_session_meta_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def compute_scope(project_root_path: str) -> str:
    """将项目根路径哈希为稳定的 scope 标识符。"""
    if not project_root_path:
        return _NOPROJECT_SCOPE
    resolved = str(Path(project_root_path).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def parse_scoped_session_id(session_id: str) -> tuple[str, str]:
    """解析 scoped session_id，返回 (scope, id_part)。"""
    if not isinstance(session_id, str):
        raise ValueError(f"Invalid scoped session_id: {session_id!r}")
    scope, _, id_part = session_id.partition(_SCOPE_SEPARATOR)
    if not scope or not id_part:
        raise ValueError(f"Invalid scoped session_id: {session_id!r}")
    if not _SCOPE_RE.fullmatch(scope) or not _ID_PART_RE.fullmatch(id_part):
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
    def load_from_disk(cls, session_id: str) -> "ConversationContext | None":
        """加载已有会话。不存在返回 None。"""
        try:
            path = cls._session_path(session_id)
        except ValueError:
            # 非法 session_id（格式错误/路径穿越）按"会话不存在"处理
            return None
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

    @classmethod
    async def load_from_disk_async(
        cls, session_id: str
    ) -> "ConversationContext | None":
        """异步加载已有会话：同步实现经 asyncio.to_thread 调度（零逻辑复制）。

        会话 JSON 可能很大（含工具消息与压缩快照），在事件循环内同步读盘
        会阻塞并发请求；async 服务层一律使用本方法。
        """
        return await asyncio.to_thread(cls.load_from_disk, session_id)

    # ── 磁盘持久化 ──────────────────────────────────────────────────────────

    @staticmethod
    def _sessions_dir(scope: str = "") -> Path:
        from backend.core.multiuser import userspace

        cfg = get_config()
        legacy = Path(cfg.features.writing_chat.sessions_dir)
        if not legacy.is_absolute():
            legacy = Path.cwd() / legacy
        # 多用户模式：按当前用户隔离到 <users_root>/<user_id>/writing_chat_sessions/
        base = userspace.user_resource_root("writing_chat_sessions", legacy)
        if scope:
            base = base / scope
        base.mkdir(parents=True, exist_ok=True)
        return base

    @classmethod
    def _session_path(cls, session_id: str) -> Path:
        scope, id_part = parse_scoped_session_id(session_id)
        base = cls._sessions_dir(scope).resolve()
        path = (base / f"{id_part}.json").resolve()
        # 纵深防御：即使正则漏网，也确保解析后的路径不越出会话目录
        if not path.is_relative_to(base):
            raise ValueError("session_id resolves outside the sessions directory")
        return path

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
        _session_meta_cache.pop(str(path), None)

    async def save_to_disk_async(self) -> None:
        """异步落盘：同步实现经 asyncio.to_thread 调度（零逻辑复制）。"""
        await asyncio.to_thread(self.save_to_disk)

    def delete_from_disk(self) -> bool:
        path = self._session_path(self.session_id)
        if path.exists():
            path.unlink()
            _session_meta_cache.pop(str(path), None)
            return True
        return False

    @classmethod
    def list_sessions(cls, scope: str = "") -> list[dict[str, Any]]:
        """
        列出持久化会话的元信息，按 last_updated_at 降序。

        使用文件 mtime 缓存避免每次调用都解析全部 JSON；仅在文件修改时间
        变化时重新读取。参数：
            scope: 为 "" 时列出所有 scope 下的会话；
                   非空时仅列出该 scope 下的会话。
        """
        global _session_meta_cache
        metas: list[dict[str, Any]] = []

        if scope:
            dirs_to_scan = [cls._sessions_dir(scope)]
        else:
            d = cls._sessions_dir()
            dirs_to_scan = [entry for entry in d.iterdir() if entry.is_dir()]

        for d in dirs_to_scan:
            for f in d.glob("*.json"):
                fpath = str(f)
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                cached = _session_meta_cache.get(fpath)
                if cached and cached[0] == mtime:
                    metas.append(cached[1])
                    continue
                try:
                    data = json.loads(safe_read(f))
                    session_id = data.get("id", "")
                    meta = {
                        "id": session_id,
                        "created_at": data.get("created_at", 0.0),
                        "last_updated_at": data.get("last_updated_at", 0.0),
                        "message_count": len(data.get("messages", [])),
                        "cumulative_tokens": data.get("cumulative_tokens", 0),
                        "file_path": data.get("file_path"),
                        "title": data.get("title")
                        or _derive_title(data.get("messages", [])),
                    }
                    _session_meta_cache[fpath] = (mtime, meta)
                    metas.append(meta)
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
        search_results: list[dict[str, Any]] | None = None,
        search_query: str = "",
        deep_read_pages: int = 0,
        rewritten_queries: list[str] | None = None,
        citation_note: str = "",
        unverified_citations: list[str] | None = None,
        evidence_sources: list[dict[str, Any]] | None = None,
        page_reads: list[dict[str, Any]] | None = None,
        cited_evidence_ids: list[str] | None = None,
        unverified_evidence_ids: list[str] | None = None,
        suggestions: list[Any] | None = None,
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
            search_results=search_results or [],
            search_query=search_query,
            deep_read_pages=deep_read_pages,
            rewritten_queries=rewritten_queries or [],
            citation_note=citation_note,
            unverified_citations=unverified_citations or [],
            evidence_sources=evidence_sources or [],
            page_reads=page_reads or [],
            cited_evidence_ids=cited_evidence_ids or [],
            unverified_evidence_ids=unverified_evidence_ids or [],
            suggestions=suggestions or [],
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
        """
        用本轮真实 usage.total_tokens 更新累计值，并按内容长度比例把 total
        分摊到本轮 user/assistant 两条消息的 tokens 字段。

        tokens 字段仅用于删除/还原时的近似记账（近似值由下一次真实 usage 校准），
        不是单条消息在 API 请求中的精确消耗。
        """
        if tokens <= 0:
            return
        if tokens > self.cumulative_tokens:
            self.cumulative_tokens = tokens

        user_idx: int | None = None
        assistant_idx: int | None = None
        for i, m in enumerate(self.messages):
            if m.role == "user":
                user_idx = i
            elif m.role == "assistant" and not m.tool_calls:
                assistant_idx = i
        if user_idx is None:
            if assistant_idx is not None:
                self.messages[assistant_idx].tokens = tokens
            return
        if assistant_idx is None:
            self.messages[user_idx].tokens = tokens
            return
        user_len = max(1, len(self.messages[user_idx].content or ""))
        asst_len = max(1, len(self.messages[assistant_idx].content or ""))
        user_tokens = int(tokens * user_len / (user_len + asst_len))
        self.messages[user_idx].tokens = max(0, user_tokens)
        self.messages[assistant_idx].tokens = max(0, tokens - user_tokens)

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

    def mark_suggestion_applied(
        self, message_id: str, index: int | None = None
    ) -> bool:
        """置位建议应用标记（编辑器助手，editor-design §5.2）。

        index=None：消息级旧行为，直接置 suggestion_applied=True（向后兼容）。
        index 非 None：index 级标记——去重追加进 applied_suggestions，仅当
        applied_suggestions 已覆盖该消息 suggestions 的全部 index 时才置
        suggestion_applied=True（逐卡审阅场景下部分应用不算整体已应用）。
        """
        for m in self.messages:
            if m.id == message_id:
                if index is None:
                    m.suggestion_applied = True
                else:
                    if index not in m.applied_suggestions:
                        m.applied_suggestions.append(index)
                    all_indices = {s.index for s in m.suggestions}
                    if set(m.applied_suggestions) >= all_indices:
                        m.suggestion_applied = True
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

    @staticmethod
    def _estimate_message_tokens(message: Any) -> int:
        """估算单条消息占用的 token（字符数近似，用于切分预算）。"""
        content = message.content or ""
        total = len(content) / 3.5
        if getattr(message, "tool_calls", None):
            total += len(str(message.tool_calls)) / 3.5
        return int(total) + 1

    def split_for_compression(
        self,
        keep_tokens: int | None = None,
        keep_turns: int | None = None,
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        """
        将消息切分为 (待摘要, 原样保留)。

        从尾部向前保留最近若干轮（轮数预算与 token 预算取先满足者）：
        - 一个“轮”以 user 或 summary 消息为起点，包含其后到下一个 user 前的
          全部 assistant / tool 消息，切分不会落在工具调用对中间；
        - summary 视为独立轮次单元，连续压缩时嵌套快照语义保持；
        - token 预算超出时优先保留更近的轮。
        """
        cfg = get_config()
        token_budget = (
            cfg.features.writing_chat.compression_keep_recent_tokens
            if keep_tokens is None
            else keep_tokens
        )
        turn_budget = (
            cfg.features.writing_chat.compression_keep_recent_turns
            if keep_turns is None
            else keep_turns
        )
        msgs = self.messages
        if not msgs or turn_budget <= 0:
            return msgs, []

        turn_starts = [i for i, m in enumerate(msgs) if m.role in ("user", "summary")]
        if not turn_starts:
            return msgs, []

        split_idx = turn_starts[-min(turn_budget, len(turn_starts))]

        # token 预算：超限时向前推进到下一个轮次起点（丢弃最旧的保留轮）
        kept_tokens = sum(self._estimate_message_tokens(m) for m in msgs[split_idx:])
        next_turn_positions = [idx for idx in turn_starts if idx > split_idx]
        while kept_tokens > token_budget and next_turn_positions:
            new_split = next_turn_positions.pop(0)
            kept_tokens -= sum(
                self._estimate_message_tokens(m) for m in msgs[split_idx:new_split]
            )
            split_idx = new_split

        # 安全网：切分点绝不落在 tool 消息与其 assistant+tool_calls 对之间
        while split_idx < len(msgs) and msgs[split_idx].role == "tool":
            j = split_idx - 1
            while j >= 0 and msgs[j].role == "tool":
                j -= 1
            if j >= 0 and msgs[j].role == "assistant" and msgs[j].tool_calls:
                split_idx = j
                break
            split_idx += 1

        return msgs[:split_idx], msgs[split_idx:]

    def should_compress(self, window_tokens: int | None = None) -> bool:
        """
        判断是否需要压缩。

        compression_threshold_tokens > 0 时按固定阈值；
        为 0 时按窗口动态推导：
        window - max_tokens - headroom - keep_recent - summary_budget。
        同步调用拿不到窗口时用 200000 兜底；异步调用应优先走
        should_compress_async() 获取真实窗口。
        """
        cfg = get_config()
        wc = cfg.features.writing_chat
        threshold = wc.compression_threshold_tokens
        if threshold <= 0:
            window = window_tokens if window_tokens is not None else 200000
            threshold = max(
                1,
                window
                - cfg.llm.max_tokens
                - wc.compression_headroom_tokens
                - wc.compression_keep_recent_tokens
                - wc.summary_token_budget,
            )
        return self.cumulative_tokens >= threshold

    async def should_compress_async(self) -> bool:
        """
        异步判断是否需要压缩；动态推导时先探测模型上下文窗口。
        """
        from backend.core.model_router import get_client_for

        cfg = get_config().features.writing_chat
        if cfg.compression_threshold_tokens > 0:
            return self.should_compress()
        # writing_chat 槽位（multi-model §6）
        window = await get_client_for("writing_chat").get_context_window()
        return self.should_compress(window)

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
                evidence_appendix = ""
                if m.evidence_sources:
                    lines = ["", "[Preserved Web Evidence]"]
                    for item in m.evidence_sources:
                        heading = " > ".join(item.heading_path)
                        lines.append(
                            f"[EVIDENCE {item.evidence_id}] {item.title} | "
                            f"{item.source_url} | {heading}\n{item.text}"
                        )
                    evidence_appendix = "\n".join(lines)
                result.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"[Conversation Summary]\n{m.content}{evidence_appendix}"
                        ),
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
        return copy.deepcopy(self.messages)

    def replace_with_summary(
        self,
        summary_text: str,
        summary_tokens: int,
        keep_messages: list[ChatMessage] | None = None,
    ) -> ChatMessage:
        """
        用一条 summary 消息替换旧消息，原样保留 keep_messages（最近消息）。

        snapshot 字段保留被替换部分的原始消息深拷贝；keep_messages 为空时
        退化为“替换全部”（旧行为）。压缩后 cumulative_tokens 置为
        summary_tokens + 保留消息按字符数/3.5 估算之和（近似值，由下一次
        真实 usage 校准；不使用消息 tokens 字段，避免把历史请求 total
        的分摊值误当作增量）。
        """
        if keep_messages:
            keep_ids = {m.id for m in keep_messages}
            cut = 0
            while cut < len(self.messages) and self.messages[cut].id not in keep_ids:
                cut += 1
            snapshot = copy.deepcopy(self.messages[:cut])
            kept = list(keep_messages)
        else:
            snapshot = self.snapshot_for_compression()
            kept = []
        evidence_sources = []
        page_reads = []
        seen_evidence: set[str] = set()
        seen_pages: set[tuple[str, str]] = set()
        cfg = get_config().features.writing_chat
        evidence_chars = 0
        for message in snapshot:
            for item in message.evidence_sources:
                if item.evidence_id in seen_evidence:
                    continue
                if len(evidence_sources) >= cfg.evidence_max_items:
                    break
                remaining = cfg.evidence_max_chars - evidence_chars
                if remaining <= 0:
                    break
                copied = item.model_copy(deep=True)
                copied.text = copied.text[:remaining]
                evidence_sources.append(copied)
                seen_evidence.add(copied.evidence_id)
                evidence_chars += len(copied.text)
            for page in message.page_reads:
                key = (page.document_id, page.url)
                if key not in seen_pages:
                    page_reads.append(page.model_copy(deep=True))
                    seen_pages.add(key)
        summary_msg = ChatMessage(
            id=uuid.uuid4().hex,
            role="summary",
            content=summary_text,
            tokens=summary_tokens,
            compressed=True,
            snapshot=snapshot,
            evidence_sources=evidence_sources,
            page_reads=page_reads,
            created_at=time.time(),
        )
        self.messages = [summary_msg] + kept
        self.cumulative_tokens = summary_tokens + sum(
            self._estimate_message_tokens(m) for m in kept
        )
        self.last_updated_at = time.time()
        return summary_msg

    # ── 上下文锚点 ─────────────────────────────────────────────────────────

    # 无指定段落时的采样预算（head / middle / tail 各取多少段）
    _SAMPLE_HEAD = 3
    _SAMPLE_MIDDLE = 3
    _SAMPLE_TAIL = 3
    # 指定段落时的上下文扩展半径
    _ANCHOR_RADIUS = 2

    @staticmethod
    def build_file_context_text(
        file_path: str, paragraph_index: int | None, workspace_id: str = ""
    ) -> str:
        """
        构造文件段落上下文 XML 信封（原始 LaTeX 源码，无 marker 剥离）。

        返回 <documents> 包裹的 XML，包含 <document>（源路径、段落统计）、
        <document_content>（带索引的 <paragraph> 标签）和 <context_note>（截断状态）。

        若 paragraph_index 为 -1，返回空字符串（表示不注入上下文内容）。
        若指定 paragraph_index ≥ 0，以该段为锚点取 ±ANCHOR_RADIUS 范围的段落。
        若 paragraph_index 为 None，采用 head + middle + tail 采样策略覆盖全文。
        """
        # -1 表示显式关闭上下文锚点，不注入任何文件内容
        if paragraph_index == -1:
            return ""

        from backend.core.workspace_manager import get_project_manager_for_workspace

        pm = get_project_manager_for_workspace(workspace_id)
        try:
            paragraphs = pm.get_paragraphs(file_path)
        except Exception as e:
            return _documents_envelope(file_path, [], 0, truncated=False, error=str(e))
        raw_paras: list[tuple[int, str]] = [(p.index, p.text) for p in paragraphs]
        total = len(raw_paras)

        if paragraph_index is not None:
            selected, selected_indices = _select_anchor(
                raw_paras, paragraph_index, ConversationContext._ANCHOR_RADIUS
            )
            truncated = len(selected) < total
        else:
            selected, selected_indices, truncated = _select_sample(
                raw_paras,
                ConversationContext._SAMPLE_HEAD,
                ConversationContext._SAMPLE_MIDDLE,
                ConversationContext._SAMPLE_TAIL,
            )

        return _documents_envelope(file_path, selected, total, truncated=truncated)


# ── 上下文格式化辅助函数 ─────────────────────────────────────────────────────


def _select_anchor(
    raw_paras: list[tuple[int, str]],
    paragraph_index: int,
    radius: int,
) -> tuple[list[tuple[int, str]], set[int]]:
    """以 paragraph_index 为中心取 ±radius 的段落（基于 index 值，非位置）。"""
    selected = [(i, t) for (i, t) in raw_paras if abs(i - paragraph_index) <= radius]
    indices = {i for i, _ in selected}
    return selected, indices


def _select_sample(
    raw_paras: list[tuple[int, str]],
    head_n: int,
    middle_n: int,
    tail_n: int,
) -> tuple[list[tuple[int, str]], set[int], bool]:
    """无指定段落时，从头/中/尾采样，返回 (selected, selected_indices, truncated)。"""
    total = len(raw_paras)
    if total <= head_n + middle_n + tail_n:
        indices = {i for i, _ in raw_paras}
        return raw_paras, indices, False

    head = raw_paras[:head_n]
    mid_start = max(head_n, (total - middle_n) // 2)
    middle = raw_paras[mid_start : mid_start + middle_n]
    tail = raw_paras[-tail_n:]

    # 去重保序
    seen: set[int] = set()
    selected: list[tuple[int, str]] = []
    for item in head + middle + tail:
        if item[0] not in seen:
            selected.append(item)
            seen.add(item[0])
    return selected, seen, True


def _paragraph_xml(index: int, text: str) -> str:
    """将单个段落渲染为 <paragraph> XML 标签。

    注意：段落正文（LaTeX 源码）刻意不做 XML 转义——该信封由 LLM 作为
    文本消费而非机器解析，转义 `&` / `<` 会破坏 LaTeX 可读性；正文出现
    字面 `<`（如数学模式 `$a<b$`）时信封不是严格 well-formed XML，属
    可接受的取舍。仅对 source 属性与错误消息等元数据做转义。
    """
    return f'<paragraph index="{index}">\n{text}\n</paragraph>'


def _documents_envelope(
    file_path: str,
    paragraphs: list[tuple[int, str]],
    total_paragraphs: int,
    *,
    truncated: bool = False,
    error: str = "",
) -> str:
    """构造完整的 <documents> XML 信封。"""
    if error:
        return (
            "<documents>\n"
            f'<document source="{_xml_attr(file_path)}">\n'
            f"<context_note>Error loading file: {_xml_text(error)}</context_note>\n"
            "</document>\n"
            "</documents>"
        )

    para_count = len(paragraphs)
    omitted = total_paragraphs - para_count

    parts: list[str] = [
        "<documents>",
        f'<document source="{_xml_attr(file_path)}">',
        f"<total_paragraphs>{total_paragraphs}</total_paragraphs>",
        "<document_content>",
    ]
    for idx, text in paragraphs:
        parts.append(_paragraph_xml(idx, text))
    parts.append("</document_content>")

    if truncated:
        parts.append(
            f"<context_note>Context is truncated: {omitted} of "
            f"{total_paragraphs} paragraphs omitted. "
            "The shown paragraphs may not cover all relevant content. "
            "Use the read_file_content or search_across_files tools to "
            "retrieve additional sections if needed.</context_note>"
        )

    parts.append("</document>")
    parts.append("</documents>")
    return "\n".join(parts)


def _xml_attr(s: str) -> str:
    """转义 XML 属性中的特殊字符。"""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _xml_text(s: str) -> str:
    """转义 XML 文本内容中的特殊字符。"""
    return s.replace("&", "&amp;").replace("<", "&lt;")


def _derive_title(messages: list[dict]) -> str:
    """从会话的第一条 user 消息中提取前 40 个字符作为标题。

    没有 user 消息（或内容为空）时返回空字符串，由前端按用户语言
    显示“无标题”类兜底文案，避免后端硬编码特定语言的占位符。
    """
    for m in messages:
        if m.get("role") == "user":
            c = (m.get("content") or "").strip().replace("\n", " ")
            return c[:40] if c else ""
    return ""

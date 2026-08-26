"""写作助手服务：上下文压缩与超限恢复。"""

from typing import Any

from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.modules.writing_chat.context import ConversationContext
from backend.modules.writing_chat.models import ChatMessage

from . import _deps


class CompactionMixin:
    """上下文超限判断、确定性兜底与 LLM 摘要压缩。"""

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        """判断异常是否属于上下文超限（可恢复）。"""
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "context length",
                "context_length",
                "maximum context",
                "context window",
                "context_window",
                "too many tokens",
                "token limit",
                "input length",
                "sequence length",
            )
        )

    @staticmethod
    def _build_summary_transcript(messages: list[ChatMessage]) -> str:
        """
        把待摘要消息转为摘要请求文本。

        剔除 role=tool 原始结果与 assistant+tool_calls 对的原始内容，
        只保留 user / assistant / summary 的对话文本，避免压缩请求本身超限。
        """
        parts: list[str] = []
        for m in messages:
            if m.role == "tool":
                continue
            if m.role == "assistant" and m.tool_calls:
                continue
            role = "summary" if m.role == "summary" else m.role
            content = m.content or ""
            if m.evidence_sources:
                evidence_lines = ["[PRESERVED WEB EVIDENCE]"]
                for item in m.evidence_sources:
                    evidence_lines.append(
                        f"[EVIDENCE {item.evidence_id}] {item.title} | "
                        f"{item.source_url} | {' > '.join(item.heading_path)}\n"
                        f"{item.text}"
                    )
                content += "\n\n" + "\n".join(evidence_lines)
            parts.append(f"[{role.upper()}]\n{content}")
        return "\n\n".join(parts)

    def _fallback_compact(self, ctx: ConversationContext) -> bool:
        """
        确定性兜底压缩：保留最近 N 轮原样，丢弃旧工具对，插入简短说明。

        与 template_gen.agent._compact_messages 风格一致，保证 LLM 摘要
        失败/超时/空摘要时上下文仍可继续。
        """
        to_summarize, to_keep = ctx.split_for_compression()
        if len(to_summarize) < 4:
            return False
        note = (
            "[Earlier conversation was compacted to fit the context window. "
            "The current project files on disk are the source of truth; "
            "continue from the retained messages below.]"
        )
        note_tokens = int(len(note) / 3.5) + 50
        ctx.replace_with_summary(note, note_tokens, keep_messages=to_keep)
        self._logger.info(
            "Session %s fallback-compacted (kept %s messages)",
            ctx.session_id,
            len(to_keep),
        )
        return True

    async def _recover_from_context_overflow(
        self,
        ctx: ConversationContext,
        system_prompt: str,
        persisted_tool_msg_refs: list[dict[str, Any]],
        tool_messages: list[dict],
    ) -> tuple[list[dict], list[dict], bool] | None:
        """
        工具循环上下文超限时的确定性恢复：

        1. 优先裁剪旧工具对（完整对、保留当前轮），不改变持久化；
        2. 仍超限：把已完成的历史工具轮次持久化到会话 → LLM 摘要，失败则
           确定性裁剪兜底；
        3. 用压缩后的会话重建 messages（不动当前正在执行的轮次）。

        返回 (tool_messages, 新的 persisted_refs, 是否发生压缩)；
        彻底失败返回 None。
        """
        try:
            # 1) 裁剪旧工具对：完整对整体删除，保留当前轮；列表变化即直接重试
            trimmed = self._trim_tool_messages(
                list(tool_messages),
                max_chars=self._MAX_TOOL_CONTEXT_CHARS // 2,
            )
            if len(trimmed) != len(tool_messages):
                return trimmed, persisted_tool_msg_refs, False

            # 2) 裁剪不够：先持久化已完成的历史工具轮次，再压缩旧历史
            if persisted_tool_msg_refs:
                ctx.append_tool_messages(persisted_tool_msg_refs)
            compressed = await self._maybe_compress(ctx)
            if not compressed:
                compressed = self._fallback_compact(ctx)
            if not compressed:
                return None
            await ctx.save_to_disk_async()
            tool_messages = [
                {"role": "system", "content": system_prompt}
            ] + ctx.to_llm_messages()
            return tool_messages, [], True
        except Exception as e:
            self._logger.error(f"Context overflow recovery failed: {e}")
            return None

    async def _maybe_compress(
        self, ctx: ConversationContext, keep_multiplier: float = 1.0
    ) -> bool:
        """
        调用 LLM 生成摘要并替换旧历史（最近轮次原样保留）。

        - 摘要输入剔除 tool 原始结果，体积显著小于全量历史；
        - 摘要 tokens 优先使用真实 usage，无 usage 时按字符降级估算；
        - 上下文超限时扩大保留预算重试（摘要更少内容）；
        - LLM 失败/空摘要时回退到 _fallback_compact()，不阻塞对话。
        """
        cfg = get_config().features.writing_chat
        llm = _deps.get_llm_client()
        for attempt in range(self._MAX_SUMMARY_RETRIES):
            keep_tokens = int(cfg.compression_keep_recent_tokens * keep_multiplier)
            keep_turns = max(
                1, int(cfg.compression_keep_recent_turns * keep_multiplier)
            )
            to_summarize, to_keep = ctx.split_for_compression(
                keep_tokens=keep_tokens, keep_turns=keep_turns
            )
            if len(to_summarize) < 4:
                return False  # 太短不值得压缩
            transcript = self._build_summary_transcript(to_summarize)
            if not transcript.strip():
                return False
            messages = [
                {
                    "role": "system",
                    "content": get_prompt("writing_chat", "summary_system"),
                },
                {"role": "user", "content": f"CONVERSATION:\n{transcript}"},
            ]
            try:
                summary_text, usage = await llm.chat_with_usage(
                    messages,
                    temperature=cfg.summary_temperature,
                    max_tokens=cfg.summary_token_budget,
                    model=cfg.summary_model or None,
                )
            except Exception as e:
                self._logger.warning(
                    "Compression LLM failed (attempt %s): %s", attempt + 1, e
                )
                if (
                    self._is_context_overflow_error(e)
                    and attempt < self._MAX_SUMMARY_RETRIES - 1
                ):
                    keep_multiplier *= 2
                    continue
                return self._fallback_compact(ctx)

            if not summary_text or not summary_text.strip():
                self._logger.warning("Compression LLM returned empty summary, fallback")
                return self._fallback_compact(ctx)
            summary_text = summary_text.strip()

            if usage is not None and usage.completion_tokens > 0:
                summary_tokens = usage.completion_tokens
            else:
                summary_tokens = int(len(summary_text) / 3.5) + 50
            ctx.replace_with_summary(
                summary_text, summary_tokens, keep_messages=to_keep
            )
            self._logger.info(
                "Session %s compressed: summary=%s tokens, kept=%s messages, "
                "snapshot saved",
                ctx.session_id,
                summary_tokens,
                len(to_keep),
            )
            return True
        return self._fallback_compact(ctx)

"""
写作助手 Chat 业务逻辑。

核心设计：
    1. 无 marker 保护：整个 writing_chat 作为特例，不采用 LaTeX → marker → 纯文本 的剥离/还原流程。
       LLM 直接接收原始 LaTeX 源码，包括所有命令、环境、公式，能够充分理解文档结构与内容。
    2. 双轮协议：
        - RS1: chat_stream_with_usage 流式输出 Markdown 回复给用户看
        - RS2: 仅当用户点击"生成编辑"按钮后，发起独立的 JSON 结构化请求
          (LlmEditResult schema + retry_with_validation)，得到编辑操作建议
          附挂到该 assistant 消息上（不直接写回 .tex，等用户再点应用）
    3. 上下文压缩：
        - 每次发送消息前判断 cumulative_tokens 是否达到阈值
        - 触发时把 messages 打包送 LLM 生成一条 summary 摘要，替换全部历史
        - 被压缩的原始消息保存在 summary 的 snapshot 字段，支持撤回

事件流（RS1）通过 SSE 推送：
    { type: "token", content: "..." }       # 流式 Markdown 片段
    { type: "compressed" }                  # 本轮触发了压缩（前端提示）
    { type: "done", session_id, message_id, tokens }
    { type: "error", message: "..." }
"""

import asyncio
import difflib
import json
import time
import uuid
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.tool_registry import get_tool_registry
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation
from backend.utils.time_utils import get_current_time_context
from backend.utils.web_search import DDGChallengeError, get_search_manager
from backend.utils.project_tools import get_project_tools_manager
from backend.utils.project_tools.edit_tools_manager import get_edit_tools_manager
from backend.modules.writing_chat.context import (
    ConversationContext,
    _NOPROJECT_SCOPE,
    compute_scope,
)
from backend.modules.writing_chat.models import (
    ApplyEditRequest,
    ChatMessage,
    ChatSendRequest,
    DeleteMessageRequest,
    EditOp,
    GenerateEditRequest,
    LlmEditResult,
    UndoMessageRequest,
)

# ── Service ────────────────────────────────────────────────────────────────


class WritingChatService:
    """
    写作助手 Chat 服务（单例）。
    """

    # 工具名 → i18n label 映射，前端通过 label 查找 toolStatus 翻译
    _TOOL_LABELS: dict[str, str] = {
        "web_search": "searching",
        "list_files": "browsing_files",
        "read_file_content": "reading_file",
        "get_section_structure": "parsing_structure",
        "search_across_files": "searching_files",
        "edit_paragraph": "editing_paragraph",
        "undo_last_edit": "undoing_edit",
        "request_clarification": "requesting_clarification",
        "search_scholarly_works": "searching_scholarly",
        "get_work_details": "getting_work_details",
        "find_related_works": "finding_related_works",
        "get_full_text_or_sections": "reading_full_text",
        "search_research_data_resources": "searching_research_data",
    }

    def __init__(self):
        self._logger = get_logger("writing_chat.service")
        # 待用户确认的工具调用（写操作：edit_paragraph / undo_last_edit）
        # key: pending_id, value: {event: asyncio.Event, confirmed: bool | None, tool_name: str, ...}
        self._pending_confirms: dict[str, dict[str, Any]] = {}
        # 待用户回复的澄清请求（request_clarification）
        # key: clarify_id, value: {event: asyncio.Event, answered: bool, answer: str, created_at: float}
        self._pending_clarifications: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _get_tool_label(tool_name: str, _tool_args: dict) -> str:
        return WritingChatService._TOOL_LABELS.get(tool_name, "running_tool")

    @staticmethod
    def _is_sensitive_tool(tool_name: str) -> bool:
        """判断工具是否属于敏感操作（需要用户确认）。"""
        return tool_name in ("edit_paragraph", "undo_last_edit")

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

    # ── 工具方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _no_api_key() -> bool:
        return not bool(get_config().llm.api_key)

    @staticmethod
    def _get_scope(workspace_id: str) -> str:
        """从 workspace_id 获取项目路径的 hash，作为会话 scope。无项目时返回 _noproject。"""
        if not workspace_id:
            return _NOPROJECT_SCOPE
        try:
            pm = get_project_manager_for_workspace(workspace_id)
            if pm.is_open:
                return compute_scope(pm.root_path)
        except Exception:
            pass
        return _NOPROJECT_SCOPE

    # ── 会话管理 ──────────────────────────────────────────────────────────

    def list_sessions(self, workspace_id: str = "") -> list[dict[str, Any]]:
        scope = self._get_scope(workspace_id) if workspace_id else ""
        return ConversationContext.list_sessions(scope)

    def load_session(self, session_id: str) -> ConversationContext | None:
        return ConversationContext.load_from_disk(session_id)

    def delete_session(self, session_id: str) -> bool:
        ctx = ConversationContext.load_from_disk(session_id)
        if ctx is None:
            return False
        return ctx.delete_from_disk()

    def rename_session(self, session_id: str, title: str) -> dict:
        ctx = ConversationContext.load_from_disk(session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {session_id}")
        ctx.rename(title)
        return {"status": "ok", "title": ctx.title}

    # ── 发送消息（RS1，SSE 流式） ────────────────────────────────────────

    async def send(
        self, req: ChatSendRequest, workspace_id: str = ""
    ) -> AsyncIterator[dict]:
        """
        主对话流：发送用户消息、流式接收 LLM 回复、必要时执行压缩。

        返回的 SSE 事件结构见模块 docstring。
        """
        if self._no_api_key():
            yield {"type": "error", "message": "No API key configured"}
            return

        # 1. 加载或创建会话
        scope = self._get_scope(workspace_id)
        ctx = (
            ConversationContext.load_from_disk(req.session_id)
            if req.session_id
            else None
        )
        if ctx is None:
            ctx = ConversationContext.create_session(
                file_path=req.file_path, scope=scope
            )

        # 2. 构造上下文锚点文本 + 追加 user 消息
        ctx.file_path = req.file_path or ctx.file_path
        ctx.save_to_disk()
        context_block = self._build_context_block(
            req.file_path, req.paragraph_index, workspace_id
        )
        user_msg = ctx.append_user_message(
            req.user_message,
            referenced_file=req.file_path,
            referenced_paragraph=req.paragraph_index,
        )
        ctx.save_to_disk()

        # 3. 压缩检测：若发送前累积 token 已超阈值，先压缩旧消息（不含本轮 user 当前批）
        compressed_this_round = False
        if ctx.should_compress():
            compressed_this_round = await self._maybe_compress(ctx)
        if compressed_this_round:
            ctx.save_to_disk()
            yield {"type": "compressed", "session_id": ctx.session_id}

        # 4. 调用 LLM 流式（支持 Function Calling 工具调用）
        llm = get_llm_client()
        system_prompt = get_prompt(
            "writing_chat",
            "chat_system",
            context_block=context_block,
            language=req.language,
        )

        # 注入当前时间上下文，帮助 LLM 理解相对时间概念
        t = get_current_time_context()
        system_prompt += get_prompt(
            "writing_chat",
            "chat_system_time_context",
            date=t["date"],
            weekday=t["weekday"],
            time=t["time"],
        )

        # 助手指令注入 system prompt（不写入 user message，界面上不可见）
        if req.assistant_prompt:
            system_prompt = req.assistant_prompt + "\n\n" + system_prompt
        research_cfg = get_config().features.function_calling.scholarly_research
        if research_cfg.enabled and research_cfg.providers.openalex.enabled:
            system_prompt += "\n\n" + get_prompt(
                "writing_chat", "scholarly_research_tool_guide"
            )
        messages = [
            {"role": "system", "content": system_prompt}
        ] + ctx.to_llm_messages()
        assistant_id = uuid.uuid4().hex

        # 获取可用的 Function Calling 工具（搜索 + 项目浏览）
        tools = self._get_enabled_tools(workspace_id, req.web_search_enabled)
        persisted_scholarly_results: list[dict[str, Any]] = []
        persisted_scholarly_query = ""
        persisted_scholarly_total = 0

        # 工具调用循环：流式检测 tool_call，实时输出文本
        if tools:
            tool_messages = list(messages)  # 拷贝用于工具调用的 messages
            initial_msg_count = len(tool_messages)
            max_rounds = 3  # 防止无限工具调用循环
            _has_tool_calls = False  # 标记是否实际执行过工具调用
            full_content_parts: list[str] = []
            last_usage_tokens = 0

            for round_idx in range(max_rounds):
                round_content: list[str] = []
                round_tool_calls = None

                try:
                    async for chunk in llm.chat_stream_with_tools(
                        messages=tool_messages,
                        tools=tools,
                        temperature=get_config().llm.temperature,
                    ):
                        if chunk.content:
                            round_content.append(chunk.content)
                            yield {"type": "token", "content": chunk.content}
                        if chunk.tool_calls:
                            round_tool_calls = chunk.tool_calls
                        if chunk.usage is not None:
                            last_usage_tokens = chunk.usage.total_tokens
                except Exception as e:
                    self._logger.error(f"chat_stream_with_tools failed: {e}")
                    yield {"type": "error", "message": str(e)}
                    ctx.delete_message(user_msg.id)
                    ctx.save_to_disk()
                    return

                if not round_tool_calls:
                    # 无 tool_call，内容已在流中实时输出到前端
                    full_content_parts = round_content
                    break

                _has_tool_calls = True

                # 重建 assistant message（含 tool_calls）追加到 messages
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in round_tool_calls
                    ],
                }
                tool_messages.append(assistant_msg)

                for tc in round_tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except Exception:
                        tool_args = {}

                    # 推送工具状态事件（前端用于显示工具调用指示器 + 搜索词）
                    yield {
                        "type": "tool_status",
                        "tool": tool_name,
                        "label": self._get_tool_label(tool_name, tool_args),
                        "args": tool_args,
                    }

                    # ── request_clarification：向用户提问 ──────────────
                    if tool_name == "request_clarification":
                        clarify_id = uuid.uuid4().hex[:12]
                        yield {
                            "type": "clarification_request",
                            "clarify_id": clarify_id,
                            "tool": tool_name,
                            "question": tool_args.get("question", ""),
                            "options": tool_args.get("options"),
                        }
                        self._logger.info(
                            f"Clarification requested [clarify_id={clarify_id}]"
                        )
                        answer = await self._request_clarification(clarify_id)
                        self._logger.info(
                            f"Clarification answered [clarify_id={clarify_id}]"
                        )
                        tool_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": answer,
                            }
                        )
                        continue

                    # 敏感操作（edit_paragraph / undo_last_edit）检查是否需要用户确认
                    if (
                        self._is_sensitive_tool(tool_name)
                        and get_config().features.function_calling.edit_tools.require_confirmation
                    ):
                        pending_id = uuid.uuid4().hex[:12]
                        yield {
                            "type": "tool_pending",
                            "pending_id": pending_id,
                            "tool": tool_name,
                            "label": self._get_tool_label(tool_name, tool_args),
                            "args": tool_args,
                        }
                        confirmed_args = await self._request_tool_confirmation(
                            pending_id,
                            tool_name,
                            tool_args,
                        )
                        if confirmed_args is None:
                            self._logger.info(
                                f"Tool {tool_name} cancelled by user [pending={pending_id}]"
                            )
                            yield {
                                "type": "tool_cancelled",
                                "pending_id": pending_id,
                                "tool": tool_name,
                            }
                            # 添加空的 tool result，避免 LLM API 报未完成的 tool_call
                            tool_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": f"The user canceled the {tool_name} operation. Please skip this change.",
                                }
                            )
                            continue
                        # 使用可能被前端修改后的参数
                        tool_args = confirmed_args
                        self._logger.info(
                            f"Tool {tool_name} confirmed by user [pending={pending_id}]"
                        )

                    # 注入工作区上下文
                    tool_args["_workspace_id"] = workspace_id
                    try:
                        tool_result = await get_tool_registry().execute(
                            tool_name, tool_args
                        )
                        # 将搜索结果格式化为文本
                        if hasattr(tool_result, "format_for_llm"):
                            result_text = tool_result.format_for_llm()
                        else:
                            result_text = str(tool_result)

                        # 构建搜索结果事件
                        tool_event = {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result_count": (
                                len(tool_result.results)
                                if hasattr(tool_result, "results")
                                else (
                                    len(tool_result.works)
                                    if hasattr(tool_result, "works")
                                    else 0
                                )
                            ),
                        }
                        # 搜索结果摘要（供前端折叠框展示）
                        if tool_name == "web_search" and hasattr(
                            tool_result, "results"
                        ):
                            search_results = tool_result.results[:5]  # 限制最多5条
                            tool_event["search_results"] = [
                                {
                                    "index": i,
                                    "title": r.title,
                                    "snippet": r.snippet,
                                    "url": r.url,
                                }
                                for i, r in enumerate(search_results, 1)
                            ]
                            tool_event["query"] = tool_args.get("query", "")
                            deep_read_pages = sum(
                                1 for r in search_results if r.full_content
                            )
                            if deep_read_pages > 0:
                                tool_event["deep_read_pages"] = deep_read_pages
                            if (
                                hasattr(tool_result, "rewritten_queries")
                                and tool_result.rewritten_queries
                            ):
                                tool_event["rewritten_queries"] = (
                                    tool_result.rewritten_queries
                                )
                        if tool_name in {
                            "search_scholarly_works",
                            "find_related_works",
                            "search_research_data_resources",
                        }:
                            scholarly_results = getattr(
                                tool_result,
                                "results",
                                getattr(tool_result, "works", []),
                            )
                            tool_event["scholarly_results"] = [
                                {
                                    "id": result.openalex_id or result.doi,
                                    "title": result.title,
                                    "authors": [
                                        author.name for author in result.authors[:3]
                                    ],
                                    "year": result.year,
                                    "journal": result.journal,
                                    "citation_count": result.citation_count,
                                    "doi": result.doi,
                                    "is_oa": result.is_oa,
                                    "oa_url": result.oa_url,
                                    "work_type": result.work_type,
                                }
                                for result in scholarly_results[:5]
                            ]
                            tool_event["query"] = tool_args.get("query", "")
                            tool_event["total_estimated"] = getattr(
                                tool_result,
                                "total_estimated",
                                getattr(tool_result, "total", len(scholarly_results)),
                            )
                            existing_ids = {
                                item["id"] for item in persisted_scholarly_results
                            }
                            persisted_scholarly_results.extend(
                                item
                                for item in tool_event["scholarly_results"]
                                if item["id"] not in existing_ids
                            )
                            persisted_scholarly_query = tool_event["query"]
                            persisted_scholarly_total = max(
                                persisted_scholarly_total,
                                tool_event["total_estimated"],
                            )
                        yield tool_event
                    except DDGChallengeError as ce:
                        # DDG Challenge：推送 captcha 到前端，终止本轮搜索
                        self._logger.info(
                            f"DDG challenge | query={ce.query[:40]} | "
                            f"images={len(ce.images)} | cid={ce.challenge_id}"
                        )
                        yield {
                            "type": "captcha_challenge",
                            "challenge_id": ce.challenge_id,
                            "images": ce.images,
                            "query": ce.query,
                            "engine_name": ce.engine_name,
                            "instruction": ce.instruction,
                        }
                        # 终止工具调用循环，通知前端等待用户解决验证码
                        yield {
                            "type": "done",
                            "session_id": ctx.session_id,
                            "message_id": "",
                            "tokens": 0,
                            "captcha_pending": True,
                        }
                        ctx.save_to_disk()
                        return
                    except Exception as e:
                        self._logger.warning(f"Tool execution failed {tool_name}: {e}")
                        result_text = f"Tool execution failed: {e}"

                    # 追加 tool result 消息
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        }
                    )

                # 继续下一轮工具调用检测
                continue

            if _has_tool_calls:
                # 有工具调用：流式输出最终回复（不再传递工具，防止继续调用）
                full_content_parts = []
                last_usage_tokens = 0
                try:
                    async for chunk in llm.chat_stream_with_usage(
                        messages=tool_messages,
                        temperature=get_config().llm.temperature,
                    ):
                        if chunk.content:
                            full_content_parts.append(chunk.content)
                            yield {"type": "token", "content": chunk.content}
                        if chunk.usage is not None:
                            last_usage_tokens = chunk.usage.total_tokens
                except Exception as e:
                    self._logger.error(f"post-tool chat_stream_with_usage failed: {e}")
                    yield {"type": "error", "message": str(e)}
                    ctx.delete_message(user_msg.id)
                    ctx.save_to_disk()
                    return

                # 将工具调用/结果消息持久化到会话上下文
                new_tool_msgs = tool_messages[initial_msg_count:]
                if new_tool_msgs:
                    ctx.append_tool_messages(new_tool_msgs)

        else:
            # 无工具：原有纯流式逻辑
            full_content_parts = []
            last_usage_tokens = 0
            try:
                async for chunk in llm.chat_stream_with_usage(
                    messages=messages,
                    temperature=get_config().llm.temperature,
                ):
                    if chunk.content:
                        full_content_parts.append(chunk.content)
                        yield {"type": "token", "content": chunk.content}
                    if chunk.usage is not None:
                        last_usage_tokens = chunk.usage.total_tokens
            except Exception as e:
                self._logger.error(f"chat_stream_with_usage failed: {e}")
                yield {"type": "error", "message": str(e)}
                ctx.delete_message(user_msg.id)
                ctx.save_to_disk()
                return

        # 5. 写入 assistant 消息并落盘
        assistant_tokens = 0
        if last_usage_tokens > 0:
            assistant_tokens = last_usage_tokens
        assistant_msg = ctx.append_assistant_message(
            content="".join(full_content_parts),
            tokens=assistant_tokens,
            scholarly_results=persisted_scholarly_results,
            scholarly_query=persisted_scholarly_query,
            scholarly_total=persisted_scholarly_total,
        )
        ctx.update_last_message_tokens(last_usage_tokens)
        ctx.save_to_disk()

        yield {
            "type": "done",
            "session_id": ctx.session_id,
            "message_id": assistant_msg.id,
            "tokens": last_usage_tokens,
        }

    # ── 生成编辑（RS2，结构化 JSON） ───────────────────────────────────

    async def generate_edit(
        self, req: GenerateEditRequest, workspace_id: str = ""
    ) -> dict:
        """
        基于用户对话指令，对指定段落生成编辑操作建议。

        直接发送原始 LaTeX 源码给 LLM，LLM 返回修改后的 LaTeX 源码，
        无需 marker pipeline（writing_chat 是特例，不用 marker 保护）。
        """
        if self._no_api_key():
            raise RuntimeError("No API key configured")

        ctx = ConversationContext.load_from_disk(req.session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {req.session_id}")

        pm = get_project_manager_for_workspace(workspace_id)
        paragraphs = pm.get_paragraphs(req.file_path)
        if req.paragraph_index < 0 or req.paragraph_index >= len(paragraphs):
            raise IndexError(
                f"Paragraph index {req.paragraph_index} out of range "
                f"(0-{len(paragraphs) - 1})"
            )
        para = paragraphs[req.paragraph_index]
        if not para.text.strip():
            raise ValueError("Target paragraph is empty")

        llm = get_llm_client()
        system_prompt = get_prompt(
            "writing_chat",
            "edit_system",
            instruction=req.instruction,
            language=req.language,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Rewrite paragraph {req.paragraph_index} of the paper.\n\n"
                    f"RAW LATEX:\n{para.text}"
                ),
            },
        ]

        try:
            validated: LlmEditResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=LlmEditResult,
                max_retries=3,
                logger_name="writing_chat.service",
                temperature=0.3,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
        except ValueError as e:
            self._logger.warning(f"generate_edit validation exhausted: {e}")
            raise RuntimeError(f"Edit generation failed: {e}")
        except Exception as e:
            self._logger.error(f"generate_edit LLM call failed: {e}")
            raise RuntimeError(f"Edit generation failed: {e}")

        optimized_latex = validated.optimized
        reason = validated.reason

        # 构造 unified diff（基于原始 LaTeX）
        diff_text = "\n".join(
            difflib.unified_diff(
                para.text.splitlines(keepends=False),
                optimized_latex.splitlines(keepends=False),
                fromfile=f"paragraph-{req.paragraph_index}-original.tex",
                tofile=f"paragraph-{req.paragraph_index}-new.tex",
                lineterm="",
            )
        )

        edit_op = EditOp(
            file_path=req.file_path,
            paragraph_index=req.paragraph_index,
            original_latex=para.text,
            new_latex=optimized_latex,
            diff_text=diff_text,
            applied=False,
        )

        # 尝试附挂到一条最近的 assistant 消息（如有），否则就返回给 router
        attached = None
        for m in reversed(ctx.messages):
            if m.role == "assistant":
                ctx.attach_edit_op(m.id, edit_op)
                attached = m.id
                break
        ctx.save_to_disk()

        return {
            "session_id": ctx.session_id,
            "attached_message_id": attached,
            "edit_op": edit_op.model_dump(mode="json"),
            "reason": reason,
        }

    # ── 应用编辑 ──────────────────────────────────────────────────────

    def apply_edit(self, req: ApplyEditRequest, workspace_id: str = "") -> dict:
        ctx = ConversationContext.load_from_disk(req.session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {req.session_id}")

        edit_op: EditOp | None = None
        for m in ctx.messages:
            if m.id == req.edit_message_id and m.edit_op is not None:
                edit_op = m.edit_op
                break
        if edit_op is None:
            raise ValueError(f"No edit op attached to message {req.edit_message_id}")
        if edit_op.applied:
            return {"applied": True, "already_applied": True}

        # 优先使用请求中携带的 new_latex（用户手动编辑的内容）
        target_latex = req.new_latex if req.new_latex is not None else edit_op.new_latex
        pm = get_project_manager_for_workspace(workspace_id)
        pm.apply_change(
            edit_op.file_path,
            edit_op.paragraph_index,
            target_latex,
            expected_md5=req.expected_md5,
        )

        ctx.mark_edit_applied(req.edit_message_id)
        ctx.save_to_disk()
        self._logger.info(
            f"Applied edit: {edit_op.file_path} paragraph[{edit_op.paragraph_index}]"
        )
        return {"applied": True, "already_applied": False}

    # ── 删除 / 撤回 ────────────────────────────────────────────────────────

    def delete_message(self, req: DeleteMessageRequest) -> bool:
        ctx = ConversationContext.load_from_disk(req.session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {req.session_id}")
        ok = ctx.delete_message(req.message_id)
        if ok:
            ctx.save_to_disk()
        return ok

    def undo_message(self, req: UndoMessageRequest) -> dict:
        ctx = ConversationContext.load_from_disk(req.session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {req.session_id}")
        result = ctx.undo_message(req.message_id)
        if result["restored"] or result["removed"]:
            ctx.save_to_disk()
        return result

    # ── 内部辅助 ────────────────────────────────────────────────────────────

    def _build_context_block(
        self, file_path: str, paragraph_index: int | None, workspace_id: str = ""
    ) -> str:
        """构造塞给 chat system prompt 的文件段落上下文（原始 LaTeX 源码）。"""
        if not file_path:
            return "(no file selected)"
        # -1 表示显式关闭上下文锚点
        if paragraph_index == -1:
            return ""
        try:
            text = ConversationContext.build_file_context_text(
                file_path, paragraph_index, workspace_id
            )
        except Exception as e:
            return f"(failed to load file context: {e})"
        return text or "(file has no readable paragraphs)"

    def _get_enabled_tools(
        self, workspace_id: str = "", web_search_enabled: bool | None = None
    ) -> list[dict]:
        """
        获取当前可用的 Function Calling 工具列表。

        包含联网搜索工具 + 项目文件浏览工具（项目已打开时）。

        参数：
            workspace_id: 工作区 ID，用于检查项目是否已打开
            web_search_enabled: 请求级别的搜索开关覆盖
                - None: 跟随全局配置
                - True: 强制启用（即使全局关闭）
                - False: 强制禁用（即使全局开启）
        """
        cfg = get_config()
        tools: list[dict] = []

        # ── 澄清请求工具（始终可用） ─────────────────────────────────
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "request_clarification",
                    "description": (
                        "向用户提问以补充信息或做出选择。"
                        "当关键参数缺失、需求存在歧义、需要执行高风险操作前确认，"
                        "或多个合理方案会导致明显不同结果时使用。"
                        "用户回答将作为工具结果返回。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "你需要向用户提出的问题。必须清晰、具体，"
                                    "包含必要的上下文说明，让用户无需回忆对话历史就能理解。"
                                ),
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "可选的候选项列表。当问题可以通过有限选项回答时提供，"
                                    "用户必须从中选择一项。不提供此字段时用户可自由输入。"
                                ),
                            },
                        },
                        "required": ["question"],
                    },
                },
            }
        )

        # ── 联网搜索工具 ──────────────────────────────────────────────
        if (
            cfg.features.function_calling.web_search.enabled
            and cfg.features.writing_chat.web_search_enabled
            and web_search_enabled is not False
        ):
            search_manager = get_search_manager()
            tools.extend(search_manager.get_tools_schema())

        # ── 项目文件浏览工具 ──────────────────────────────────────────
        if cfg.features.function_calling.project_tools.enabled and workspace_id:
            try:
                pm = get_project_manager_for_workspace(workspace_id)
                if pm.is_open:
                    pt_mgr = get_project_tools_manager()
                    tools.extend(pt_mgr.get_tools_schema())
            except Exception as e:
                self._logger.warning(
                    "Failed to load project tools for workspace %s: %s",
                    workspace_id,
                    e,
                )

        # ── 段落编辑工具（从"建议者"升级为"执行者"） ─────────────────
        if cfg.features.function_calling.edit_tools.enabled and workspace_id:
            try:
                pm = get_project_manager_for_workspace(workspace_id)
                if pm.is_open:
                    et_mgr = get_edit_tools_manager()
                    tools.extend(et_mgr.get_tools_schema())
            except Exception as e:
                self._logger.warning(
                    "Failed to load edit tools for workspace %s: %s",
                    workspace_id,
                    e,
                )

        # ── 学术论文检索与引文追踪工具 ────────────────────────────────
        research_cfg = cfg.features.function_calling.scholarly_research
        if research_cfg.enabled and research_cfg.providers.openalex.enabled:
            try:
                from backend.utils.scholarly_research import get_research_manager

                tools.extend(get_research_manager().get_tools_schema())
            except Exception as e:
                self._logger.warning("Failed to load scholarly research tools: %s", e)

        return tools

    async def _maybe_compress(self, ctx: ConversationContext) -> bool:
        """
        调用 LLM 生成摘要并替换历史消息。

        失败时记录日志但不抛出，避免影响主聊天流。
        """
        messages_to_summarize = ctx.to_llm_messages()
        if len(messages_to_summarize) < 4:
            return False  # 太短不值得压缩

        llm = get_llm_client()
        cfg = get_config()
        transcript = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in messages_to_summarize
        )
        messages = [
            {"role": "system", "content": get_prompt("writing_chat", "summary_system")},
            {"role": "user", "content": f"CONVERSATION:\n{transcript}"},
        ]
        try:
            summary_text = await llm.chat(
                messages,
                temperature=cfg.features.writing_chat.summary_temperature,
                max_tokens=cfg.features.writing_chat.summary_max_tokens,
            )
        except Exception as e:
            self._logger.warning(f"Compression failed, skip: {e}")
            return False
        if not summary_text or not summary_text.strip():
            self._logger.warning("Compression failed, empty summary, skip")
            return False
        summary_text = summary_text.strip()

        # 估算摘要 token：粗略按字符近似
        summary_tokens = int(len(summary_text) / 3.5) + 50
        ctx.replace_with_summary(summary_text, summary_tokens)
        self._logger.info(
            f"Session {ctx.session_id} compressed to {summary_tokens} tokens, "
            f"snapshot saved"
        )
        return True


# ── 单例 ─────────────────────────────────────────────────────────────────


_service: WritingChatService | None = None


def get_writing_chat_service() -> WritingChatService:
    global _service
    if _service is None:
        _service = WritingChatService()
    return _service

"""写作助手服务：RS1 对话流与 Function Calling 工具编排。"""

import json
import re
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlparse, urlunparse

from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.core.tool_registry import get_tool_registry
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.writing_chat.context import (
    ConversationContext,
    _NOPROJECT_SCOPE,
    compute_scope,
)
from backend.modules.writing_chat.models import ChatSendRequest
from backend.utils.project_tools import get_project_tools_manager
from backend.utils.project_tools.edit_tools_manager import get_edit_tools_manager
from backend.utils.time_utils import get_current_time_context
from backend.utils.web_search import DDGChallengeError, get_search_manager

from . import _deps


class ConversationMixin:
    """RS1 主对话流与工具执行。"""

    _TOOL_LABELS: dict[str, str] = {
        "web_search": "searching",
        "open_page": "reading_page",
        "find_in_page": "searching_in_page",
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

    # 工具调用循环最大轮数：放长 agentic 搜索（搜索 → 读页 → 再搜索）
    _MAX_TOOL_ROUNDS = 6

    # 工具结果注入上下文的滚动字符预算：超出后裁剪最旧的工具调用对
    _MAX_TOOL_CONTEXT_CHARS = 60000

    # 工具循环单轮上下文超限的最大恢复尝试次数
    _MAX_CONTEXT_RETRIES = 2

    # 摘要请求的最大尝试次数（超限时扩大保留预算重试）
    _MAX_SUMMARY_RETRIES = 3

    # 正文内 Markdown 链接提取正则（URL 不含空格与右括号）
    _MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")

    @staticmethod
    def _normalize_citation_url(url: str) -> str:
        """URL 规范化，用于引用校验（去 fragment、小写 host、去尾部斜杠）。"""
        try:
            p = urlparse(url)
            return urlunparse(
                (
                    p.scheme.lower(),
                    p.netloc.lower(),
                    p.path.rstrip("/") or "/",
                    "",
                    "",
                    "",
                )
            )
        except Exception:
            return url

    @classmethod
    def _validate_citations(
        cls, content: str, allowed_urls: set[str]
    ) -> tuple[list[str], str]:
        """
        校验回复中的 Markdown 链接是否来自当轮工具返回的真实来源。

        返回：
            (unverified_urls, note)
        """
        if not content or not allowed_urls:
            return [], ""
        allowed = {cls._normalize_citation_url(u) for u in allowed_urls}
        found = cls._MARKDOWN_LINK_RE.findall(content)
        unverified = [u for u in found if cls._normalize_citation_url(u) not in allowed]
        if not unverified:
            return [], ""
        return unverified, "".join(f"[{i}] {u}\n" for i, u in enumerate(unverified, 1))

    @staticmethod
    def _trim_tool_messages(
        messages: list[dict], max_chars: int = _MAX_TOOL_CONTEXT_CHARS
    ) -> list[dict]:
        """
        裁剪工具证据，使注入 LLM 的工具结果总量尽量不超过字符预算。

        规则：
        - 只裁剪"完整"的工具调用对（assistant tool_calls 后紧跟数量匹配的
          tool 消息），避免破坏正在执行的同一轮多工具调用
        - 从最旧的一对开始删，但始终保留当前轮（最后一条 assistant+tool_calls）
          ——最新证据是生成最终回复的直接依据，预算超限时作为软上限接受
        """
        total = sum(
            len(str(m.get("content", ""))) for m in messages if m.get("role") == "tool"
        )
        while total > max_chars:
            # 当前轮（最后一条 assistant+tool_calls）即使结果尚未收齐也不可裁剪，
            # 否则会拆散正在执行的同轮多工具调用
            last_tool_assistant_idx = -1
            for i, m in enumerate(messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    last_tool_assistant_idx = i
            candidates: list[tuple[int, int, int]] = []
            for i, m in enumerate(messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    tool_calls = m["tool_calls"]
                    j = i + 1
                    count = 0
                    while j < len(messages) and messages[j].get("role") == "tool":
                        count += 1
                        j += 1
                    if count == len(tool_calls):
                        pair_chars = sum(
                            len(str(messages[k].get("content", "")))
                            for k in range(i + 1, j)
                        )
                        candidates.append((i, j, pair_chars))
            if not candidates:
                break
            removed = False
            for start, end, pair_chars in candidates:
                if start == last_tool_assistant_idx:
                    continue
                total -= pair_chars
                del messages[start:end]
                removed = True
                break
            if not removed:
                break
        return messages

    @staticmethod
    def _get_tool_label(tool_name: str, _tool_args: dict) -> str:
        return ConversationMixin._TOOL_LABELS.get(tool_name, "running_tool")

    @staticmethod
    def _is_sensitive_tool(tool_name: str) -> bool:
        """判断工具是否属于敏感操作（需要用户确认）。"""
        return tool_name in ("edit_paragraph", "undo_last_edit")

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

    async def send(
        self, req: ChatSendRequest, workspace_id: str = ""
    ) -> AsyncIterator[dict]:
        """主对话流入口：同一会话的 send 串行执行，防止并发落盘丢消息。"""
        if self._no_api_key():
            yield {"type": "error", "message": "No API key configured"}
            return
        if req.session_id:
            async with self._session_lock(req.session_id):
                async for event in self._send_locked(req, workspace_id):
                    yield event
        else:
            async for event in self._send_locked(req, workspace_id):
                yield event

    async def _send_locked(
        self, req: ChatSendRequest, workspace_id: str = ""
    ) -> AsyncIterator[dict]:
        """
        主对话流实现（调用方已持有会话级锁）：发送用户消息、流式接收
        LLM 回复、必要时执行压缩。

        返回的 SSE 事件结构见模块 docstring。
        """

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

        # 2. 压缩检测：若发送前累积 token 已超阈值，先压缩旧消息（不含本轮
        #    user 当前批），确保本轮 user 消息始终以真实 user 角色进入 LLM
        ctx.file_path = req.file_path or ctx.file_path
        compressed_this_round = False
        if await ctx.should_compress_async():
            compressed_this_round = await self._maybe_compress(ctx)
        if compressed_this_round:
            ctx.save_to_disk()
            yield {"type": "compressed", "session_id": ctx.session_id}

        # 3. 构造上下文锚点文本 + 追加 user 消息
        context_block = self._build_context_block(
            req.file_path, req.paragraph_index, workspace_id
        )
        user_msg = ctx.append_user_message(
            req.user_message,
            referenced_file=req.file_path,
            referenced_paragraph=req.paragraph_index,
        )
        ctx.save_to_disk()

        # 4. 调用 LLM 流式（支持 Function Calling 工具调用）
        llm = _deps.get_llm_client()
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
        persisted_search_results: list[dict[str, Any]] = []
        persisted_search_query = ""
        persisted_deep_read_pages = 0
        persisted_rewritten_queries: list[str] = []
        persisted_source_urls: set[str] = set()
        persisted_opened_pages: set[str] = set()
        citation_note = ""
        unverified_citations: list[str] = []

        # 工具调用循环：流式检测 tool_call，实时输出文本
        if tools:
            tool_messages = list(messages)  # 拷贝用于工具调用的 messages
            # 引用方式记录本轮回新增的 tool 消息，裁剪/删除不影响持久化
            persisted_tool_msg_refs: list[dict[str, Any]] = []
            max_rounds = self._MAX_TOOL_ROUNDS  # 防止无限工具调用循环
            # max_rounds 用尽且最后一轮仍有工具调用时置 True
            need_final_regeneration = False
            full_content_parts: list[str] = []
            last_usage_tokens = 0

            for round_idx in range(max_rounds):
                round_content: list[str] = []
                round_tool_calls = None
                retry_skip_chars = 0

                # 新一轮工具轮次的文本流从零开始累积：通知前端重置流式消息内容，
                # 确保实时展示与最终落盘内容一致（前面轮次在工具调用前输出的
                # 思考片段不写入会话 JSON）
                if round_idx > 0:
                    yield {"type": "round_start"}

                # 上下文超限恢复：裁剪/压缩后重发同一轮，避免整轮失败
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        async for chunk in llm.chat_stream_with_tools(
                            messages=tool_messages,
                            tools=tools,
                            temperature=get_config().llm.temperature,
                        ):
                            if chunk.content:
                                content = chunk.content
                                # 重试轮次跳过已实时输出过的前缀，避免前端重复显示
                                if retry_skip_chars > 0:
                                    if retry_skip_chars >= len(content):
                                        retry_skip_chars -= len(content)
                                        content = ""
                                    else:
                                        content = content[retry_skip_chars:]
                                        retry_skip_chars = 0
                                if content:
                                    round_content.append(content)
                                    yield {
                                        "type": "token",
                                        "content": content,
                                    }
                            if chunk.tool_calls:
                                round_tool_calls = chunk.tool_calls
                            if chunk.usage is not None:
                                last_usage_tokens = chunk.usage.total_tokens
                        break
                    except Exception as e:
                        is_overflow = self._is_context_overflow_error(e)
                        if not is_overflow or attempt > self._MAX_CONTEXT_RETRIES:
                            self._logger.error(f"chat_stream_with_tools failed: {e}")
                            yield {"type": "error", "message": str(e)}
                            if not is_overflow:
                                # 非超限错误（认证/网络等）维持原有行为：
                                # 移除本轮 user 消息，便于用户重新发送。
                                # 超限重试耗尽时保留用户消息，避免静默丢消息。
                                ctx.delete_message(user_msg.id)
                            ctx.save_to_disk()
                            return
                        self._logger.warning(
                            "Context overflow in tool round %s (attempt %s): %s",
                            round_idx,
                            attempt,
                            e,
                        )
                        recovered = await self._recover_from_context_overflow(
                            ctx,
                            system_prompt,
                            persisted_tool_msg_refs,
                            tool_messages,
                        )
                        if recovered is None:
                            # 恢复失败：保留用户消息，不静默删除
                            yield {"type": "error", "message": str(e)}
                            ctx.save_to_disk()
                            return
                        (
                            tool_messages,
                            persisted_tool_msg_refs,
                            compressed_flag,
                        ) = recovered
                        if compressed_flag:
                            compressed_this_round = True
                            yield {
                                "type": "compressed",
                                "session_id": ctx.session_id,
                            }
                        retry_skip_chars = sum(len(x) for x in round_content)
                        round_content = []

                if not round_tool_calls:
                    # 无 tool_call：该轮内容即最终回复，已在流中实时输出到前端，
                    # 直接落盘，不再重复生成
                    full_content_parts = round_content
                    break

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
                persisted_tool_msg_refs.append(assistant_msg)

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
                        tool_msg: dict[str, Any] = {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": answer,
                        }
                        tool_messages.append(tool_msg)
                        persisted_tool_msg_refs.append(tool_msg)
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
                            tool_msg = {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"The user canceled the {tool_name} operation. Please skip this change.",
                            }
                            tool_messages.append(tool_msg)
                            persisted_tool_msg_refs.append(tool_msg)
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
                            # 持久化到 assistant 消息：与 tool_result 事件同构，
                            # 刷新后搜索卡片与实时展示一致。前端多轮搜索覆盖显示，
                            # 这里同样覆盖保留最后一轮的结果。
                            persisted_search_results = tool_event["search_results"]
                            persisted_search_query = tool_event["query"]
                            persisted_deep_read_pages = tool_event.get(
                                "deep_read_pages", 0
                            )
                            persisted_rewritten_queries = tool_event.get(
                                "rewritten_queries", []
                            )
                            for r in tool_result.results:
                                if r.url:
                                    persisted_source_urls.add(r.url)
                        if tool_name == "open_page":
                            url = getattr(tool_result, "url", "")
                            if url and not getattr(tool_result, "error", ""):
                                persisted_source_urls.add(url)
                                if url not in persisted_opened_pages:
                                    persisted_opened_pages.add(url)
                                    persisted_deep_read_pages += 1
                                tool_event["url"] = url
                                tool_event["title"] = getattr(tool_result, "title", "")
                                tool_event["chars"] = len(
                                    getattr(tool_result, "content", "")
                                )
                        if tool_name == "find_in_page":
                            url = getattr(tool_result, "url", "")
                            if url and not getattr(tool_result, "error", ""):
                                persisted_source_urls.add(url)
                                tool_event["url"] = url
                                tool_event["match_count"] = len(
                                    getattr(tool_result, "matches", [])
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
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                    tool_messages.append(tool_msg)
                    persisted_tool_msg_refs.append(tool_msg)
                    # 证据滚动预算：裁剪最旧的工具调用对
                    tool_messages = self._trim_tool_messages(tool_messages)

                # 已达最大轮次且本轮仍请求工具调用：循环结束后需再生成最终回复
                if round_idx == max_rounds - 1:
                    need_final_regeneration = True
                # 继续下一轮工具调用检测
                continue

            if need_final_regeneration:
                # 有工具调用：流式输出最终回复（不再传递工具，防止继续调用）
                full_content_parts = []
                last_usage_tokens = 0
                yield {"type": "round_start"}
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

            # 将工具调用/结果消息持久化到会话上下文（任一成功路径都会执行）
            if persisted_tool_msg_refs:
                ctx.append_tool_messages(persisted_tool_msg_refs)

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

        # 5. 引用校验：回复中的链接必须来自当轮工具返回的真实来源
        content = "".join(full_content_parts)
        if persisted_source_urls:
            unverified_citations, citation_note = self._validate_citations(
                content, persisted_source_urls
            )
            if unverified_citations:
                self._logger.warning(
                    "Unverified citations in response: %s",
                    ", ".join(unverified_citations[:5]),
                )

        # 6. 写入 assistant 消息并落盘
        assistant_tokens = 0
        if last_usage_tokens > 0:
            assistant_tokens = last_usage_tokens
        assistant_msg = ctx.append_assistant_message(
            content=content,
            tokens=assistant_tokens,
            scholarly_results=persisted_scholarly_results,
            scholarly_query=persisted_scholarly_query,
            scholarly_total=persisted_scholarly_total,
            search_results=persisted_search_results,
            search_query=persisted_search_query,
            deep_read_pages=persisted_deep_read_pages,
            rewritten_queries=persisted_rewritten_queries,
            citation_note=citation_note,
            unverified_citations=unverified_citations,
        )
        ctx.update_last_message_tokens(last_usage_tokens)
        ctx.save_to_disk()

        if citation_note:
            yield {
                "type": "citation_note",
                "note": citation_note,
                "unverified": unverified_citations,
            }

        yield {
            "type": "done",
            "session_id": ctx.session_id,
            "message_id": assistant_msg.id,
            "tokens": last_usage_tokens,
        }

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

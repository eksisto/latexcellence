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

import difflib
import json
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
from backend.modules.writing_chat.context import ConversationContext, _NOPROJECT_SCOPE, compute_scope
from backend.modules.writing_chat.models import (
    ApplyEditRequest,
    ChatMessage,
    ChatSendRequest,
    DeleteMessageRequest,
    EditOp,
    GenerateEditRequest,
    LlmEditResult,
    LlmSummaryResult,
    UndoMessageRequest,
)


# ── Service ────────────────────────────────────────────────────────────────


class WritingChatService:
    """
    写作助手 Chat 服务（单例）。
    """

    def __init__(self):
        self._logger = get_logger("writing_chat.service")

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

    async def send(self, req: ChatSendRequest, workspace_id: str = "") -> AsyncIterator[dict]:
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
                file_path=req.file_path, scope=scope)

        # 2. 构造上下文锚点文本 + 追加 user 消息
        ctx.file_path = req.file_path or ctx.file_path
        ctx.save_to_disk()
        context_block = self._build_context_block(
            req.file_path, req.paragraph_index, workspace_id)
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
            "writing_chat", "chat_system", context_block=context_block)

        # 注入当前时间上下文，帮助 LLM 理解相对时间概念
        t = get_current_time_context()
        system_prompt += get_prompt(
            "writing_chat", "chat_system_time_context",
            date=t["date"], weekday=t["weekday"], time=t["time"],
        )

        # 助手指令注入 system prompt（不写入 user message，界面上不可见）
        if req.assistant_prompt:
            system_prompt = req.assistant_prompt + "\n\n" + system_prompt
        messages = [{"role": "system", "content": system_prompt}
                    ] + ctx.to_llm_messages()
        assistant_id = uuid.uuid4().hex

        # 检查是否启用联网搜索工具
        tools = self._get_enabled_tools(req.web_search_enabled)

        # 工具调用循环：先非流式检测 tool_call，再流式输出最终回复
        if tools:
            tool_messages = list(messages)  # 拷贝用于工具调用的 messages
            max_rounds = 3  # 防止无限工具调用循环

            for round_idx in range(max_rounds):
                try:
                    first_response = await llm.chat_with_tools(
                        messages=tool_messages,
                        tools=tools,
                        temperature=get_config().llm.temperature,
                    )
                except Exception as e:
                    self._logger.error(f"chat_with_tools failed: {e}")
                    yield {"type": "error", "message": str(e)}
                    ctx.delete_message(user_msg.id)
                    ctx.save_to_disk()
                    return

                choice = first_response.choices[0]
                tool_calls = choice.message.tool_calls

                if not tool_calls:
                    # 无 tool_call，退化为普通流式（用非流式返回的内容直接输出）
                    content = choice.message.content or ""
                    if content:
                        full_content_parts_t: list[str] = [content]
                        yield {"type": "token", "content": content}
                    break

                # 有 tool_call：执行工具，将结果追加到 messages
                # 将 assistant message（含 tool_calls）转为 dict 追加
                tool_messages.append(choice.message.model_dump())

                for tc in tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except Exception:
                        tool_args = {}

                    # 推送搜索状态事件
                    if tool_name == "web_search":
                        yield {
                            "type": "searching",
                            "query": tool_args.get("query", ""),
                            "engine": "",
                        }

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
                                if hasattr(tool_result, "results") else 0
                            ),
                        }
                        # 搜索结果摘要（供前端折叠框展示）
                        if tool_name == "web_search" and hasattr(tool_result, "results"):
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
                        self._logger.warning(f"工具执行失败 {tool_name}: {e}")
                        result_text = f"工具执行失败：{e}"

                    # 追加 tool result 消息
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

                # 继续下一轮工具调用检测
                continue

            # 工具调用结束后，流式输出最终回复
            full_content_parts: list[str] = []
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
                self._logger.error(
                    f"post-tool chat_stream_with_usage failed: {e}")
                yield {"type": "error", "message": str(e)}
                ctx.delete_message(user_msg.id)
                ctx.save_to_disk()
                return

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

    async def generate_edit(self, req: GenerateEditRequest, workspace_id: str = "") -> dict:
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
            "writing_chat", "edit_system", instruction=req.instruction)
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
            raise ValueError(
                f"No edit op attached to message {req.edit_message_id}")
        if edit_op.applied:
            return {"applied": True, "already_applied": True}

        # 优先使用请求中携带的 new_latex（用户手动编辑的内容）
        target_latex = req.new_latex if req.new_latex is not None else edit_op.new_latex
        pm = get_project_manager_for_workspace(workspace_id)
        pm.apply_change(edit_op.file_path,
                        edit_op.paragraph_index, target_latex)

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

    def _build_context_block(self, file_path: str, paragraph_index: int | None, workspace_id: str = "") -> str:
        """构造塞给 chat system prompt 的文件段落上下文（原始 LaTeX 源码）。"""
        if not file_path:
            return "(no file selected)"
        try:
            text = ConversationContext.build_file_context_text(
                file_path, paragraph_index, workspace_id)
        except Exception as e:
            return f"(failed to load file context: {e})"
        return text or "(file has no readable paragraphs)"

    def _get_enabled_tools(self, web_search_enabled: bool | None = None) -> list[dict]:
        """
        获取当前可用的 Function Calling 工具列表。

        参数：
            web_search_enabled: 请求级别的搜索开关覆盖
                - None: 跟随全局配置
                - True: 强制启用（即使全局关闭）
                - False: 强制禁用（即使全局开启）
        """
        cfg = get_config()

        # 全局搜索功能未启用时，不提供工具
        if not cfg.features.web_search.enabled:
            return []

        # writing_chat 模块级开关
        if not cfg.features.writing_chat.web_search_enabled:
            return []

        # 请求级别覆盖
        if web_search_enabled is False:
            return []

        # 获取搜索工具 schema
        search_manager = get_search_manager()
        return search_manager.get_tools_schema()

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
            {"role": "system", "content": get_prompt(
                "writing_chat", "summary_system")},
            {"role": "user", "content": f"CONVERSATION:\n{transcript}"},
        ]
        try:
            validated: LlmSummaryResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=LlmSummaryResult,
                max_retries=3,
                logger_name="writing_chat.service",
                temperature=cfg.features.writing_chat.summary_temperature,
                max_tokens=cfg.features.writing_chat.summary_max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            self._logger.warning(f"Compression failed, skip: {e}")
            return False

        # 估算摘要 token：粗略按字符近似
        summary_tokens = int(len(validated.summary) / 3.5) + 50
        ctx.replace_with_summary(validated.summary, summary_tokens)
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

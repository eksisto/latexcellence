"""编辑器侧 AI 助手服务（editor-design §5.2 / §7，阶段四）。

复用边界（方案 §7.1）：
    - 直接复用 ConversationContext 会话持久化 / token 计数 / 压缩、
      SSE 流式协议、ToolRegistry 只读三件套、多用户 LLM 治理与配额
    - 新增：编辑器缓冲区协议端点、自适应上下文注入（§7.3）、
      建议提取与消息级应用标记

AI 建议**不经后端落盘**：建议以结构化形式随 SSE 返回并随消息持久化
（仅供会话历史展示），由前端应用到 CM6 缓冲区，最终经 save-file 单一
路径落盘（决策 Q22）。

阶段五b 确认流：联网/学术工具在编辑器侧逐次确认（先登记 pending 再
yield `tool_pending`，流内等待 confirm/cancel 端点决议；拒绝 / TTL 过期
注入取消语义 tool result），复用 Writing Chat 的 pending 基础设施与端点。

模块级纯函数 build_editor_context() / extract_suggestions() 供单测直测。
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.core.config import get_config
from backend.core.multiuser.quota import (
    checkpoint_derived_usage_for_current_user,
    enforce_derived_quota_for_current_user,
)
from backend.core.prompts import get_prompt
from backend.core.tool_registry import get_tool_registry
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.writing_chat.context import ConversationContext
from backend.modules.writing_chat.models import (
    EDITOR_BUFFER_MAX_CHARS,
    EDITOR_SELECTION_MAX_CHARS,
    EDITOR_USER_MESSAGE_MAX_CHARS,
    EditorSendRequest,
)
from backend.utils.project_tools import get_project_tools_manager
from backend.utils.tex_compile import sanitize_output_paths
from backend.utils.web_search import DDGChallengeError, get_search_manager

from . import _deps

# ── 上下文注入常量（editor-design §7.3 / 决策 Q24） ────────────────────────────

# 小文件阈值：行数低于该值时全文携带
SMALL_FILE_LINE_THRESHOLD = 400
# 大文件时光标邻域半径：前后各约 50 行
CURSOR_CONTEXT_RADIUS = 50
# 大文件时章节结构摘要的条目数上限
OUTLINE_MAX_ENTRIES = 60
# 章节标题在摘要中的截断长度
OUTLINE_TITLE_MAX_CHARS = 80

# 章节结构识别：仅锚定行首的分节命令（避免灾难性回溯，AGENTS.md 正则规范）
_SECTION_RE = re.compile(r"^\\(part|chapter|section|subsection|subsubsection)\*?\s*\{")

# 建议提取：```latex 围栏 → snippet；```diff / ```patch 围栏 → diff
_SNIPPET_LANGS = {"latex", "tex"}
_DIFF_LANGS = {"diff", "patch"}

# 联网搜索 / 学术检索工具名分组（执行期源开关复核的判定依据，见
# _tool_source_gate_open）；_EDITOR_TOOL_NAMES 由两组派生，保持单一来源。
_WEB_TOOL_NAMES = frozenset(
    {"web_search", "open_page", "read_page_sections", "find_in_page"}
)
_SCHOLARLY_TOOL_NAMES = frozenset(
    {
        "search_scholarly_works",
        "get_work_details",
        "find_related_works",
        "get_full_text_or_sections",
        "search_research_data_resources",
    }
)

# 确认流分组（阶段五b，editor-experience-design §4.5 / §5"经确认执行"）：编辑器
# 侧对联网/学术工具逐次确认——每次调用产生一个 pending 交互，用户确认
# 后才执行、拒绝/超时后模型收到取消语义。这是比 Writing Chat 主对话
# （这些工具直通、零 pending）更严格的新策略，属编辑器侧计划本意；
# 项目只读三件套维持直通，不产生 pending。
_CONFIRMABLE_TOOL_NAMES = _WEB_TOOL_NAMES | _SCHOLARLY_TOOL_NAMES

# 取消语义 tool result 注入文本（模型侧消息，不面向用户、无需 i18n）：
# 明确告知用户拒绝了本次检索且不得原样重试，模型可基于已有信息继续
# 回答或换策略。
_TOOL_CANCELLED_TEXT = (
    "The user declined the {tool_name} operation. Do not retry the same "
    "call; continue with the information you already have, or ask the "
    "user how to proceed."
)

# 只读工具白名单（阶段五a 扩展）：项目只读三件套（决策 Q26）+ 联网搜索
# + 学术检索（editor-experience-design §4.5，以 Writing Chat 主对话注册表为准）。
# 写入一律走缓冲区协议——edit_paragraph / undo_last_edit 明确不入白名单。
# 该表是执行前硬校验的依据之一：ToolRegistry.execute 只查运行时 enabled
# 标志、不复查 schema 来源，缺此校验时模型幻觉调用即可执行全局任意
# 已启用工具（含写盘）。联网/学术工具另有执行期源开关复核。
_EDITOR_TOOL_NAMES = (
    "list_files",
    "read_file_content",
    "search_across_files",
    # 联网搜索（function-calling-tools.md「联网搜索」；read_page_sections
    # 仅 structured 策略下进入 schema，白名单无条件列入、过滤天然生效）
    *_WEB_TOOL_NAMES,
    # 学术检索（function-calling-tools.md「学术检索与引文追踪工具」）
    *_SCHOLARLY_TOOL_NAMES,
)


def _tool_source_gate_open(tool_name: str) -> bool:
    """执行期复核联网/学术工具的源开关（阶段五a 审查加固）。

    白名单只约束工具名集合，源开关约束"该工具此刻是否允许出网/出库"：
    ToolRegistry.execute 只查运行时 enabled 标志、不复查 config_path，
    若只在 schema 收集层把关，开关关闭后模型幻觉调用仍可直达执行器
    产生真实网络请求。故执行前按与 _get_editor_tools 相同口径复核：
    web 源为全局开关 AND Writing Chat 灰度总闸，学术源为
    scholarly_research.enabled AND providers.openalex.enabled（与
    conversation.py 同款双重把关）。项目工具源只读且注入 schema 时已过
    workspace 门，维持阶段四现状不在此复核。
    """
    cfg = get_config()
    if tool_name in _WEB_TOOL_NAMES:
        return bool(
            cfg.features.function_calling.web_search.enabled
            and cfg.features.writing_chat.web_search_enabled
        )
    if tool_name in _SCHOLARLY_TOOL_NAMES:
        research_cfg = cfg.features.function_calling.scholarly_research
        return bool(research_cfg.enabled and research_cfg.providers.openalex.enabled)
    return True


def tool_failure_text(
    tool_name: str, exc: BaseException, workspace_id: str = ""
) -> str:
    """工具失败信息入 LLM 消息前的统一构造口（审查遗留项 C2）。

    失败详情帮助模型区分可自纠错误（路径非法 / 文件不存在等），但异
    常原文可能携带服务器绝对路径。多用户模式下按项目根目录前缀改写为
    相对形式；workspace 缺失或项目根不可解析时降级为无详情稳定文案
    （默认不泄漏）；单用户模式保留原文（零行为变化）。完整原文已由调
    用方记入服务端日志。
    """
    if get_config().multi_user.enabled:
        root: Path | None = None
        if workspace_id:
            try:
                root = Path(get_project_manager_for_workspace(workspace_id).root_path)
            except Exception:
                root = None
        if root is None:
            return "Tool execution failed."
        sanitized = sanitize_output_paths(f"{type(exc).__name__}: {exc}", root)
        return f"Tool execution failed: {sanitized}"
    return f"Tool execution failed: {exc}"


def _xml_attr(s: str) -> str:
    """转义 XML 属性中的特殊字符。"""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _outline_entry(line_no: int, line: str) -> str | None:
    """从分节命令行提取大纲条目；非分节命令返回 None。"""
    match = _SECTION_RE.match(line)
    if not match:
        return None
    title = line[match.end() :].split("}", 1)[0].strip()
    if len(title) > OUTLINE_TITLE_MAX_CHARS:
        title = title[:OUTLINE_TITLE_MAX_CHARS] + "…"
    return f"L{line_no}: \\{match.group(1)} {{{title}}}"


def build_outline(lines: list[str]) -> list[str]:
    """提取章节结构摘要（行号 + 标题），超出上限后停止收集。"""
    outline: list[str] = []
    for i, line in enumerate(lines, start=1):
        entry = _outline_entry(i, line)
        if entry:
            outline.append(entry)
            if len(outline) >= OUTLINE_MAX_ENTRIES:
                break
    return outline


def _window_text(lines: list[str], center: int, radius: int) -> tuple[int, int]:
    """返回光标邻域窗口的 [start_line, end_line]（1-based，含端点）。"""
    total = len(lines)
    start = max(1, (center or 1) - radius)
    end = min(total, (center or 1) + radius)
    return start, end


def _render_buffer_lines(lines: list[str], start: int, end: int) -> str:
    """渲染带 1-based 行号的缓冲区片段。"""
    return "\n".join(f"{no}: {lines[no - 1]}" for no in range(start, end + 1))


def build_editor_context(
    file_path: str,
    buffer: str,
    cursor_line: int | None = None,
    selection: str = "",
    include_full_file: bool = False,
) -> str:
    """
    构造编辑器助手 system prompt 的 <editor_state> XML 信封（§7.3）。

    自适应策略：
        - 行数 < SMALL_FILE_LINE_THRESHOLD 或显式 include_full_file：全文携带；
        - 大文件：章节结构摘要 + 光标前后各约 CURSOR_CONTEXT_RADIUS 行 +
          当前选区，并以 context_note 说明省略情况。

    选区始终单独携带（大文件时选区可能落在窗口之外）。缓冲区正文刻意
    不做 XML 转义——信封由 LLM 作为文本消费而非机器解析，转义会破坏
    LaTeX 可读性（与 writing_chat 段落信封同一取舍）。
    """
    lines = buffer.split("\n")
    total = len(lines)

    parts: list[str] = [
        "<editor_state>",
        f'<document source="{_xml_attr(file_path or "(unsaved buffer)")}">',
        f"<total_lines>{total}</total_lines>",
    ]
    if cursor_line is not None and cursor_line >= 1:
        parts.append(f'<cursor line="{min(cursor_line, max(total, 1))}"/>')

    selection = selection or ""
    if selection.strip():
        parts.append("<selection>")
        parts.append(selection)
        parts.append("</selection>")

    full_file = include_full_file or total < SMALL_FILE_LINE_THRESHOLD
    if full_file:
        parts.append('<buffer_content full="true">')
        parts.append(_render_buffer_lines(lines, 1, total))
        parts.append("</buffer_content>")
    else:
        outline = build_outline(lines)
        parts.append("<section_outline>")
        parts.extend(outline or ["(no sectioning commands found)"])
        parts.append("</section_outline>")
        start, end = _window_text(lines, cursor_line or 1, CURSOR_CONTEXT_RADIUS)
        parts.append(
            f'<buffer_content full="false" from_line="{start}" to_line="{end}">'
        )
        parts.append(_render_buffer_lines(lines, start, end))
        parts.append("</buffer_content>")

    if not full_file:
        omitted_head = start - 1
        omitted_tail = total - end
        parts.append(
            "<context_note>"
            "The buffer is large, so only the section outline and the lines "
            f"around the cursor are shown ({omitted_head} lines before the "
            f"shown window and {omitted_tail} lines after it are omitted). "
            "Line numbers refer to the full buffer. Use the "
            "read_file_content tool to retrieve other sections if needed."
            "</context_note>"
        )

    parts.append("</document>")
    parts.append("</editor_state>")
    return "\n".join(parts)


def extract_suggestions(content: str) -> list[dict[str, str]]:
    """
    从 assistant 回复中提取建议（editor-design §7.2 三种形态的数据来源）。

    规则：
        - ```latex / ```tex 围栏 → kind="snippet"（插入光标处 / 替换选区）
        - ```diff / ```patch 围栏 → kind="diff"（unified diff 卡片逐卡审阅）
        - 其他语言标签或裸围栏不视为建议
        - 提取按出现顺序编号（index）

    逐行扫描围栏状态机实现，避免对任意 LLM 文本使用嵌套量词正则。
    """
    suggestions: list[dict[str, str]] = []
    lang = ""
    buf: list[str] = []
    inside = False
    for line in (content or "").split("\n"):
        stripped = line.strip()
        if not inside and stripped.startswith("```"):
            candidate = stripped[3:].strip().lower()
            if candidate in _SNIPPET_LANGS or candidate in _DIFF_LANGS:
                inside = True
                lang = candidate
                buf = []
            continue
        if inside and stripped.startswith("```"):
            kind = "diff" if lang in _DIFF_LANGS else "snippet"
            suggestions.append(
                {
                    "kind": kind,
                    "language": "diff" if kind == "diff" else "latex",
                    "content": "\n".join(buf),
                    "index": len(suggestions),
                }
            )
            inside = False
            lang = ""
            buf = []
            continue
        if inside:
            buf.append(line)
    # 未闭合围栏：丢弃（不完整的建议不可直接应用）
    return suggestions


class EditorChatMixin:
    """编辑器侧 AI 助手对话流（/api/writing-chat/editor/* 的业务实现）。"""

    @staticmethod
    def _enforce_request_limits(req: EditorSendRequest) -> None:
        """请求体长度上限把关（超限统一抛结构化 content_too_long 413）。

        在 Pydantic 模型上用 Field(max_length=)/field_validator 硬拦截会被
        FastAPI 转成裸 422（detail 为数组），前端 translateError 无法映射；
        此处以结构化 {error, message} 抛出，SSE 兜底分支原样透传给前端。
        """
        limits: list[tuple[str, str, int]] = [
            ("buffer", "Editor buffer", EDITOR_BUFFER_MAX_CHARS),
            ("selection", "Selection", EDITOR_SELECTION_MAX_CHARS),
            ("user_message", "User message", EDITOR_USER_MESSAGE_MAX_CHARS),
        ]
        for field_name, label, max_chars in limits:
            value = getattr(req, field_name) or ""
            if len(value) > max_chars:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "content_too_long",
                        "message": f"{label} is {len(value)} characters, "
                        f"exceeding the maximum of {max_chars} characters",
                    },
                )

    def _get_editor_tools(self, workspace_id: str) -> list[dict]:
        """编辑器助手工具 schema 汇聚（阶段五a，editor-experience-design §4.5）。

        三源汇聚、按源独立容错（单一源故障只降级该源，不连坐其余）：
            - 联网搜索：双重开关把关（全局 web_search.enabled AND Writing
              Chat 侧 web_search_enabled 灰度总闸，镜像 conversation.py）；
            - 学术检索：scholarly_research.enabled AND providers.openalex.enabled
              （get_research_manager 依赖 lifespan 初始化的共享 httpx 客户端，
              未初始化时抛 RuntimeError，按源捕获降级为空）；
            - 项目工具：project_tools.enabled 且项目已打开（现状不变）。

        功能开关在 schema 收集层把关，工具循环执行前另按同口径复核
        （_tool_source_gate_open）：ToolRegistry.execute 不复查
        config_path，仅靠 schema 不注入挡不住模型幻觉调用。返回前按
        _EDITOR_TOOL_NAMES 过滤（read_page_sections 仅 structured 策略
        下由 web 源产出）。
        """
        cfg = get_config()
        schemas: list[dict] = []

        # ── 联网搜索源（不需要 workspace / 项目打开） ──────────────────
        if (
            cfg.features.function_calling.web_search.enabled
            and cfg.features.writing_chat.web_search_enabled
        ):
            try:
                schemas.extend(get_search_manager().get_tools_schema())
            except Exception as e:
                self._logger.warning("Failed to load editor web search tools: %s", e)

        # ── 学术检索源（同样不依赖 workspace） ─────────────────────────
        research_cfg = cfg.features.function_calling.scholarly_research
        if research_cfg.enabled and research_cfg.providers.openalex.enabled:
            try:
                from backend.utils.scholarly_research import get_research_manager

                schemas.extend(get_research_manager().get_tools_schema())
            except Exception as e:
                self._logger.warning(
                    "Failed to load editor scholarly research tools: %s", e
                )

        # ── 项目工具源（workspace 门只约束本源） ───────────────────────
        if cfg.features.function_calling.project_tools.enabled and workspace_id:
            try:
                pm = get_project_manager_for_workspace(workspace_id)
                if pm.is_open:
                    schemas.extend(get_project_tools_manager().get_tools_schema())
            except Exception as e:
                self._logger.warning(
                    "Failed to load editor assistant tools for workspace %s: %s",
                    workspace_id,
                    e,
                )

        return [
            schema
            for schema in schemas
            if schema.get("function", {}).get("name") in _EDITOR_TOOL_NAMES
        ]

    async def editor_send(
        self, req: EditorSendRequest, workspace_id: str = ""
    ) -> AsyncIterator[dict]:
        """编辑器助手主对话流：同一会话串行执行，防止并发落盘丢消息。"""
        self._enforce_request_limits(req)
        if self._no_api_key():
            yield {"type": "error", "message": "No API key configured"}
            return
        if req.session_id:
            async with self._session_lock(req.session_id):
                async for event in self._editor_send_locked(req, workspace_id):
                    yield event
        else:
            async for event in self._editor_send_locked(req, workspace_id):
                yield event

    async def _editor_send_locked(
        self, req: EditorSendRequest, workspace_id: str
    ) -> AsyncIterator[dict]:
        """对话流实现（调用方已持有会话级锁）。"""
        scope = self._get_scope(workspace_id)
        created_new_session = False
        ctx = (
            await ConversationContext.load_from_disk_async(req.session_id)
            if req.session_id
            else None
        )
        if ctx is None:
            # 多用户模式：新会话前检查衍生数据配额（超限 413，语义与
            # writing_chat 一致）；单用户模式静默跳过
            await asyncio.to_thread(
                enforce_derived_quota_for_current_user, 0, "writing_chat_sessions"
            )
            ctx = ConversationContext.create_session(
                file_path=req.file_path or None, scope=scope
            )
            created_new_session = True

        ctx.file_path = req.file_path or ctx.file_path
        compressed_this_round = False
        if await ctx.should_compress_async():
            compressed_this_round = await self._maybe_compress(ctx)
        if compressed_this_round:
            await ctx.save_to_disk_async()
            yield {"type": "compressed", "session_id": ctx.session_id}

        context_block = build_editor_context(
            req.file_path,
            req.buffer,
            cursor_line=req.cursor_line,
            selection=req.selection,
            include_full_file=req.include_full_file,
        )
        user_msg = ctx.append_user_message(
            req.user_message, referenced_file=req.file_path
        )
        await ctx.save_to_disk_async()
        if created_new_session:
            await asyncio.to_thread(
                checkpoint_derived_usage_for_current_user, "writing_chat_sessions"
            )

        llm = _deps.get_llm_client()
        system_prompt = get_prompt(
            "editor_assistant",
            "chat_system",
            context_block=context_block,
            language=req.language,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ] + ctx.to_llm_messages()

        tools = self._get_editor_tools(workspace_id)
        full_content_parts: list[str] = []
        last_usage_tokens = 0
        llm_failed = False

        if tools:
            tool_messages = list(messages)
            persisted_tool_msg_refs: list[dict[str, Any]] = []
            # 达到最大工具轮次且最后一轮仍在请求工具调用时置 True：
            # 与 writing_chat 同语义，不再传递工具流式生成最终回复
            need_final_regeneration = False
            for round_idx in range(self._MAX_TOOL_ROUNDS):
                round_content: list[str] = []
                round_tool_calls = None
                if round_idx > 0:
                    yield {"type": "round_start"}

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
                    # 与 writing_chat 非超限错误同语义：直接终止本轮并移除
                    # 本轮 user 消息，便于用户重发。严禁落入下方"轮次用尽"
                    # 兜底重发——那会对已失败的流静默重试，且重试成功后内容
                    # 会因 llm_failed 被整体丢弃、留下无 user 提问的孤儿工具
                    # 消息，破坏会话历史一致性。
                    self._logger.error(f"editor chat_stream_with_tools failed: {e}")
                    yield {"type": "error", "message": str(e)}
                    ctx.delete_message(user_msg.id)
                    await ctx.save_to_disk_async()
                    return

                if not round_tool_calls:
                    full_content_parts = round_content
                    break

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

                    # 执行前白名单硬校验（阶段五a 既有缺陷必修）：
                    # ToolRegistry 全局存有 edit_paragraph 等写工具且
                    # execute 不复查工具来源，模型幻觉调用将绕过编辑器
                    # "只读 + 缓冲区协议"边界直达写盘。不在白名单、或属
                    # 联网/学术分组但源开关已关（_tool_source_gate_open，
                    # 防 schema 关闭后幻觉调用仍产生真实网络请求）的一律
                    # 不执行、不推送 tool_status（无真实执行可指示），
                    # 注入模型可自纠的失败 tool result 并记日志。
                    if (
                        tool_name not in _EDITOR_TOOL_NAMES
                        or not _tool_source_gate_open(tool_name)
                    ):
                        self._logger.warning(
                            "Editor assistant blocked non-whitelisted "
                            f"tool call: {tool_name}"
                        )
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": (
                                f'Tool "{tool_name}" is not available to the '
                                "editor assistant. Use only the tools provided "
                                "in this conversation."
                            ),
                        }
                        tool_messages.append(tool_msg)
                        persisted_tool_msg_refs.append(tool_msg)
                        tool_messages = self._trim_tool_messages(tool_messages)
                        continue

                    # 确认流（阶段五b）：联网/学术工具逐次确认。**先登记
                    # pending 再 yield tool_pending**——事件到达前端后用户
                    # 可能在亚秒内点击确认，登记在后会使 confirm 端点查无
                    # 此 pending（Writing Chat 既有次序的时序缺陷，编辑器
                    # 侧不复现）。等待在流内挂起（confirm/cancel 端点经
                    # Event / pending_interactions 表续流），TTL 过期等同
                    # 拒绝（_await_tool_confirmation 返回 None）。
                    if tool_name in _CONFIRMABLE_TOOL_NAMES:
                        pending_id = uuid.uuid4().hex[:12]
                        await self._register_tool_confirmation(
                            pending_id, tool_name, tool_args, workspace_id
                        )
                        yield {
                            "type": "tool_pending",
                            "pending_id": pending_id,
                            "tool": tool_name,
                            "label": self._get_tool_label(tool_name, tool_args),
                            "args": tool_args,
                        }
                        confirmed_args = await self._await_tool_confirmation(
                            pending_id, tool_name, tool_args
                        )
                        if confirmed_args is None:
                            self._logger.info(
                                f"Editor assistant tool {tool_name} declined "
                                f"by user [pending={pending_id}]"
                            )
                            yield {
                                "type": "tool_cancelled",
                                "pending_id": pending_id,
                                "tool": tool_name,
                            }
                            # 取消语义 tool result：tool_call 配对完整，
                            # 模型明确知道用户拒绝了本次检索
                            tool_msg = {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": _TOOL_CANCELLED_TEXT.format(
                                    tool_name=tool_name
                                ),
                            }
                            tool_messages.append(tool_msg)
                            persisted_tool_msg_refs.append(tool_msg)
                            tool_messages = self._trim_tool_messages(tool_messages)
                            continue
                        # 使用可能被前端修改后的参数
                        tool_args = confirmed_args
                        self._logger.info(
                            f"Editor assistant tool {tool_name} confirmed "
                            f"by user [pending={pending_id}]"
                        )

                    yield {
                        "type": "tool_status",
                        "tool": tool_name,
                        "label": self._get_tool_label(tool_name, tool_args),
                        "args": tool_args,
                    }
                    tool_args["_workspace_id"] = workspace_id
                    try:
                        tool_result = await get_tool_registry().execute(
                            tool_name, tool_args
                        )
                        result_text = (
                            tool_result.format_for_llm()
                            if hasattr(tool_result, "format_for_llm")
                            else str(tool_result)
                        )
                    except DDGChallengeError as ce:
                        # DDG 挑战（镜像 conversation.py）：推送 captcha 事件
                        # 后携带 captcha_pending 语义终流。本轮工具消息不持久
                        # 化（与前端"删占位"语义对齐），挑战解决后由用户手动
                        # 重发原提问；user 消息此前已落盘，回存保持会话一致。
                        self._logger.info(
                            "Editor assistant DDG challenge | "
                            f"query={ce.query[:40]} | images={len(ce.images)} | "
                            f"cid={ce.challenge_id}"
                        )
                        yield {
                            "type": "captcha_challenge",
                            "challenge_id": ce.challenge_id,
                            "images": ce.images,
                            "query": ce.query,
                            "engine_name": ce.engine_name,
                            "instruction": ce.instruction,
                        }
                        yield {
                            "type": "done",
                            "session_id": ctx.session_id,
                            "message_id": "",
                            "tokens": 0,
                            "captcha_pending": True,
                        }
                        await ctx.save_to_disk_async()
                        return
                    except Exception as e:
                        self._logger.warning(
                            f"Editor assistant tool failed {tool_name}: {e}"
                        )
                        result_text = tool_failure_text(tool_name, e, workspace_id)
                    yield {"type": "tool_result", "tool": tool_name}
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                    tool_messages.append(tool_msg)
                    persisted_tool_msg_refs.append(tool_msg)
                    tool_messages = self._trim_tool_messages(tool_messages)
                # 已达最大轮次且本轮仍请求工具调用：循环结束后需再生成最终回复
                if round_idx == self._MAX_TOOL_ROUNDS - 1:
                    need_final_regeneration = True
                continue

            if persisted_tool_msg_refs:
                ctx.append_tool_messages(persisted_tool_msg_refs)

            if need_final_regeneration:
                # 工具轮次用尽且最后一轮仍在请求工具调用：与 writing_chat
                # 同语义，不再传递工具流式生成最终回复，防止空回复落盘
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
                    self._logger.error(
                        f"editor post-tool chat_stream_with_usage failed: {e}"
                    )
                    yield {"type": "error", "message": str(e)}
                    ctx.delete_message(user_msg.id)
                    await ctx.save_to_disk_async()
                    return
        else:
            # 无项目工具：纯流式
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
                self._logger.error(f"editor chat_stream_with_usage failed: {e}")
                yield {"type": "error", "message": str(e)}
                llm_failed = True

        if llm_failed:
            # 与 writing_chat 同语义：移除本轮 user 消息，便于用户重发
            ctx.delete_message(user_msg.id)
            await ctx.save_to_disk_async()
            return

        content = "".join(full_content_parts)
        suggestion_dicts = extract_suggestions(content)
        assistant_msg = ctx.append_assistant_message(
            content=content,
            tokens=last_usage_tokens,
            suggestions=suggestion_dicts,
        )
        ctx.update_last_message_tokens(last_usage_tokens)
        await ctx.save_to_disk_async()

        yield {
            "type": "done",
            "session_id": ctx.session_id,
            "message_id": assistant_msg.id,
            "tokens": last_usage_tokens,
            "suggestions": suggestion_dicts,
        }

    async def apply_suggestion(
        self,
        session_id: str,
        message_id: str,
        suggestion_index: int | None = None,
    ) -> dict:
        """记录建议应用标记（前端已把建议应用到 CM6 缓冲区之后调用）。

        suggestion_index 非 None 时做 index 级标记：applied_suggestions
        去重追加，覆盖该消息全部建议 index 后才整体置位 suggestion_applied；
        为 None 保持旧契约直接消息级置位（向后兼容）。
        """
        async with self._session_lock(session_id):
            ctx = await ConversationContext.load_from_disk_async(session_id)
            if ctx is None:
                raise ValueError(f"Session not found: {session_id}")
            if not ctx.mark_suggestion_applied(message_id, index=suggestion_index):
                raise IndexError(f"Message not found: {message_id}")
            applied_suggestions: list[int] = []
            suggestion_applied = False
            for m in ctx.messages:
                if m.id == message_id:
                    applied_suggestions = list(m.applied_suggestions)
                    suggestion_applied = m.suggestion_applied
                    break
            await ctx.save_to_disk_async()
            return {
                "status": "ok",
                "session_id": session_id,
                "message_id": message_id,
                # index 级部分应用时为 False：仅当 applied_suggestions 覆盖
                # 该消息全部建议 index（或走消息级旧行为）才置位
                "suggestion_applied": suggestion_applied,
                "applied_suggestions": applied_suggestions,
            }

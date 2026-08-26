"""写作助手服务：RS2 编辑建议、应用与消息删除。"""

import asyncio
import difflib
from typing import Any

from backend.core import access_control
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.writing_chat.context import ConversationContext
from backend.modules.writing_chat.models import (
    ApplyEditRequest,
    DeleteMessageRequest,
    EditOp,
    GenerateEditRequest,
)
from backend.utils.latex_json_guard import validate_latex_field
from backend.utils.latex_literals import extract_delimited_block

from . import _deps


class EditMixin:
    """RS2 编辑协议与消息删除。"""

    async def generate_edit(
        self, req: GenerateEditRequest, workspace_id: str = ""
    ) -> dict:
        """基于用户对话指令生成编辑建议（会话写操作串行化）。"""
        if self._no_api_key():
            raise RuntimeError("No API key configured")
        async with self._session_lock(req.session_id):
            return await self._generate_edit_locked(req, workspace_id)

    async def _generate_edit_locked(
        self, req: GenerateEditRequest, workspace_id: str = ""
    ) -> dict:
        """
        生成编辑操作建议实现（调用方已持有会话级锁）。

        基于用户对话指令，对指定段落生成编辑操作建议。

        直接发送原始 LaTeX 源码给 LLM，LLM 返回修改后的 LaTeX 源码，
        无需 marker pipeline（writing_chat 是特例，不用 marker 保护）。

        LLM 输出走分隔符协议（<<<LATEX>>>...<<<END>>>）而非 JSON：
        反斜杠原样传输，避免 JSON 转义静默损坏；original 由后端从段落
        原文填充，不再要求 LLM 回显，减少误报与 token 消耗。
        """
        ctx = await ConversationContext.load_from_disk_async(req.session_id)
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

        llm = _deps.get_llm_client()
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

        optimized_latex, reason = await self._generate_edit_with_llm(llm, messages)

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
        await ctx.save_to_disk_async()

        return {
            "session_id": ctx.session_id,
            "attached_message_id": attached,
            "edit_op": edit_op.model_dump(mode="json"),
            "reason": reason,
        }

    # ── 分隔符协议：提取 + 校验 + 重试 ───────────────────────────────

    _MAX_EDIT_RETRIES = 3

    async def _generate_edit_with_llm(
        self, llm, messages: list[dict[str, Any]]
    ) -> tuple[str, str]:
        """
        按分隔符协议调用 LLM 生成编辑结果。

        返回 (optimized_latex, reason)。提取的 LaTeX 过内容校验
        （非 JSON 宽松模式，放行 \\t / \\r），失败时把错误回喂 LLM 重试，
        最多 3 次；重试耗尽后抛 RuntimeError（与 style_modify 行为一致）。
        """
        max_tokens = max(get_config().llm.max_tokens, 4096)
        last_error: str | None = None
        for attempt in range(self._MAX_EDIT_RETRIES):
            try:
                response_text = await llm.chat(
                    messages,
                    temperature=0.3,
                    max_tokens=max_tokens,
                )
                optimized = self._extract_edit_latex_block(response_text)
                if optimized is None:
                    raise ValueError(
                        "Response did not contain a <<<LATEX>>>...<<<END>>> block. "
                        "Output the complete rewritten paragraph strictly between "
                        "those two markers, with NO JSON and NO markdown fences."
                    )
                if not optimized.strip():
                    raise ValueError(
                        "The <<<LATEX>>> block is empty. Output the complete "
                        "rewritten paragraph between the markers."
                    )
                validate_latex_field(optimized, relaxed=True)
                reason = self._extract_edit_reason_block(response_text) or ""
                self._logger.info(
                    f"generate_edit succeeded: {len(optimized)} chars "
                    f"on attempt {attempt + 1}"
                )
                return optimized, reason

            except ValueError as e:
                last_error = str(e)
                self._logger.warning(
                    f"generate_edit protocol/validation failed on attempt "
                    f"{attempt + 1}/{self._MAX_EDIT_RETRIES}: {last_error}"
                )
            except Exception as e:
                self._logger.error(f"generate_edit LLM call failed: {e}")
                raise RuntimeError(f"Edit generation failed: {e}")

            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed the output protocol. "
                        f"Error: {last_error}\n\n"
                        "Please output exactly:\n"
                        "<<<LATEX>>>\n<complete rewritten paragraph as raw LaTeX>\n"
                        "<<<END>>>\n"
                        "<<<REASON>>>\n<brief explanation>\n<<<END>>>"
                    ),
                }
            ]

        raise RuntimeError(
            f"Edit generation failed: all {self._MAX_EDIT_RETRIES} attempts "
            f"failed. Last error: {last_error}"
        )

    @staticmethod
    def _extract_edit_latex_block(response_text: str) -> str | None:
        """提取 <<<LATEX>>>...<<<END>>> 块内容；缺失返回 None。

        逐字内容（\\verb / \\lstinline 等）中的 <<<END>>> 不是协议结束，
        提取由 latex_literals.extract_delimited_block 统一处理。
        """
        block = extract_delimited_block(response_text, "<<<LATEX>>>", "<<<END>>>")
        if block is None:
            return None
        return block.strip()

    @staticmethod
    def _extract_edit_reason_block(response_text: str) -> str | None:
        """提取 <<<REASON>>>...<<<END>>> 块内容；缺失返回 None。"""
        block = extract_delimited_block(response_text, "<<<REASON>>>", "<<<END>>>")
        if block is None:
            return None
        return block.strip()

    # ── 应用编辑 ──────────────────────────────────────────────────────

    async def apply_edit(self, req: ApplyEditRequest, workspace_id: str = "") -> dict:
        """应用编辑操作（会话写操作串行化）。"""
        async with self._session_lock(req.session_id):
            # 会话 JSON 读写（含 fsync）与 apply_change 的段落解析/落盘
            # 一并入线程池，避免阻塞事件循环（B1 异步化收尾）
            result = await asyncio.to_thread(self._apply_edit_locked, req, workspace_id)
        if not result.get("already_applied"):
            # 多用户模式：写盘后刷新项目用量记账（单用户无绑定，静默跳过）
            await access_control.refresh_workspace_project_usage(workspace_id)
        return result

    def _apply_edit_locked(self, req: ApplyEditRequest, workspace_id: str = "") -> dict:
        """应用编辑操作实现（调用方已持有会话级锁）。"""
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

    # ── 删除消息 ────────────────────────────────────────────────────────────

    async def delete_message(self, req: DeleteMessageRequest) -> dict:
        """删除消息（会话写操作串行化）。"""
        async with self._session_lock(req.session_id):
            # 同 apply_edit：会话读写入线程池，不阻塞事件循环
            return await asyncio.to_thread(self._delete_message_locked, req)

    def _delete_message_locked(self, req: DeleteMessageRequest) -> dict:
        """删除消息实现（调用方已持有会话级锁）。"""
        ctx = ConversationContext.load_from_disk(req.session_id)
        if ctx is None:
            raise ValueError(f"Session not found: {req.session_id}")
        result = ctx.delete_message(req.message_id)
        if result["restored"] or result["removed"]:
            ctx.save_to_disk()
        return result

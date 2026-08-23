"""
LaTeX 样式修改业务逻辑。

流程：
    1. 接收用户粘贴的 LaTeX 源码和自然语言描述
    2. 将 LaTeX 源码和描述发送给 LLM，要求 LLM 根据描述修改 LaTeX
    3. 返回修改后的 LaTeX 代码

项目 Agent 模式：
    创建独立 session，真实项目只读；Agent 工具将候选文件写入 session overlay，
    在 shadow 项目编译，生成 proposal 后由用户确认，再通过 LatexProject 批量
    事务写回并支持撤销。旧粘贴代码流程保持不变。

设计说明：
    - 不使用 Marker 保护策略。样式修改需要 LLM 理解并修改 LaTeX 结构
      （如添加列对齐、调整表格位置、加粗文字等），因此 LLM 看到的是原始 LaTeX 源码。
    - LaTeX 不放在 JSON 字符串中传输——JSON 会把未转义的 \\rho 解释为回车等
      （静默损坏）。改用分隔符协议（<<<LATEX>>>...<<<END>>>）让反斜杠原样传输，
      再用 latex_json_guard 内容校验（宽松模式，放行 \\t / \\r）拦截结构损坏，
      失败时回喂 LLM 重试。
"""

import asyncio
from typing import Any, AsyncIterator

from backend.core import access_control
from backend.core.config import get_config
from backend.core.llm_client import get_llm_client
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.style_modify.agent import get_style_modify_agent
from backend.modules.style_modify.session import get_session_store
from backend.utils.latex_json_guard import validate_latex_field
from backend.utils.latex_literals import extract_delimited_block

# 分隔符协议：LaTeX 以纯文本形式夹在标记之间，避免 JSON 反斜杠转义损坏
_MAX_RETRIES = 3


class StyleModifyService:
    """LaTeX 样式修改服务。"""

    def __init__(self):
        self._logger = get_logger("style_modify")
        self._store = get_session_store()

    # ── 粘贴代码模式（旧接口） ──────────────────────────────────────

    async def modify(self, latex: str, description: str) -> dict[str, Any]:
        """
        根据用户描述修改 LaTeX 代码。

        参数：
            latex: 原始 LaTeX 源码
            description: 用户对修改要求的自然语言描述

        返回：
            { latex: "修改后的 LaTeX 源码" }
        """
        cfg = get_config()
        if not cfg.llm.api_key:
            self._logger.warning("No API key configured")
            raise RuntimeError("No API key configured")

        llm = get_llm_client()
        return await self._modify_with_llm(llm, latex, description)

    async def _modify_with_llm(
        self, llm, latex: str, description: str
    ) -> dict[str, Any]:
        """按分隔符协议调用 LLM，提取并校验 LaTeX，失败时回喂重试。"""
        user_message = (
            f"Here is my LaTeX code:\n\n```latex\n{latex}\n```\n\n"
            f"Please make the following changes:\n{description}\n\n"
            "Return the complete modified LaTeX code between the markers:\n"
            "<<<LATEX>>>\n<complete modified LaTeX code>\n<<<END>>>"
        )

        messages = [
            {"role": "system", "content": get_prompt("style_modify", "system")},
            {"role": "user", "content": user_message},
        ]

        max_tokens = max(get_config().llm.max_tokens, 8192)
        last_error: str | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response_text = await llm.chat(
                    messages,
                    temperature=0.3,
                    max_tokens=max_tokens,
                )
                latex_out = self._extract_latex_block(response_text)
                if latex_out is None:
                    raise ValueError(
                        "Response did not contain a <<<LATEX>>>...<<<END>>> block. "
                        "Output the complete modified LaTeX code strictly between "
                        "those two markers, with NO JSON and NO markdown fences."
                    )
                if not latex_out.strip():
                    self._logger.warning("LLM returned empty latex block")
                    return {"latex": latex}
                validate_latex_field(latex_out, relaxed=True)

                self._logger.info(
                    f"Style modify completed: {len(latex)} -> {len(latex_out)} chars"
                )
                return {"latex": latex_out}

            except ValueError as e:
                last_error = str(e)
            except Exception as e:
                self._logger.error(f"Style modify error: {e}")
                raise RuntimeError(f"Style modification failed: {e}")

            self._logger.warning(
                f"Style modify protocol/validation failed on attempt "
                f"{attempt + 1}/{_MAX_RETRIES}: {last_error}"
            )
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed the output protocol. "
                        f"Error: {last_error}\n\n"
                        "Please output exactly:\n"
                        "<<<LATEX>>>\n<complete modified LaTeX code>\n<<<END>>>"
                    ),
                }
            ]

        raise RuntimeError(
            f"Style modification failed: all {_MAX_RETRIES} attempts "
            f"failed. Last error: {last_error}"
        )

    @staticmethod
    def _extract_latex_block(response_text: str) -> str | None:
        """提取 <<<LATEX>>>...<<<END>>> 块；缺失返回 None。

        逐字内容（\\verb / \\lstinline 等）中的 <<<END>>> 不是协议结束，
        提取由 latex_literals.extract_delimited_block 统一处理。
        """
        block = extract_delimited_block(response_text, "<<<LATEX>>>", "<<<END>>>")
        if block is None:
            return None
        return block.strip()

    # ── 项目 Agent 模式 ─────────────────────────────────────────────

    async def agent_run(
        self,
        workspace_id: str,
        description: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """创建 session 并通过 SSE 执行项目 Agent。"""
        cfg = get_config()
        project = get_project_manager_for_workspace(workspace_id)
        if not project.is_open:
            yield {"type": "error", "message": "No project opened"}
            return
        if not cfg.features.style_modify.agent.enabled:
            yield {"type": "error", "message": "Style modify agent is disabled"}
            return
        if not cfg.llm.api_key and llm is None:
            yield {"type": "error", "message": "No API key configured"}
            return

        # create_session 内含衍生配额检查（目录递归扫描）+ 会话目录创建 +
        # meta 落盘，均为阻塞 I/O，整体入线程池避免阻塞事件循环
        session_id = await asyncio.to_thread(
            self._store.create_session,
            workspace_id,
            project.root_path,
            description,
            language,
        )
        yield {"type": "session", "session_id": session_id}
        agent = get_style_modify_agent()
        async with self._store.guard(session_id):
            async for event in agent.run(
                session_id,
                workspace_id,
                description,
                language,
                llm=llm,
            ):
                yield event

    async def agent_refine(
        self,
        session_id: str,
        workspace_id: str,
        message: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """基于最新 proposal 追加要求并流式生成新 revision。"""
        meta = self._store.load_meta(session_id)
        self._store.require_workspace(meta, workspace_id)
        if meta.proposal is None:
            yield {"type": "error", "message": "No proposal to refine"}
            return
        agent = get_style_modify_agent()
        async with self._store.guard(session_id):
            async for event in agent.refine(
                session_id,
                workspace_id,
                message,
                language,
                llm=llm,
            ):
                yield event

    def get_agent_session(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        return self._store.get_session(session_id, workspace_id)

    def get_agent_trace(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        return self._store.get_trace(session_id, workspace_id)

    async def apply_agent_proposal(
        self,
        session_id: str,
        workspace_id: str,
        revision: int,
    ) -> dict[str, Any]:
        meta = self._store.load_meta(session_id)
        self._store.require_workspace(meta, workspace_id)
        async with self._store.guard(session_id):
            operation = await asyncio.to_thread(
                self._store.apply_proposal,
                session_id,
                workspace_id,
                revision,
            )
        # 多用户模式：批量事务写盘后刷新项目用量记账（单用户无绑定，静默跳过）
        await access_control.refresh_workspace_project_usage(workspace_id)
        return operation.model_dump()

    async def undo_agent_operation(
        self,
        operation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        operation = await asyncio.to_thread(
            self._store.undo_operation,
            operation_id,
            workspace_id,
        )
        # 撤销同样改变项目磁盘占用，记账需同步回刷
        await access_control.refresh_workspace_project_usage(workspace_id)
        return operation.model_dump()

    async def delete_agent_session(
        self,
        session_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        deleted = await asyncio.to_thread(
            self._store.delete_session,
            session_id,
            workspace_id,
        )
        return {"deleted": deleted}

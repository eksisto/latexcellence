"""
LaTeX 样式修改业务逻辑。

流程：
    1. 接收用户粘贴的 LaTeX 源码和自然语言描述
    2. 将 LaTeX 源码和描述发送给 LLM，要求 LLM 根据描述修改 LaTeX
    3. 返回修改后的 LaTeX 代码

设计说明：
    - 不使用 Marker 保护策略。样式修改需要 LLM 理解并修改 LaTeX 结构
      （如添加列对齐、调整表格位置、加粗文字等），因此 LLM 看到的是原始 LaTeX 源码。
    - LaTeX 不放在 JSON 字符串中传输——JSON 会把未转义的 \\rho 解释为回车等
      （静默损坏）。改用分隔符协议（<<<LATEX>>>...<<<END>>>）让反斜杠原样传输，
      再用 latex_json_guard 内容校验拦截结构损坏，失败时回喂 LLM 重试。
"""

import re
from typing import Any

from backend.core.config import get_config
from backend.core.llm_client import get_llm_client
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.latex_json_guard import validate_latex_field

# 分隔符协议：LaTeX 以纯文本形式夹在标记之间，避免 JSON 反斜杠转义损坏
_LATEX_BLOCK_RE = re.compile(r"<<<LATEX>>>(.*?)<<<END>>>", re.DOTALL | re.IGNORECASE)
_MAX_RETRIES = 3


class StyleModifyService:
    """LaTeX 样式修改服务。"""

    def __init__(self):
        self._logger = get_logger("style_modify")

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

        last_error: str | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response_text = await llm.chat(
                    messages,
                    temperature=0.3,
                    max_tokens=4096,
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
                validate_latex_field(latex_out)

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
        """提取 <<<LATEX>>>...<<<END>>> 块；缺失返回 None。"""
        match = _LATEX_BLOCK_RE.search(response_text)
        if not match:
            return None
        return match.group(1).strip()

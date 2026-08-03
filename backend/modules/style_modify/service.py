"""
LaTeX 样式修改业务逻辑。

流程：
    1. 接收用户粘贴的 LaTeX 源码和自然语言描述
    2. 将 LaTeX 源码和描述发送给 LLM，要求 LLM 根据描述修改 LaTeX
    3. 返回修改后的 LaTeX 代码

设计说明：
    - 不使用 Marker 保护策略。样式修改需要 LLM 理解并修改 LaTeX 结构
      （如添加列对齐、调整表格位置、加粗文字等），因此 LLM 看到的是原始 LaTeX 源码。
    - 通过 Pydantic 校验和 retry_with_validation 确保 LLM 输出格式正确。
"""

from typing import Any

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation
from backend.utils.latex_json_guard import LatexText


class StyleModifyResult(BaseModel):
    """LLM 样式修改输出"""

    latex: LatexText


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
        user_message = (
            f"Here is my LaTeX code:\n\n```latex\n{latex}\n```\n\n"
            f"Please make the following changes:\n{description}\n\n"
            f"Return the complete modified LaTeX code in the 'latex' field."
        )

        messages = [
            {"role": "system", "content": get_prompt("style_modify", "system")},
            {"role": "user", "content": user_message},
        ]

        try:
            validated: StyleModifyResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=StyleModifyResult,
                max_retries=3,
                logger_name="style_modify",
                temperature=0.3,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

            result = validated.model_dump()

            if not result["latex"].strip():
                self._logger.warning("LLM returned empty latex field")
                return {"latex": latex}

            self._logger.info(
                f"Style modify completed: {len(latex)} -> {len(result['latex'])} chars"
            )
            return result

        except ValueError as e:
            self._logger.error(f"Style modify failed after retries: {e}")
            raise RuntimeError(f"Style modification failed: {e}")
        except Exception as e:
            self._logger.error(f"Style modify error: {e}")
            raise RuntimeError(f"Style modification failed: {e}")

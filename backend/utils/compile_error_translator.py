"""
LaTeX 编译错误 LLM 翻译工具。

职责：
    1. 将已过滤（如 texfot）或原始的编译日志发送给 LLM 分析
    2. 调用 LLM 将英文错误信息翻译为通俗易懂的中文解释和修复建议

供 compile 模块（自动编译）和 git_diff 模块（差异高亮）复用。

当调用方已通过 tex_compile 获取了 texfot 过滤后的输出时，
通过 filtered_output 参数直接传入，可跳过 extract_key_errors 正则过滤。
"""

import re

from pydantic import BaseModel, ConfigDict

# default 槽位（multi-model §6）：编译错误翻译为低频辅助调用；
# get_llm_client 即 get_client_for("default") 的薄别名（P2 起语义等价）
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation

logger = get_logger(__name__)


class ErrorTranslation(BaseModel):
    """LLM 返回的错误翻译结构。"""

    model_config = ConfigDict(extra="forbid")

    explanation: str
    fixes: list[str] = []


_LATEX_ERROR_PATTERN = re.compile(
    r"^! .*|^Runaway argument\?|^Emergency stop|^l\.\d+|"
    r"^No file .*|"
    r"^LaTeX Error:|^Package .* Error:|"
    r"^Missing .* inserted",
    re.MULTILINE,
)


def extract_key_errors(log_text: str, max_chars: int = 3000) -> str:
    """从日志中提取关键错误信息，避免全文过长。"""
    matches = _LATEX_ERROR_PATTERN.findall(log_text)
    if matches:
        lines = log_text.split("\n")
        key_lines = []
        capture = False
        for line in lines:
            stripped = line.strip()
            if _LATEX_ERROR_PATTERN.match(stripped):
                capture = True
            if capture:
                key_lines.append(line)
                if len("\n".join(key_lines)) > max_chars:
                    break
        return "\n".join(key_lines)
    return log_text[-max_chars:]


async def translate_compile_errors(
    stdout: str,
    stderr: str = "",
    language: str = "zh",
    *,
    filtered_output: str | None = None,
) -> tuple[str, list[str]]:
    """
    将 LaTeX 编译失败的日志翻译为中文解释和修复建议。

    参数：
        stdout: 编译进程的标准输出
        stderr: 编译进程的标准错误
        language: 输出语言（"zh" 或 "en"）
        filtered_output: texfot 过滤后的干净输出（可选）。
            若提供则直接用于 LLM 分析，跳过 extract_key_errors 正则过滤。

    返回：
        (explanation, fixes) 元组：
        - explanation: 中文错误解释
        - fixes: 中文修复建议列表

    若 LLM 调用失败，返回兜底提示。
    """
    if filtered_output is not None:
        input_for_llm = filtered_output
    else:
        combined_output = stdout + "\n" + stderr
        input_for_llm = combined_output

    if not input_for_llm.strip():
        return ("", [])

    try:
        key_errors = (
            input_for_llm
            if filtered_output is not None
            else extract_key_errors(input_for_llm)
        )
        llm_client = get_llm_client()

        messages = [
            {
                "role": "system",
                "content": get_prompt(
                    "compile_error_translator", "system", language=language
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Please analyze the following LaTeX compilation error log and provide a plain-English explanation along with specific repair suggestions:\n\n"
                    f"{key_errors}"
                ),
            },
        ]

        async def chat_fn(msgs, **kwargs):
            kwargs["response_format"] = {"type": "json_object"}
            return await llm_client.chat(msgs, **kwargs)

        result = await retry_with_validation(
            chat_fn=chat_fn,
            messages=messages,
            model_cls=ErrorTranslation,
            max_retries=2,
            logger_name="compile_error_translator",
            temperature=0.3,
            max_tokens=2048,
        )
        return (result.explanation, result.fixes)
    except Exception as e:
        logger.error(f"LLM error translation failed: {e}")
        return (
            "LLM error translation failed, please refer to the raw compilation output below",
            [],
        )

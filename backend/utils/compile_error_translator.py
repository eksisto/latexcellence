"""
LaTeX 编译错误 LLM 翻译工具。

职责：
    1. 从 latexmk/xelatex 原始日志中提取关键错误行
    2. 调用 LLM 将英文错误信息翻译为通俗易懂的中文解释和修复建议

供 compile 模块（自动编译）和 git_diff 模块（差异高亮）复用。
"""

import re

from pydantic import BaseModel, ConfigDict

from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.json_utils import retry_with_validation

logger = get_logger(__name__)


class ErrorTranslation(BaseModel):
    """LLM 返回的错误翻译结构。"""

    model_config = ConfigDict(extra="forbid")

    explanation: str
    fixes: list[str] = []


_SYSTEM_PROMPT = (
    "你是一个专业的 LaTeX 编译错误诊断专家。\n"
    "你的任务是分析 latexmk/xelatex 的原始报错日志，定位根本原因，并用通俗易懂的中文向非专业用户解释，同时提供切实可行的修复建议。\n\n"
    "【诊断规则】\n"
    "1. 聚焦致命错误：忽略常规警告（如 Underfull \\\\hbox, Font Warning 等），优先找出导致编译失败的核心错误（如 `! Undefined control sequence`, `Fatal error`, `Missing $ inserted`, `File \\`xxx.sty' not found` 等）。\n"
    "2. 精确定位位置：必须尽可能从日志中提取出错的具体文件（如 `(./xxx.tex)`）和代码行号（如 `l.123` 代表第 123 行）。\n"
    "3. 深度分析原因：不要仅做机器翻译。例如：遇到 Undefined control sequence 需分析是拼写错误还是漏加宏包；遇到 File not found 需提示安装对应宏包或检查文件名。\n"
    "4. 具体且可操作的建议：提供具体的修改方案（如：在导言区添加 `\\\\usepackage{amsmath}` 或将第 15 行的 `\\\\alfa` 改为 `\\\\alpha`）。\n"
    "5. 纯 JSON 输出：必须严格返回 JSON 格式，不要包含 Markdown 代码块或任何其他无关文本。\n\n"
    "【JSON 格式要求】\n"
    "{\n"
    '  "explanation": "<字符串：综合说明出错的文件、行号以及根本原因。语气要温和、易懂。>",\n'
    '  "fixes": [\n'
    '    "<字符串：具体的修复建议 1>",\n'
    '    "<字符串：具体的修复建议 2>"\n'
    '  ]\n'
    "}"
)

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
    stderr: str,
) -> tuple[str, list[str]]:
    """
    将 LaTeX 编译失败的日志翻译为中文解释和修复建议。

    参数：
        stdout: 编译进程的标准输出
        stderr: 编译进程的标准错误

    返回：
        (explanation, fixes) 元组：
        - explanation: 中文错误解释
        - fixes: 中文修复建议列表

    若 LLM 调用失败，返回兜底提示。
    """
    combined_output = stdout + "\n" + stderr
    if not combined_output.strip():
        return ("", [])

    try:
        key_errors = extract_key_errors(combined_output)
        llm_client = get_llm_client()

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"请分析以下 LaTeX 编译错误日志，提供通俗易懂的中文解释和具体的修复建议：\n\n"
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
            "（LLM 错误翻译失败，请查看下方原始编译输出来排查问题）",
            [],
        )

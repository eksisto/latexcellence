"""
内置 LaTeX 轻量格式化器。
无外部依赖，作为 latexindent.pl 不可用时的兜底方案。

处理范围：
- 环境缩进（\\begin{env} … \\end{env} 内统一缩进）
- 去除行尾多余空白
- 压缩连续空行
- 一致性换行
"""

import re

from backend.core.logger import get_logger

_logger = get_logger("builtin_formatter")

_ENV_START_RE = re.compile(r"(\\begin\{[^}]*\})")
_ENV_END_RE = re.compile(r"(\\end\{[^}]*\})")


def format_latex(source: str) -> str:
    """
    对 LaTeX 源码进行轻量格式化。

    按行扫描，维护环境栈，自动缩进和空行处理。
    """
    lines = source.split("\n")
    result: list[str] = []
    env_stack: list[str] = []

    for line in lines:
        stripped = line.strip()
        # 行尾空白已通过 strip 去除
        if not stripped:
            result.append("")
            continue

        # 检测 \\end 降低缩进层级（先 pop，使 \\end 本身以外层缩进对齐）
        end_match = _ENV_END_RE.search(stripped)
        if end_match:
            env_names = _ENV_END_RE.findall(stripped)
            for _ in env_names:
                if env_stack:
                    env_stack.pop()
                else:
                    _logger.warning("发现多余的 \\end，环境栈为空: %s", stripped)

        indent_level = len(env_stack)
        indented = "  " * indent_level + stripped

        result.append(indented)

        # 检测 \\begin 提升缩进层级（独立于 \\end，支持同行同时出现）
        start_match = _ENV_START_RE.search(stripped)
        if start_match:
            env_names = _ENV_START_RE.findall(stripped)
            for name in env_names:
                env_name = re.search(r"\\begin\{([^}]*)\}", name)
                if env_name:
                    env_stack.append(env_name.group(1))

    # 压缩连续空行最多 1 个
    formatted: list[str] = []
    prev_empty = False
    for line in result:
        if line == "":
            if not prev_empty:
                formatted.append(line)
            prev_empty = True
        else:
            formatted.append(line)
            prev_empty = False

    return "\n".join(formatted)

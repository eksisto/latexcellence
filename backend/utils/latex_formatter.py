"""
统一 LaTeX 格式化工具。

对外提供单一异步入口 format_latex_source()：
- 优先调用 latexindent.pl（加载项目内置 latexindent.yaml，每层 4 个空格缩进）
- latexindent 不可用时回退到内置轻量格式化器（同样每层 4 个空格）

code_review / formula_ocr / table_ocr 共用，避免各模块重复实现格式化调度。
"""

import asyncio

from backend.core.logger import get_logger
from backend.utils.builtin_formatter import format_latex
from backend.utils.latexindent_utils import (
    format_with_latexindent,
    is_latexindent_available,
)

_logger = get_logger("latex_formatter")

# 进程内共享 latexindent 可用性缓存（各模块复用，避免重复探测）
_latexindent_available: bool | None = None


async def format_latex_source(source: str) -> str:
    """统一格式化入口：latexindent（每层 4 空格）→ 内置格式化器兜底。

    两个底层实现内部均已做失败兜底（异常 / 超时 / 空结果时返回原样），
    因此本函数不会抛错；空白源码直接原样返回，不触发子进程。
    """
    if not source.strip():
        return source

    global _latexindent_available
    if _latexindent_available is None:
        _latexindent_available = await asyncio.to_thread(is_latexindent_available)
    if _latexindent_available:
        return await format_with_latexindent(source)
    return await asyncio.to_thread(format_latex, source)


def reset_latexindent_cache() -> None:
    """重置 latexindent 可用性缓存（测试 / 环境变化后重新探测用）。"""
    global _latexindent_available
    _latexindent_available = None

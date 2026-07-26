"""
统一 TeX 编译执行模块。

职责：
    1. 通过 texfot（TeX Live 自带工具）包装 latexmk 执行编译，
       自动过滤日志中的噪声信息，只保留错误、警告等关键内容。
    2. 返回过滤后的干净输出 + 原始输出，供 LLM 分析和前端展示。
    3. 当 texfot 不可用时，静默降级为原始 latexmk 输出 + 正则 fallback。

供 compile 模块（自动编译）、git_diff 模块（差异高亮）、
template_gen 模块（模板生成 Agent）复用。
"""

import asyncio
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger

logger = get_logger(__name__)

_texfot_available: bool | None = None


class TexCompileResult(BaseModel):
    """TeX 编译执行结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    filtered_output: str
    raw_output: str
    timed_out: bool = False
    used_texfot: bool = True


def _check_texfot_available() -> bool:
    """检测 texfot 是否在 PATH 中可用（模块级缓存，只查一次）。"""
    global _texfot_available
    if _texfot_available is None:
        _texfot_available = shutil.which("texfot") is not None
        if _texfot_available:
            logger.info("texfot is available, will use it for compile filtering")
        else:
            logger.info("texfot not found on PATH, falling back to raw latexmk output")
    return _texfot_available


def _filter_with_regex(raw_output: str, max_chars: int = 3000) -> str:
    """降级路径：用正则从原始日志中提取关键错误行。

    当 texfot 不可用时作为 fallback，复用 compile_error_translator 中的
    同款正则逻辑。
    """
    import re

    error_pattern = re.compile(
        r"^! .*|^Runaway argument\?|^Emergency stop|^l\.\d+|"
        r"^No file .*|"
        r"^LaTeX Error:|^Package .* Error:|"
        r"^Missing .* inserted",
        re.MULTILINE,
    )

    lines = raw_output.split("\n")
    key_lines: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if error_pattern.match(stripped):
            capture = True
        if capture:
            key_lines.append(line)
            if len("\n".join(key_lines)) > max_chars:
                break

    if key_lines:
        return "\n".join(key_lines)
    return raw_output[-max_chars:] if len(raw_output) > max_chars else raw_output


async def run_tex_compile(
    main_file: str,
    cwd: str | Path,
    timeout: float,
    extra_args: list[str] | None = None,
) -> TexCompileResult:
    """通过 texfot 包装 latexmk 执行 TeX 编译，返回过滤后的输出。

    参数：
        main_file: 主 .tex 文件路径（相对于 cwd）
        cwd: 编译工作目录
        timeout: 超时秒数
        extra_args: 传递给 latexmk 的额外参数（如 ["-halt-on-error"]）

    返回：
        TexCompileResult（含过滤后输出、原始输出、成功/失败状态）
    """
    cwd_str = str(cwd)
    latexmk_base = ["latexmk", "-xelatex", "-interaction=nonstopmode"]
    if extra_args:
        latexmk_base.extend(extra_args)
    latexmk_base.append(main_file)

    if _check_texfot_available():
        cmd = ["texfot", "--quiet", "--no-interactive"] + latexmk_base
        logger.debug(f"Running: {' '.join(cmd)} in {cwd_str}")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_b, stderr_b = await process.communicate()
            raw = _decode(stdout_b, stderr_b)
            return TexCompileResult(
                success=False,
                filtered_output="",
                raw_output=raw,
                timed_out=True,
            )

        raw = _decode(stdout_b, stderr_b)
        filtered = raw
        success = process.returncode == 0
        return TexCompileResult(
            success=success,
            filtered_output=filtered,
            raw_output=raw,
            used_texfot=True,
        )

    # ── 降级路径：直接 latexmk + 正则过滤 ──
    logger.debug(f"Running (fallback): {' '.join(latexmk_base)} in {cwd_str}")
    try:
        process = await asyncio.create_subprocess_exec(
            *latexmk_base,
            cwd=cwd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout_b, stderr_b = await process.communicate()
        raw = _decode(stdout_b, stderr_b)
        return TexCompileResult(
            success=False,
            filtered_output="",
            raw_output=raw,
            timed_out=True,
            used_texfot=False,
        )

    raw = _decode(stdout_b, stderr_b)
    success = process.returncode == 0
    filtered = raw if success else _filter_with_regex(raw)
    return TexCompileResult(
        success=success,
        filtered_output=filtered,
        raw_output=raw,
        used_texfot=False,
    )


def _decode(stdout_b: bytes, stderr_b: bytes) -> str:
    """合并解码 stdout + stderr。"""
    stdout_str = stdout_b.decode("utf-8", errors="replace")
    stderr_str = stderr_b.decode("utf-8", errors="replace")
    if stderr_str:
        return stdout_str + "\n" + stderr_str
    return stdout_str

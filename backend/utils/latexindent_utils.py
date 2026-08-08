"""
latexindent.pl 调用封装。

负责：检测环境、调用 latexindent 对 LaTeX 源码片段进行格式化。
"""

import asyncio
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from backend.core.logger import get_logger

_logger = get_logger("latexindent_utils")

_LATEXINDENT_TIMEOUT = 30

# 清理重试次数与间隔：覆盖 Windows 上备份/日志文件延迟落盘的时序窗口
_CLEANUP_MAX_RETRIES = 3
_CLEANUP_RETRY_INTERVAL_SECONDS = 0.05

# 项目统一缩进配置：覆盖 latexindent 默认的制表符缩进，统一为每层 4 个空格
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "latexindent.yaml"


def is_latexindent_available() -> bool:
    """检测 latexindent 是否可用。"""
    try:
        result = subprocess.run(
            ["latexindent", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def format_with_latexindent(
    source: str,
    file_path: str | None = None,
    config_path: str | None = None,
) -> str:
    """
    对 LaTeX 源码片段执行 latexindent 格式化。

    参数：
        source: 要格式化的 LaTeX 源码
        file_path: 原始文件路径（用于检测文件类型，可选）
        config_path: latexindent 配置文件路径（可选）；默认加载项目内置
            latexindent.yaml（每层 4 个空格缩进），可传入自定义配置覆盖

    返回：
        格式化后的 LaTeX 源码；异常或空结果时返回原始源码。
    """
    tmp_path = None
    try:
        tmp_name = f"latexindent_{uuid.uuid4().hex[:8]}.tex"
        tmp_path = Path(tempfile.gettempdir()) / tmp_name
        tmp_path.write_text(source, encoding="utf-8")

        # 每次运行使用独立日志名（与临时文件同 stem），随备份文件一并清理，
        # 避免并发运行共享 indent.log 时互相覆盖/删除
        log_path = tmp_path.with_suffix(".log")
        cmd = [
            "latexindent",
            "-s",
            "-w",
            "-c",
            str(tmp_path.parent),
            f"-g={log_path.name}",
        ]
        # -s: silent mode, -w: overwrite, -c: 日志/备份统一写入临时目录便于清理
        cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if cfg_path.is_file():
            # -l 只合并指定配置文件（其余沿用 latexindent 默认设置）
            cmd.append(f"-l={cfg_path.as_posix()}")
        else:
            _logger.warning(f"latexindent config not found: {cfg_path}, using defaults")
        cmd.append(str(tmp_path))

        def _cleanup_cruft() -> None:
            """删除本次运行产生的 latexindent 备份（*.bak*）与日志。"""
            for _ in range(_CLEANUP_MAX_RETRIES):
                leftovers = [
                    p
                    for p in tmp_path.parent.glob(f"{tmp_path.stem}.*")
                    if p != tmp_path
                ]
                if not leftovers:
                    return
                for leftover in leftovers:
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
                time.sleep(_CLEANUP_RETRY_INTERVAL_SECONDS)
            _logger.warning(
                "latexindent cruft cleanup incomplete: %s",
                [p.name for p in leftovers],
            )

        def _run() -> str:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=_LATEXINDENT_TIMEOUT,
                )
                if result.returncode != 0:
                    _logger.warning(
                        f"latexindent failed with rc={result.returncode}: "
                        f"{result.stderr[:200]}"
                    )
                    return source
                formatted = tmp_path.read_text(encoding="utf-8")
                if not formatted.strip():
                    _logger.warning("latexindent returned empty output, falling back")
                    return source
                return formatted
            finally:
                # 成功/失败/超时路径统一清理，避免临时目录残留备份与日志
                _cleanup_cruft()

        return await asyncio.to_thread(_run)

    except subprocess.TimeoutExpired:
        _logger.warning(
            f"latexindent timed out after {_LATEXINDENT_TIMEOUT}s, "
            "falling back to original source"
        )
        return source
    except Exception as e:
        _logger.warning(
            f"latexindent call failed: {e}, falling back to original source"
        )
        return source
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

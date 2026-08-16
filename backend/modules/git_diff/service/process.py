"""Git 子进程执行工具。"""

import asyncio
import os
import subprocess
from pathlib import Path

from backend.core.logger import get_logger
from backend.utils.tex_compile import terminate_process_tree

from .models import ProcessResult

logger = get_logger(__name__)


async def _run_git_checked(
    cwd: Path,
    args: list[str],
    timeout: int = 30,
) -> ProcessResult:
    result = await _run_git(cwd, args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "Git command failed"
        )
    return result


async def _run_git(cwd: Path, args: list[str], timeout: int = 30) -> ProcessResult:
    return await _run_process(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        timeout=timeout,
    )


async def _run_process(cmd: list[str], cwd: Path, timeout: int) -> ProcessResult:
    proc_kwargs: dict = {}
    if os.name == "nt":
        proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        proc_kwargs["start_new_session"] = True
    # git 输出随用户 locale 变化，而本模块需要解析 stderr 中的固定文案
    # （如空仓库判定）。强制 LC_ALL=C 保证机器可解析的稳定英文输出。
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        **proc_kwargs,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await terminate_process_tree(process)
        stdout, stderr = await process.communicate()
        return ProcessResult(
            returncode=-1,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=(
                stderr.decode("utf-8", errors="replace")
                + f"\nCommand timed out after {timeout} seconds."
            ),
        )

    return ProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


async def _get_optional_git_output(cwd: Path, args: list[str]) -> str:
    result = await _run_git(cwd, args, timeout=30)
    if result.returncode != 0:
        return ""
    return result.stdout

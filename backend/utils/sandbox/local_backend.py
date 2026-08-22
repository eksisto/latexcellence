"""本地执行后端：既有 subprocess 行为的封装（sandbox 回落路径）。

行为契约与沙箱化之前完全一致（v2 方案 §5.6 "local 回落回归全绿"）：
- 环境变量 = 完整 os.environ + ExecSpec.env 白名单覆盖；
- POSIX 下 start_new_session、Windows 下 CREATE_NEW_PROCESS_GROUP，
  超时整组击杀进程树。
"""

import asyncio
import os
import signal
import subprocess
from typing import Any

from backend.core.logger import get_logger
from backend.utils.sandbox.models import ExecSpec, ExecResult

logger = get_logger("sandbox.local")


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """终止进程及其子进程树，供 latexmk / latexdiff 等会派生子进程的命令使用。

    只 kill 顶层进程时，Windows 上 xelatex / bibtex 等子进程会残留并占用
    文件锁。子进程创建时已通过 ``creationflags`` / ``start_new_session``
    建立独立进程组，这里整组终止。taskkill 不可用或失败时回退为只杀
    顶层进程（与旧行为一致）。
    """
    pid = process.pid
    # pid<=1（0/1 或 MagicMock 缺省值）绝不能作为 killpg 目标：
    # PID 1 的进程组可能正是当前进程组，误杀会带走整个服务/测试进程
    if not pid or pid <= 1:
        return

    def _kill() -> None:
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return
            except Exception:
                pass
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    await asyncio.to_thread(_kill)


def _decode(stdout_b: bytes, stderr_b: bytes) -> tuple[str, str]:
    return (
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _build_proc_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


async def execute_local(spec: ExecSpec) -> ExecResult:
    """在本地以子进程执行 ExecSpec（与历史各调用点行为逐项一致）。"""
    env = os.environ.copy()
    env.update(spec.env)
    logger.debug("local exec | cmd=%s | cwd=%s", spec.command, spec.cwd)
    process = await asyncio.create_subprocess_exec(
        *spec.command,
        cwd=spec.cwd,
        env=env,
        **_build_proc_kwargs(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=spec.timeout_seconds
        )
    except asyncio.TimeoutError:
        await terminate_process_tree(process)
        stdout_b, stderr_b = await process.communicate()
        stdout, stderr = _decode(stdout_b, stderr_b)
        return ExecResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            backend="local",
        )

    stdout, stderr = _decode(stdout_b, stderr_b)
    return ExecResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        backend="local",
    )

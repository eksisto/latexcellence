"""Git 子进程执行工具。

v2 P1a：执行统一经 SandboxRunner —— sandbox 模式下 git/latexdiff 进
容器（tex 镜像内置 git 与 latexdiff，项目目录同绝对路径挂载），
auto 探测失败或 local 模式回落本地子进程（行为与历史完全一致，
含 LC_ALL=C 的稳定英文输出约定与超时语义）。
"""

from pathlib import Path

from backend.core.logger import get_logger
from backend.utils.sandbox import ExecSpec, current_sandbox_user_id, get_sandbox_runner

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
    # git 输出随用户 locale 变化，而本模块需要解析 stderr 中的固定文案
    # （如空仓库判定）。强制 LC_ALL=C 保证机器可解析的稳定英文输出。
    spec = ExecSpec(
        command=list(cmd),
        cwd=str(cwd),
        image_kind="tex",
        env={"LC_ALL": "C", "LANG": "C"},
        timeout_seconds=float(timeout),
    )
    result = await get_sandbox_runner().execute(spec, user_id=current_sandbox_user_id())
    if result.timed_out:
        return ProcessResult(
            returncode=-1,
            stdout=result.stdout,
            stderr=(result.stderr + f"\nCommand timed out after {timeout} seconds."),
        )
    return ProcessResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


async def _get_optional_git_output(cwd: Path, args: list[str]) -> str:
    result = await _run_git(cwd, args, timeout=30)
    if result.returncode != 0:
        return ""
    return result.stdout

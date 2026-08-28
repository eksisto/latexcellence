"""Git 子进程执行工具。

v2 P1a：执行统一经 SandboxRunner —— sandbox 模式下 git/latexdiff 进
容器（tex 镜像内置 git 与 latexdiff，项目目录同绝对路径挂载），
auto 探测失败或 local 模式回落本地子进程（行为与历史完全一致，
含 LC_ALL=C 的稳定英文输出约定与超时语义）。

每用户并发租约：只读 git 子命令（status/log 等查询类，见
``_is_readonly_git_args``）豁免租约；init/add/commit 等写操作照常
占用；latexdiff 属重任务，不豁免。
"""

from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.sandbox import ExecSpec, current_sandbox_user_id, get_sandbox_runner
from backend.utils.tex_compile import sanitize_output_paths

from .models import ProcessResult

logger = get_logger(__name__)

# 只读 git 子命令：查询/导出类，不改动仓库且耗时极短，豁免每用户沙箱
# 并发租约（仍受全局并发约束）——否则版本页并行拉取 status/log 这类
# 只读请求会在每用户上限=1 时互相顶掉，误报 429 sandbox_busy。
_GIT_READONLY_SUBCOMMANDS = frozenset(
    {
        "archive",
        "branch",
        "cat-file",
        "diff",
        "for-each-ref",
        "log",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "show",
        "status",
    }
)
# 会改动仓库状态的子命令：用于在参数扫描中确认真实子命令的位置，
# 防止子命令的参数值（如 commit message 为 "status"）被误判为只读。
_GIT_MUTATING_SUBCOMMANDS = frozenset(
    {
        "add",
        "checkout",
        "clean",
        "commit",
        "init",
        "merge",
        "mv",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "tag",
    }
)


def _is_readonly_git_args(args: list[str]) -> bool:
    """判定 git 参数列表是否只读调用（供每用户沙箱租约豁免）。

    按顺序找第一个已知子命令 token：全局选项的值（如 ``-c user.name=x``）
    含 ``=`` 不会精确命中子命令名，子命令自身的参数又都排在子命令之后，
    因此首个命中即真实子命令；未识别的调用保守按非只读处理（照常占用
    租约，宁多不漏）。
    """
    for token in args:
        if token in _GIT_READONLY_SUBCOMMANDS:
            return True
        if token in _GIT_MUTATING_SUBCOMMANDS:
            return False
    return False


def redact_command_output_for_user(text: str, cwd: Path) -> str:
    """命令输出作为面向用户的错误消息前的统一脱敏口。

    多用户模式下把 ``<cwd>/`` 前缀（含裸 cwd 边界）改写为相对形式，
    防止服务器上的项目/用户空间绝对路径经 git/latexdiff 的 stderr 或
    stdout 泄漏给客户端；单用户本地模式保留原文（路径对操作者本人可
    见，与编译输出口径一致）。完整原始输出仍由调用方按需记入服务端日
    志（日志不做脱敏，符合「完整路径只允许进服务端日志」约定）。
    """
    if not get_config().multi_user.enabled:
        return text
    return sanitize_output_paths(text, cwd)


async def run_git_checked(
    cwd: Path,
    args: list[str],
    timeout: int = 30,
) -> ProcessResult:
    result = await run_git(cwd, args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            redact_command_output_for_user(
                result.stderr.strip() or result.stdout.strip() or "Git command failed",
                cwd,
            )
        )
    return result


async def run_git(cwd: Path, args: list[str], timeout: int = 30) -> ProcessResult:
    return await _run_process(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        timeout=timeout,
        lease_exempt=_is_readonly_git_args(args),
    )


async def _run_process(
    cmd: list[str], cwd: Path, timeout: int, lease_exempt: bool = False
) -> ProcessResult:
    # git 输出随用户 locale 变化，而本模块需要解析 stderr 中的固定文案
    # （如空仓库判定）。强制 LC_ALL=C 保证机器可解析的稳定英文输出。
    # safe.directory=* 硬化 docker 部署下容器内 uid 与挂载目录属主不
    # 一致时的 "dubious ownership" 拒绝（git 只在仓库归属可疑时读取该
    # 配置，非沙箱本地模式行为不变）。
    spec = ExecSpec(
        command=list(cmd),
        cwd=str(cwd),
        image_kind="tex",
        env={
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        },
        timeout_seconds=float(timeout),
    )
    result = await get_sandbox_runner().execute(
        spec, user_id=current_sandbox_user_id(), lease_exempt=lease_exempt
    )
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


async def get_optional_git_output(cwd: Path, args: list[str]) -> str:
    result = await run_git(cwd, args, timeout=30)
    if result.returncode != 0:
        return ""
    return result.stdout

"""Docker 沙箱执行包（多用户 v2 P1a）。

统一入口：所有外部命令执行点把命令/工作目录/超时描述为
:class:`ExecSpec`，经 :func:`get_sandbox_runner` 执行并得到归一的
:class:`ExecResult`。local 回落与既有 subprocess 行为逐项一致。

对外再导出（调用方约定）：
    from backend.utils.sandbox import ExecSpec, get_sandbox_runner
"""

from backend.utils.sandbox.errors import (
    SANDBOX_BUSY,
    SANDBOX_UNAVAILABLE,
    make_sandbox_error,
    sandbox_busy,
    sandbox_code_of,
    sandbox_unavailable,
    to_http_exception,
)
from backend.utils.sandbox.models import (
    BackendKind,
    ExecResult,
    ExecSpec,
    ImageKind,
    SandboxLimits,
    SandboxMount,
)
from backend.utils.sandbox.runner import (
    cached_backend_for,
    close_sandbox_runner,
    current_sandbox_user_id,
    get_sandbox_runner,
    initialize_sandbox_runner,
    reset_sandbox_runner,
)

__all__ = [
    "SANDBOX_BUSY",
    "SANDBOX_UNAVAILABLE",
    "BackendKind",
    "ExecResult",
    "ExecSpec",
    "ImageKind",
    "SandboxLimits",
    "SandboxMount",
    "cached_backend_for",
    "close_sandbox_runner",
    "current_sandbox_user_id",
    "get_sandbox_runner",
    "initialize_sandbox_runner",
    "make_sandbox_error",
    "reset_sandbox_runner",
    "sandbox_busy",
    "sandbox_code_of",
    "sandbox_unavailable",
    "to_http_exception",
]

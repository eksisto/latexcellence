"""沙箱基础设施错误的表达与映射。

AGENTS.md 规范禁止自定义异常类：这里复用 RuntimeError 实例，通过
``sandbox_code`` 属性携带机器可读错误码（与 v2 方案 §4.4 新增错误码一致）：

- ``sandbox_unavailable`` → HTTP 503（daemon 不可达 / 容器创建失败）
- ``sandbox_busy``        → HTTP 429（并发池满）

路由层用 :func:`to_http_exception` 把沙箱错误翻译为带
``detail={"error": code}`` 的 HTTPException，前端经 errorMessages 统一翻译。
"""

from fastapi import HTTPException

SANDBOX_UNAVAILABLE = "sandbox_unavailable"
SANDBOX_BUSY = "sandbox_busy"

_SANDBOX_CODES = frozenset({SANDBOX_UNAVAILABLE, SANDBOX_BUSY})


def make_sandbox_error(code: str, message: str) -> RuntimeError:
    """构造携带 sandbox_code 属性的 RuntimeError。"""
    if code not in _SANDBOX_CODES:
        raise ValueError(f"unknown sandbox error code: {code}")
    err = RuntimeError(message)
    setattr(err, "sandbox_code", code)
    return err


def sandbox_unavailable(reason: str) -> RuntimeError:
    return make_sandbox_error(SANDBOX_UNAVAILABLE, f"Sandbox unavailable: {reason}")


def sandbox_busy(reason: str) -> RuntimeError:
    return make_sandbox_error(SANDBOX_BUSY, f"Sandbox busy: {reason}")


def sandbox_code_of(exc: BaseException) -> str | None:
    """读取异常上的 sandbox_code 标记；非沙箱错误返回 None。"""
    return getattr(exc, "sandbox_code", None)


def to_http_exception(err: BaseException) -> HTTPException | None:
    """把沙箱错误翻译为 HTTPException；非沙箱错误返回 None（调用方继续原处理）。"""
    code = sandbox_code_of(err)
    if code not in _SANDBOX_CODES:
        return None
    status = 503 if code == SANDBOX_UNAVAILABLE else 429
    return HTTPException(status_code=status, detail={"error": code})

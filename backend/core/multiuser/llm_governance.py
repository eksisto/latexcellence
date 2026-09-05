"""LLM 每用户治理（多用户模式，v2 方案 §6 / P1b）。

两个强制执行维度（补齐 v1 P7 缺口，配置项
``multi_user.llm_rate_limit_per_minute`` / ``llm_max_concurrency_per_user``
自此真实生效）：

1. 速率：``rate_limit_events(kind='llm', identity=user_id, ts)`` 滑动窗口，
   每次 LLM 调用前在同一事务内 COUNT（60s 窗口）+ INSERT；达到阈值抛
   ``llm_rate_limited``（HTTP 429，附 Retry-After = 窗口内最老事件到期秒数）。
2. 并发：``llm_concurrency_leases`` 租约（task_id = 本次调用 uuid），活跃
   租约数 ≥ 每用户上限直接拒绝不排队（``llm_concurrency_limited``，429，
   Retry-After = 最早租约到期秒数）；调用结束 finally 释放；进程崩溃残留
   租约由 TTL 过滤 + 维护循环 purge 兜底。

与登录限流（kind='login'，auth.py 迁移至同表）共用 rate_limit_events。
事务使用 BEGIN IMMEDIATE：P7 多实例阶段同一共享卷上天然跨副本生效，
本模块无需修改。

错误表达遵循 AGENTS.md（禁止自定义异常类）：复用 RuntimeError 实例，通过
``gov_code`` / ``retry_after`` 属性携带机器可读码与重试等待秒数；
:func:`governance_http_response` 把治理错误翻译为 429 JSONResponse
（main.py 全局异常处理器注册），前端经 errorMessages 统一翻译。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi.responses import JSONResponse

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import db, userspace

logger = get_logger("multiuser.llm_governance")

# LLM 限流滑动窗口宽度（方案 §6 固定 60s）
WINDOW_SECONDS = 60
# 并发租约 TTL：单次 LLM 调用（含流式生成）的最长持有窗口。进程崩溃后残留
# 租约最长滞留该时长；期间该用户并发额度被占用（维护循环会提前清理过期行）。
LEASE_TTL_SECONDS = 900
# 维护循环清理限流事件的保留期（远大于最大窗口，仅防表无限增长）
EVENTS_RETENTION_SECONDS = 3600

LLM_RATE_LIMITED = "llm_rate_limited"
LLM_CONCURRENCY_LIMITED = "llm_concurrency_limited"


def make_governance_error(code: str, message: str, retry_after: int) -> RuntimeError:
    """构造携带 gov_code / retry_after 属性的 RuntimeError（429 语义）。"""
    err = RuntimeError(message)
    setattr(err, "gov_code", code)
    setattr(err, "retry_after", max(1, int(retry_after)))
    return err


def governance_code_of(exc: BaseException) -> str | None:
    """读取异常上的 gov_code 标记；非治理错误返回 None。"""
    return getattr(exc, "gov_code", None)


def governance_http_response(exc: BaseException) -> JSONResponse | None:
    """把治理错误翻译为 429 JSONResponse；非治理错误返回 None。

    main.py 全局异常处理器调用。响应体遵循应用既有的结构化错误约定
    （HTTPException 的 ``{"detail": {"error", "message", ...}}`` 包装，
    与 sandbox/登录限流一致）：前端 request() 只读 ``err.detail`` 并把
    ``detail.error`` 挂到 error.code，缺 detail 包装会退化为裸 "HTTP 429"。
    Retry-After 同时写入响应头与 detail，前端按错误码翻译并展示等待秒数。
    """
    code = governance_code_of(exc)
    if code is None:
        return None
    retry_after = max(1, int(getattr(exc, "retry_after", 1) or 1))
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "error": code,
                "message": str(exc),
                "retry_after": retry_after,
            }
        },
        headers={"Retry-After": str(retry_after)},
    )


def register_governance_handler(app) -> None:
    """把治理错误处理器注册到 FastAPI 应用（main.py 与集成测试共用）。

    带 ``gov_code`` 标记的 RuntimeError 统一翻译为 429；不带标记的普通
    RuntimeError 原样重抛，维持默认 ServerErrorMiddleware 处理路径。
    """
    from fastapi import Request

    async def _handler(request: Request, exc: RuntimeError):
        response = governance_http_response(exc)
        if response is None:
            raise exc
        return response

    app.add_exception_handler(RuntimeError, _handler)


def governed_user_id() -> str | None:
    """返回应被治理的当前用户 id；治理不适用时返回 None。

    仅多用户模式启用；单用户模式、以及多用户模式下取不到请求级用户上下文
    的调用方（后台任务等），均跳过治理保持原有行为。
    """
    if not userspace.is_multiuser():
        return None
    user_id = userspace.get_current_user_id()
    if not user_id:
        logger.debug(
            "Multiuser mode active but no current-user context; skip LLM governance"
        )
    return user_id


@asynccontextmanager
async def admission_gate() -> AsyncIterator[str | None]:
    """LLM 调用治理门（async with 使用）。

    - 治理不适用（单用户模式 / 无当前用户上下文）：yield None 直通；
    - 适用：先在单事务内完成"限流校验 + 事件落账 + 并发租约获取"，超限即抛
      429 语义 RuntimeError（不排队）；成功则 yield task_id，退出时 finally
      释放租约（客户端断开导致的 GeneratorExit 同样覆盖）。
    """
    user_id = governed_user_id()
    if user_id is None:
        yield None
        return
    task_id = await asyncio.to_thread(acquire, user_id)
    try:
        yield task_id
    finally:
        # 释放失败（如 busy_timeout 耗尽）不得掩盖业务原异常；过期租约
        # 由惰性清扫与维护循环兜底，此处仅告警
        try:
            await asyncio.to_thread(release_lease, task_id)
        except Exception:
            logger.warning(
                "LLM lease release failed (task=%s, user=%s)", task_id, user_id
            )


@asynccontextmanager
async def admission_exempt_scope() -> AsyncIterator[None]:
    """管理面 LLM 诊断豁免门（deploy-followups §4.4，有意偏离）。

    POST /api/llm/test（模型连通性测试）属管理员诊断操作：触发真实计费
    调用的主体是服务器管理员，且一次诊断不应消耗其个人速率窗口 / 并发
    租约（否则排障点击会与正常功能调用争抢配额）。作用域内临时挂起请求级
    当前用户上下文，admission_gate() 的 governed_user_id() 即返回 None
    直通（档案级并发 Semaphore 等客户端层约束不受影响）；单用户模式本就
    不受治理，该作用域为无害空操作。ContextVar 按异步任务隔离，作用域
    退出（含异常路径）即恢复，不影响并发处理中的其他请求。
    """
    previous = userspace.get_current_user_id()
    userspace.set_current_user_id(None)
    try:
        yield
    finally:
        userspace.set_current_user_id(previous)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def acquire(user_id: str) -> str:
    """原子获取 LLM 调用许可（速率 + 并发），返回 task_id；超限抛 RuntimeError。

    BEGIN IMMEDIATE 在"清扫-校验-记账-租约"全程持有写锁：同机多副本（P7）
    下校验与写入也是原子的，不会超发。被拒绝的调用不落任何行——速率窗口
    只统计已准入的调用，并发等待不消耗速率配额。
    """
    cfg = get_config().multi_user
    rate_limit = cfg.llm_rate_limit_per_minute
    max_concurrency = cfg.llm_max_concurrency_per_user

    task_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    now_iso = _iso(now)
    window_start = _iso(now - timedelta(seconds=WINDOW_SECONDS))

    with db.transaction() as conn:
        # 惰性清扫：过期租约不再计入活跃数（崩溃残留的主要自愈路径）
        conn.execute(
            "DELETE FROM llm_concurrency_leases WHERE expires_at <= ?", (now_iso,)
        )
        rate_count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_events "
            "WHERE kind = 'llm' AND identity = ? AND ts >= ?",
            (user_id, window_start),
        ).fetchone()[0]
        if rate_count >= rate_limit:
            oldest = conn.execute(
                "SELECT MIN(ts) FROM rate_limit_events "
                "WHERE kind = 'llm' AND identity = ? AND ts >= ?",
                (user_id, window_start),
            ).fetchone()[0]
            retry_after = 1
            if oldest:
                delta = (
                    datetime.fromisoformat(oldest)
                    + timedelta(seconds=WINDOW_SECONDS)
                    - now
                ).total_seconds()
                retry_after = max(1, int(delta) + 1)
            raise make_governance_error(
                LLM_RATE_LIMITED,
                f"LLM rate limit exceeded ({rate_count}/{rate_limit} per "
                f"{WINDOW_SECONDS}s); retry after {retry_after}s",
                retry_after,
            )
        lease_count = conn.execute(
            "SELECT COUNT(*) FROM llm_concurrency_leases WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        if lease_count >= max_concurrency:
            earliest = conn.execute(
                "SELECT MIN(expires_at) FROM llm_concurrency_leases WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            retry_after = LEASE_TTL_SECONDS
            if earliest:
                delta = (datetime.fromisoformat(earliest) - now).total_seconds()
                retry_after = max(1, min(int(delta) + 1, LEASE_TTL_SECONDS))
            raise make_governance_error(
                LLM_CONCURRENCY_LIMITED,
                f"LLM concurrency limit reached ({lease_count}/{max_concurrency}); "
                f"retry after {retry_after}s",
                retry_after,
            )
        conn.execute(
            "INSERT INTO rate_limit_events (kind, identity, ts) VALUES ('llm', ?, ?)",
            (user_id, now_iso),
        )
        conn.execute(
            "INSERT INTO llm_concurrency_leases "
            "(task_id, user_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                task_id,
                user_id,
                now_iso,
                _iso(now + timedelta(seconds=LEASE_TTL_SECONDS)),
            ),
        )
    logger.debug(
        "LLM admitted: user=%s task=%s (rate=%d/%d, concurrency=%d/%d)",
        user_id,
        task_id,
        rate_count + 1,
        rate_limit,
        lease_count + 1,
        max_concurrency,
    )
    return task_id


def release_lease(task_id: str) -> None:
    """释放并发租约（调用结束/异常路径 finally；幂等）。"""
    removed = db.db_execute(
        "DELETE FROM llm_concurrency_leases WHERE task_id = ?", (task_id,)
    )
    if removed == 0:
        logger.debug("LLM lease already gone (expired?): %s", task_id)


def active_counts(user_id: str) -> dict[str, int]:
    """返回指定用户的活跃事件/租约计数（测试与运维观测）。"""
    now_iso = db.utcnow()
    window_start = _iso(datetime.now(timezone.utc) - timedelta(seconds=WINDOW_SECONDS))
    return {
        "rate_events": db.db_count(
            "SELECT COUNT(*) FROM rate_limit_events "
            "WHERE kind = 'llm' AND identity = ? AND ts >= ?",
            (user_id, window_start),
        ),
        "leases": db.db_count(
            "SELECT COUNT(*) FROM llm_concurrency_leases "
            "WHERE user_id = ? AND expires_at > ?",
            (user_id, now_iso),
        ),
    }


# ── 维护循环统一入口（与 quota.clean_* / sandbox_leases.purge_expired 对齐）──
def purge_expired_llm_leases(now: str | None = None) -> int:
    """删除已过期的 LLM 并发租约，返回删除行数。"""
    return db.db_execute(
        "DELETE FROM llm_concurrency_leases WHERE expires_at <= ?",
        (now or db.utcnow(),),
    )


def purge_old_rate_limit_events(
    retention_seconds: int = EVENTS_RETENTION_SECONDS,
) -> int:
    """删除超过保留期的限流事件（login 与 llm 共用表，一并清理）。"""
    cutoff = _iso(
        datetime.now(timezone.utc) - timedelta(seconds=int(retention_seconds))
    )
    return db.db_execute("DELETE FROM rate_limit_events WHERE ts < ?", (cutoff,))

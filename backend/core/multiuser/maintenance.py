"""
多用户后台维护任务（P3：配额对账 + 回收站清理 + 暂存区清理）。

- reconcile_project_sizes()：重算各项目磁盘大小，修正 projects.size_bytes 缓存漂移
- clean_expired_recycle_bin()：永久删除超过 recycle_bin_days 的回收站项目
- clean_staging()：清理上传中断/失败遗留的暂存目录
- purge_expired_sandbox_slots()：清理崩溃残留的沙箱并发租约（v2 P1a）
- purge_expired_llm_leases() / purge_old_rate_limit_events()：
  清理过期 LLM 并发租约与超过保留期的限流事件（v2 P1b）
- purge_expired_edit_logs()：清理超过 edit_log_ttl_hours 无活动的
  反向补丁编辑日志栈与快照目录（v2 P3）

由 main.py lifespan 通过 asyncio.Task 启动；方法内实时读取配置。
"""

import asyncio

from backend.core.logger import get_logger
from . import quota
from .db import init_db
from .edit_log import purge_expired_edit_logs
from .llm_governance import (
    purge_expired_llm_leases,
    purge_old_rate_limit_events,
)
from .sandbox_leases import purge_expired_sandbox_slots

logger = get_logger("multiuser.maintenance")

DEFAULT_INTERVAL_SECONDS = 3600


async def maintenance_loop() -> None:
    """后台循环：周期执行配额对账与回收站/暂存区/租约/限流事件/编辑日志清理。"""
    logger.info("Multi-user maintenance loop started")
    while True:
        interval = DEFAULT_INTERVAL_SECONDS
        try:
            await asyncio.to_thread(init_db)
            reconciled = await asyncio.to_thread(quota.reconcile_project_sizes)
            purged = await asyncio.to_thread(quota.clean_expired_recycle_bin)
            cleaned = await asyncio.to_thread(quota.clean_staging)
            archived = await asyncio.to_thread(quota.clean_expired_archives)
            slots = await asyncio.to_thread(purge_expired_sandbox_slots)
            llm_leases = await asyncio.to_thread(purge_expired_llm_leases)
            rl_events = await asyncio.to_thread(purge_old_rate_limit_events)
            edit_ops = await asyncio.to_thread(purge_expired_edit_logs)
            if (
                reconciled
                or purged
                or cleaned
                or archived
                or slots
                or llm_leases
                or rl_events
                or edit_ops
            ):
                logger.info(
                    "Multi-user maintenance done: reconciled=%d purged=%d "
                    "cleaned_staging=%d cleaned_archives=%d sandbox_slots=%d "
                    "llm_leases=%d rate_limit_events=%d edit_log_ops=%d",
                    reconciled,
                    purged,
                    cleaned,
                    archived,
                    slots,
                    llm_leases,
                    rl_events,
                    edit_ops,
                )
        except Exception:
            logger.exception("Multi-user maintenance cycle failed")
        await asyncio.sleep(interval)

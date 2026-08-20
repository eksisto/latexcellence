"""
多用户后台维护任务（P3：配额对账 + 回收站清理 + 暂存区清理）。

- reconcile_project_sizes()：重算各项目磁盘大小，修正 projects.size_bytes 缓存漂移
- clean_expired_recycle_bin()：永久删除超过 recycle_bin_days 的回收站项目
- clean_staging()：清理上传中断/失败遗留的暂存目录

由 main.py lifespan 通过 asyncio.Task 启动；方法内实时读取配置。
"""

import asyncio

from backend.core.logger import get_logger
from . import quota
from .db import init_db

logger = get_logger("multiuser.maintenance")

DEFAULT_INTERVAL_SECONDS = 3600


async def maintenance_loop() -> None:
    """后台循环：周期执行配额对账与回收站/暂存区清理。"""
    logger.info("Multi-user maintenance loop started")
    while True:
        interval = DEFAULT_INTERVAL_SECONDS
        try:
            await asyncio.to_thread(init_db)
            reconciled = await asyncio.to_thread(quota.reconcile_project_sizes)
            purged = await asyncio.to_thread(quota.clean_expired_recycle_bin)
            cleaned = await asyncio.to_thread(quota.clean_staging)
            if reconciled or purged or cleaned:
                logger.info(
                    "Multi-user maintenance done: reconciled=%d purged=%d cleaned_staging=%d",
                    reconciled,
                    purged,
                    cleaned,
                )
        except Exception:
            logger.exception("Multi-user maintenance cycle failed")
        await asyncio.sleep(interval)

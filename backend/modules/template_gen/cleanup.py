"""
模板生成沙箱自动清理服务。

定期扫描 `output_dir/` 下的会话沙箱目录，删除超过保留期限的过期
目录。基于目录内文件的最大修改时间（mtime）判定是否过期，而非目录创建
时间，以确保最近被访问/读取的沙箱不会被误删。

设计说明：
    - 清理逻辑独立为模块，由 main.py 的 lifespan 启动后台任务
    - 每次执行时实时读取配置（get_config()），支持热重载
    - 仅清理输出目录下的会话沙箱（12 位 hex UUID 命名）
    - 使用 shutil.rmtree 删除目录树，兼容 Windows
"""

import asyncio
import shutil
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger(__name__)


def cleanup_expired_sandboxes() -> int:
    """扫描沙箱目录并删除过期会话，返回本次删除的目录数。

    判定逻辑：遍历 `<output_dir>/` 下所有子目录，取目录内
    所有文件的最大 mtime，若距今超过 `cleanup_retention_days` 天
    则删除该目录。
    """
    cfg = get_config()
    tg = cfg.features.template_gen

    if not tg.cleanup_enabled:
        return 0

    # 收集待扫描的沙箱根目录：
    #   - 单用户模式：存量全局目录（保持既有行为）
    #   - 多用户模式：每个用户的 templates 目录
    from backend.core.multiuser import userspace

    roots: list[Path] = []
    if not userspace.is_multiuser():
        legacy = Path(tg.output_dir)
        if not legacy.is_absolute():
            legacy = Path.cwd() / legacy
        if legacy.is_dir():
            roots.append(legacy)
    else:
        roots = userspace.iterate_user_resource_roots("templates")

    import time

    now = time.time()
    cutoff = now - tg.cleanup_retention_days * 86400
    removed = 0

    for sandbox_root in roots:
        if not sandbox_root.is_dir():
            continue
        for child in sorted(sandbox_root.iterdir()):
            if not child.is_dir():
                continue

            # 取目录内所有文件的最大 mtime
            try:
                latest_mtime = _latest_mtime(child)
            except OSError as e:
                logger.warning("Could not read sandbox mtime: %s — %s", child.name, e)
                continue

            if latest_mtime < cutoff:
                try:
                    shutil.rmtree(child)
                    # v2 P7：清理会话沙箱旁的跨进程 flock 锁文件（幂等）
                    from backend.utils.session_mutex import (
                        lock_file_for,
                        remove_lock_file,
                    )

                    remove_lock_file(lock_file_for(child))
                    removed += 1
                    logger.info(
                        "Expired sandbox cleaned: %s (last modified: %s, retention days: %d)",
                        child.name,
                        _format_mtime(latest_mtime),
                        tg.cleanup_retention_days,
                    )
                except OSError as e:
                    logger.error("Failed to clean sandbox: %s — %s", child.name, e)

    if removed > 0:
        logger.info("This cleanup removed %d expired sandboxes", removed)
        # v2 P4 检查点：TTL 清理触达 templates 分类，重扫受影响用户的缓存行
        from backend.core.multiuser.quota import resync_category_all_users

        resync_category_all_users("templates")

    return removed


def _latest_mtime(directory: Path) -> float:
    """递归获取目录内所有文件的最大修改时间。"""
    latest = directory.stat().st_mtime
    for p in directory.rglob("*"):
        if p.is_file():
            mt = p.stat().st_mtime
            if mt > latest:
                latest = mt
    return latest


def _format_mtime(ts: float) -> str:
    """将 Unix 时间戳格式化为可读日期字符串。"""
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


async def cleanup_loop() -> None:
    """后台循环：按配置间隔定期执行沙箱清理。

    由 FastAPI lifespan 启动，通过 asyncio.Task 管理生命周期。
    服务器关闭时通过 cancel 终止。
    """
    logger.info("Sandbox cleanup service has started")

    while True:
        try:
            cfg = get_config()
            interval = cfg.features.template_gen.cleanup_interval_seconds
        except Exception:
            interval = 3600

        try:
            removed = await asyncio.to_thread(cleanup_expired_sandboxes)
            if removed > 0:
                logger.info(
                    "Periodic cleanup completed, deleted %d expired sandboxes", removed
                )
        except Exception:
            logger.exception("Sandbox cleanup execution error")

        await asyncio.sleep(interval)

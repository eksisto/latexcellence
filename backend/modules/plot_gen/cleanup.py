"""科研绘图 session 沙箱过期清理。"""

import asyncio
import datetime
import shutil
import time
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger(__name__)


def cleanup_expired_sandboxes() -> int:
    cfg = get_config()
    pg = cfg.features.plot_gen
    if not pg.cleanup_enabled:
        return 0

    # 收集待扫描的沙箱根目录：
    #   - 单用户模式：存量全局目录（保持既有行为）
    #   - 多用户模式：每个用户的 plots 目录
    from backend.core.multiuser import userspace

    roots: list[Path] = []
    if not userspace.is_multiuser():
        legacy = Path(pg.output_dir)
        if not legacy.is_absolute():
            legacy = Path.cwd() / legacy
        if legacy.is_dir():
            roots.append(legacy)
    else:
        roots = userspace.iterate_user_resource_roots("plots")

    now = time.time()
    cutoff = now - pg.cleanup_retention_days * 86400
    removed = 0
    for output_dir in roots:
        if not output_dir.is_dir():
            continue
        for child in sorted(output_dir.iterdir()):
            if not child.is_dir():
                continue
            latest_mtime = _latest_mtime(child)
            if latest_mtime < cutoff:
                try:
                    shutil.rmtree(child)
                    removed += 1
                    logger.info(
                        "Expired plot session cleaned: %s (last modified: %s)",
                        child.name,
                        _format_mtime(latest_mtime),
                    )
                except OSError as e:
                    logger.error("Failed to clean plot session %s: %s", child.name, e)
    if removed:
        # v2 P4 检查点：TTL 清理触达 plots 分类，重扫受影响用户的缓存行
        from backend.core.multiuser.quota import resync_category_all_users

        resync_category_all_users("plots")
    return removed


def _latest_mtime(directory: Path) -> float:
    latest = directory.stat().st_mtime
    for p in directory.rglob("*"):
        if p.is_file():
            latest = max(latest, p.stat().st_mtime)
    return latest


def _format_mtime(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


async def cleanup_loop() -> None:
    logger.info("Plot session cleanup service has started")
    while True:
        try:
            interval = get_config().features.plot_gen.cleanup_interval_seconds
        except Exception:
            interval = 3600
        try:
            removed = await asyncio.to_thread(cleanup_expired_sandboxes)
            if removed:
                logger.info("Plot cleanup removed %d expired sessions", removed)
        except Exception:
            logger.exception("Plot cleanup execution error")
        await asyncio.sleep(interval)

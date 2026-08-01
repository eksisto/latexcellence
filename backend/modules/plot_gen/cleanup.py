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
    output_dir = Path(pg.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    if not output_dir.is_dir():
        return 0

    now = time.time()
    cutoff = now - pg.cleanup_retention_days * 86400
    removed = 0
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

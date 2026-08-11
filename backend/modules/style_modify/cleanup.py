"""style_modify Agent 会话清理。"""

import asyncio
import json
import shutil
import time
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger(__name__)


def cleanup_expired_sessions() -> int:
    cfg = get_config()
    sm = cfg.features.style_modify.agent
    if not sm.cleanup_enabled:
        return 0

    sessions_dir = Path(sm.sessions_dir)
    if not sessions_dir.is_absolute():
        sessions_dir = Path.cwd() / sessions_dir
    sessions_dir = sessions_dir.resolve()
    if not sessions_dir.is_dir():
        return 0

    now = time.time()
    cutoff = now - sm.cleanup_retention_days * 86400
    removed = 0
    for child in sorted(sessions_dir.iterdir()):
        if not child.is_dir() or child.name == "operations":
            continue
        meta_path = child / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") in ("running", "applying"):
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        latest = _latest_mtime(child)
        if latest < cutoff:
            try:
                shutil.rmtree(child)
                removed += 1
                logger.info("Expired style_modify session cleaned: %s", child.name)
            except OSError as e:
                logger.error(
                    "Failed to clean style_modify session %s: %s", child.name, e
                )

    operations_dir = sessions_dir / "operations"
    if operations_dir.is_dir():
        for child in sorted(operations_dir.iterdir()):
            if not child.is_dir():
                continue
            latest = _latest_mtime(child)
            if latest < cutoff:
                try:
                    shutil.rmtree(child)
                    removed += 1
                except OSError as e:
                    logger.error(
                        "Failed to clean style_modify operation %s: %s",
                        child.name,
                        e,
                    )
    return removed


def _latest_mtime(directory: Path) -> float:
    latest = directory.stat().st_mtime
    for p in directory.rglob("*"):
        if p.is_file():
            mt = p.stat().st_mtime
            if mt > latest:
                latest = mt
    return latest


async def cleanup_loop() -> None:
    logger.info("style_modify session cleanup service started")
    while True:
        try:
            cfg = get_config()
            interval = cfg.features.style_modify.agent.cleanup_interval_seconds
        except Exception:
            interval = 3600
        try:
            removed = await asyncio.to_thread(cleanup_expired_sessions)
            if removed > 0:
                logger.info("style_modify cleanup removed %d items", removed)
        except Exception:
            logger.exception("style_modify cleanup execution error")
        await asyncio.sleep(interval)

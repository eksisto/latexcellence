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

    # 收集待扫描的会话根目录：
    #   - 单用户模式：存量全局目录（保持既有行为）
    #   - 多用户模式：每个用户的 style_modify_sessions 目录
    from backend.core.multiuser import userspace

    roots: list[Path] = []
    if not userspace.is_multiuser():
        legacy = Path(sm.sessions_dir)
        if not legacy.is_absolute():
            legacy = Path.cwd() / legacy
        legacy = legacy.resolve()
        if legacy.is_dir():
            roots.append(legacy)
    else:
        roots = userspace.iterate_user_resource_roots("style_modify_sessions")

    if not roots:
        return 0

    now = time.time()
    cutoff = now - sm.cleanup_retention_days * 86400
    removed = 0
    for sessions_dir in roots:
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
                    # v2 P7：清理会话目录旁的跨进程 flock 锁文件（幂等）
                    from backend.utils.session_mutex import (
                        lock_file_for,
                        remove_lock_file,
                    )

                    remove_lock_file(lock_file_for(child))
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
    if removed:
        # v2 P4 检查点：TTL 清理触达 style_modify_sessions 分类，
        # 重扫受影响用户的缓存行
        from backend.core.multiuser.quota import resync_category_all_users

        resync_category_all_users("style_modify_sessions")
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

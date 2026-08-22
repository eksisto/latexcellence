"""写作会话保留期清理（多用户 v2 P4）。

`multi_user.writing_chat_retention_days`（默认 0 = 不自动删，仅由
`/api/auth/me` 的分类用量统计口径覆盖）大于 0 时，由多用户维护循环周期
调用本模块：删除最近修改时间超过保留期的写作会话文件。

目录布局（见 userspace.DERIVED_SUBDIRS 与 writing_chat/context.py）：

    <users_root>/<user_id>/writing_chat_sessions/<scope>/<uuid>.json

约定：
    - 只在多用户模式生效；单用户模式的存量全局目录不在本清理范围内
      （该配置属于 multi_user 节，单用户无配额/治理语义）
    - 按文件 mtime 判定过期，与 template_gen / plot_gen / style_modify 的
      既有 TTL 清理判定方式一致
    - 清理后 prune 空的 scope 子目录；有删除时即时重扫受影响用户的
      writing_chat_sessions 分类缓存行（与三个模块 TTL 清理的检查点口径
      一致，不依赖下一次对账才可见）
"""

import time
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

logger = get_logger("multiuser.writing_chat_cleanup")


def purge_expired_writing_chats() -> int:
    """删除超过 writing_chat_retention_days 未活动的写作会话文件。

    返回删除的会话文件数。保留期为 0 或非多用户模式时不做任何删除。
    """
    retention_days = get_config().multi_user.writing_chat_retention_days
    if retention_days <= 0:
        return 0

    from backend.core.multiuser import userspace

    roots = userspace.iterate_user_resource_roots("writing_chat_sessions")
    if not roots:
        return 0

    cutoff = time.time() - retention_days * 86400
    removed = 0
    for sessions_dir in roots:
        for entry in sorted(sessions_dir.iterdir()):
            try:
                if not entry.is_dir():
                    # 游离的散文件（异常布局）同样按 mtime 判定
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        entry.unlink()
                        removed += 1
                    continue
                removed += _purge_scope_dir(entry, cutoff)
                _prune_if_empty(entry)
            except OSError:
                continue
    if removed:
        logger.info(
            "Writing chat retention purge: %d session(s) removed "
            "(retention=%d days)",
            removed,
            retention_days,
        )
        # v2 P4 检查点：删除触达 writing_chat_sessions 分类，即时重扫受影响
        # 用户的缓存行（维护循环内 reconcile 先行、purge 后执行，若不在此
        # 刷新，删除量要到下一轮对账才从缓存消失）
        from backend.core.multiuser.quota import resync_category_all_users

        resync_category_all_users("writing_chat_sessions")
    return removed


def _purge_scope_dir(scope_dir: Path, cutoff: float) -> int:
    """删除 scope 目录内过期的会话 JSON 文件，返回删除数。"""
    removed = 0
    for session_file in sorted(scope_dir.glob("*.json")):
        try:
            if session_file.stat().st_mtime >= cutoff:
                continue
            session_file.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _prune_if_empty(directory: Path) -> None:
    """scope 子目录已空时收尾删除，避免目录骨架无限堆积。"""
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass

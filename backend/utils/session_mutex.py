"""跨进程会话互斥（flock 锁文件，v2 方案 §12.1 / P7）。

style_modify / template_gen / plot_gen 的会话级互斥原为进程内
asyncio.Lock——多副本部署下两个副本可同时写同一会话目录。本模块在会话
目录**旁**放置 `<session_id>.lock` 锁文件并以 flock 互斥（同机共享卷上
跨进程/跨副本有效）；调用方保留进程内 asyncio.Lock 叠加，形成两级互斥：

    进程内：asyncio.Lock（零开销快路径）
    跨进程：flock 锁文件（覆盖其他副本 / 同机其他进程）

放置约定：锁文件位于会话目录同级（`<root>/<session_id>.lock`），不进
会话目录本身——避免被 zip 打包、rglob 扫描与用量统计误收；会话删除 /
TTL 清理时由 :func:`remove_lock_file` 一并清理。

平台说明：flock 为 POSIX 能力（Linux/macOS）。Windows 上 fcntl 缺失时
本模块降级为"仅进程内 asyncio.Lock 生效"并告警一次——多副本部署目标
平台是 Linux（见 deployment.md），单机 Windows 开发场景行为不变。
"""

import asyncio
import errno
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from backend.core.logger import get_logger

logger = get_logger("session_mutex")

# flock 抢锁轮询间隔（秒）：非阻塞抢锁失败后让出事件循环再试。
# 会话操作为长任务（秒级~分钟级），50ms 轮询开销可忽略且延迟感低。
FLOCK_POLL_INTERVAL_SECONDS = 0.05

try:  # 平台分叉：flock 为 POSIX 能力，Windows 缺失时降级直通
    import fcntl

    _FLOCK_AVAILABLE = True
except ImportError:  # pragma: no cover - 仅 Windows 触发
    _FLOCK_AVAILABLE = False


def lock_file_for(session_dir: Path) -> Path:
    """返回会话目录对应的锁文件路径（会话目录旁：`<dir>.lock`）。"""
    session_dir = Path(session_dir)
    return session_dir.parent / f"{session_dir.name}.lock"


def remove_lock_file(lock_path: Path) -> None:
    """删除残留的锁文件（会话删除 / TTL 清理时调用；幂等、尽力而为）。

    v2 P7 防删除竞态：flock 锁在 inode 上，若在他人持锁期间直接 unlink，
    后续进程会 os.open(O_CREAT) 新建不同 inode 的锁文件并成功加锁——
    与仍持有旧 inode 的写者失去互斥（经典陷阱）。因此 POSIX 平台先以
    非阻塞 flock 探测：抢不到说明有人正持有，跳过删除留待下轮清理
    （会话目录已删，孤儿锁文件无副作用）；抢到才 unlink。
    """
    lock_path = Path(lock_path)
    if not _FLOCK_AVAILABLE:  # pragma: no cover - 仅 Windows 触发
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as e:
            logger.debug("Failed to remove lock file %s: %s", lock_path, e)
        return
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
    except FileNotFoundError:
        return
    except OSError as e:
        logger.debug("Failed to open lock file %s for removal: %s", lock_path, e)
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # 正被持有：不删，避免新锁文件绕过互斥
        lock_path.unlink(missing_ok=True)
    finally:
        os.close(fd)


@asynccontextmanager
async def cross_process_session_lock(
    lock_path: Path,
) -> AsyncIterator[None]:
    """跨进程互斥守卫：flock 独占锁定 `lock_path` 直至退出。

    非阻塞轮询抢锁（LOCK_EX | LOCK_NB），失败即让出事件循环后重试，
    不阻塞同进程内其他协程；无超时（与会话操作的 asyncio.Lock 语义一致）。
    fcntl 不可用的平台上降级为直通（告警一次），由调用方叠加的
    进程内 asyncio.Lock 兜底单进程互斥。
    """
    if not _FLOCK_AVAILABLE:
        logger.warning(
            "fcntl unavailable on this platform; cross-process session "
            "mutex degrades to in-process only"
        )
        yield
        return
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                await asyncio.sleep(FLOCK_POLL_INTERVAL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@asynccontextmanager
async def combined_session_lock(
    session_dir: Path,
    in_process_lock: asyncio.Lock,
) -> AsyncIterator[None]:
    """组合互斥：跨进程 flock + 进程内 asyncio.Lock（P7 接线的标准形态）。

    先抢进程内锁（快路径、公平排队），再抢跨进程 flock；退出按相反顺序
    释放。供 style_modify 等已有 ``store.lock(session_id)`` 调用点的统一
    替换入口使用。
    """
    async with in_process_lock:
        async with cross_process_session_lock(lock_file_for(session_dir)):
            yield

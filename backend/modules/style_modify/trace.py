"""style_modify Agent 运行追踪。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

TRACE_FILE = "trace.jsonl"

_trace_locks: dict[str, asyncio.Lock] = {}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    lock = _trace_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _trace_locks[key] = lock
    return lock


def forget_trace_lock(session_dir: Path) -> None:
    """会话删除后移除对应的 trace 写锁，避免模块级字典无限增长。"""
    _trace_locks.pop(str((session_dir / TRACE_FILE).resolve()), None)


def _append_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def append_trace(session_dir: Path, event: dict[str, Any]) -> None:
    """追加一条追踪事件；调用方失败时只记日志，不中断 Agent。"""
    path = session_dir / TRACE_FILE
    line = json.dumps(event, ensure_ascii=False)
    async with _lock_for(path):
        await asyncio.to_thread(_append_line, path, line)


def read_trace(session_dir: Path) -> list[dict[str, Any]]:
    """读取全部追踪事件，损坏行自动跳过。"""
    path = session_dir / TRACE_FILE
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events

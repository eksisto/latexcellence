"""模板生成 Agent 运行追踪。

将 Agent 主循环的完整交互（system prompt、LLM 回复、工具调用参数与结果、
警告与错误）按时间顺序逐条追加到 session 沙箱内的 trace.jsonl，供分析、改进
prompt 与复现调试使用。trace.jsonl 随沙箱一起被 cleanup 清理，并包含在下载的
ZIP 中。
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

TRACE_FILE = "trace.jsonl"

_trace_locks: dict[str, asyncio.Lock] = {}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _lock_for(sandbox: Path) -> asyncio.Lock:
    key = str(sandbox.resolve())
    lock = _trace_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _trace_locks[key] = lock
    return lock


def _append_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def append_trace(sandbox: Path, event: dict[str, Any]) -> None:
    """追加一条追踪事件到 trace.jsonl（沙箱级锁 + 线程内写文件，防并发交错）。"""
    path = sandbox / TRACE_FILE
    line = json.dumps(event, ensure_ascii=False)
    async with _lock_for(sandbox):
        await asyncio.to_thread(_append_line, path, line)


def read_trace(sandbox: Path) -> list[dict[str, Any]]:
    """读取全部追踪事件（按时间顺序），损坏的行自动跳过。"""
    path = sandbox / TRACE_FILE
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

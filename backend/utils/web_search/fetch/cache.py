"""Bounded disk cache for raw responses and extracted documents."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from backend.utils.file_ops import atomic_write


class WebCache:
    """Content-addressed cache which never stores request credentials."""

    def __init__(self, root: Path, ttl_seconds: int, max_entry_bytes: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_entry_bytes = max_entry_bytes
        self._lock = asyncio.Lock()

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"

    async def read(
        self, namespace: str, key: str, *, allow_expired: bool = False
    ) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        if not path.is_file():
            return None
        try:
            payload = await asyncio.to_thread(path.read_text, encoding="utf-8")
            data = json.loads(payload)
            if (
                not allow_expired
                and time.time() - float(data.get("cached_at", 0)) > self.ttl_seconds
            ):
                return None
            return data
        except (OSError, ValueError, TypeError):
            return None

    async def write(self, namespace: str, key: str, data: dict[str, Any]) -> bool:
        payload = json.dumps(
            {**data, "cached_at": time.time()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > self.max_entry_bytes:
            return False
        path = self._path(namespace, key)
        async with self._lock:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(atomic_write, path, payload)
        return True

    async def clear(self) -> None:
        """Remove cache entries without removing the configured cache root."""
        if not self.root.is_dir():
            return
        paths = [path for path in self.root.rglob("*.json") if path.is_file()]
        for path in paths:
            await asyncio.to_thread(path.unlink, missing_ok=True)

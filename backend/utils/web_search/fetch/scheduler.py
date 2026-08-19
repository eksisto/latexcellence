"""Per-domain concurrency, pacing, and circuit breaker coordination."""

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class DomainCircuitOpen(RuntimeError):
    """Raised when a domain is temporarily excluded after repeated failures."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("domain circuit open")
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _DomainState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    active: int = 0
    next_allowed_at: float = 0
    consecutive_failures: int = 0
    circuit_until: float = 0
    last_seen: float = field(default_factory=time.monotonic)


class DomainLease:
    def __init__(self, scheduler: "DomainScheduler", domain: str) -> None:
        self._scheduler = scheduler
        self.domain = domain

    async def __aenter__(self) -> "DomainLease":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self._scheduler.release(self.domain)


class DomainScheduler:
    """Coordinate all requests made by one fetch service without blocking the loop."""

    # 超过此数量时触发一次淘汰扫描
    _PRUNE_THRESHOLD = 128
    # 超过此秒数未使用的域名状态将被淘汰
    _STALE_TTL = 3600.0

    def __init__(self) -> None:
        self._states: dict[str, _DomainState] = {}
        self._states_lock = asyncio.Lock()

    async def _prune_stale_states(self) -> None:
        """移除超过 _STALE_TTL 未使用的域名状态条目，防止内存泄漏。"""
        now = time.monotonic()
        async with self._states_lock:
            stale = [
                domain
                for domain, state in self._states.items()
                if state.active == 0 and (now - state.last_seen) > self._STALE_TTL
            ]
            for domain in stale:
                del self._states[domain]

    async def acquire(
        self, url: str, concurrency: int, interval_seconds: float
    ) -> DomainLease:
        domain = (urlsplit(url).hostname or "").casefold()
        async with self._states_lock:
            state = self._states.setdefault(domain, _DomainState())
        state.last_seen = time.monotonic()
        if len(self._states) > self._PRUNE_THRESHOLD:
            await self._prune_stale_states()
        while True:
            sleep_for = 0.0
            async with state.condition:
                now = time.monotonic()
                if state.circuit_until > now:
                    raise DomainCircuitOpen(state.circuit_until - now)
                if state.active < concurrency and now >= state.next_allowed_at:
                    state.active += 1
                    state.next_allowed_at = now + interval_seconds
                    return DomainLease(self, domain)
                if now < state.next_allowed_at:
                    sleep_for = state.next_allowed_at - now
                else:
                    await state.condition.wait()
                    continue
            await asyncio.sleep(sleep_for)

    async def release(self, domain: str) -> None:
        state = self._states.get(domain)
        if state is None:
            return
        async with state.condition:
            state.active = max(0, state.active - 1)
            state.last_seen = time.monotonic()
            state.condition.notify_all()

    async def record_success(self, url: str) -> None:
        state = self._states.get((urlsplit(url).hostname or "").casefold())
        if state is None:
            return
        async with state.condition:
            state.consecutive_failures = 0
            state.circuit_until = 0
            state.last_seen = time.monotonic()
            state.condition.notify_all()

    async def record_failure(
        self, url: str, threshold: int, cooldown_seconds: float
    ) -> None:
        state = self._states.get((urlsplit(url).hostname or "").casefold())
        if state is None:
            return
        async with state.condition:
            state.consecutive_failures += 1
            if state.consecutive_failures >= threshold:
                state.circuit_until = time.monotonic() + cooldown_seconds
            state.last_seen = time.monotonic()
            state.condition.notify_all()

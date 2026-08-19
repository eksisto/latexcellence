"""Resilient HTTP fetch coordinator with typed failures and browser fallback."""

import asyncio
import base64
import hashlib
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.errors import FetchArtifact, FetchFailure

from .browser import BrowserFetchResult, BrowserHealth, BrowserPool
from .cache import WebCache
from .detector import BlockageDetection, detect_blockage
from .policy import url_has_credentials, url_is_safe
from .scheduler import DomainCircuitOpen, DomainScheduler


class FetchResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    artifact: FetchArtifact | None = None
    body: bytes = b""
    failure: FetchFailure | None = None


class FetchCoordinator:
    """Fetch public HTTPS resources under shared retry and domain budgets."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._scheduler = DomainScheduler()
        self._browser = BrowserPool()
        self._logger = get_logger("web_fetch")

    def _config(self) -> Any:
        return get_config().features.function_calling.web_search

    def _cache(self) -> WebCache:
        cfg = self._config().page_reading
        return WebCache(
            root=Path(cfg.cache_dir),
            ttl_seconds=cfg.cache_ttl_seconds,
            max_entry_bytes=cfg.max_cache_entry_mb * 1024 * 1024,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                headers={
                    "User-Agent": get_config().http.user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain,application/xml;q=0.9,*/*;q=0.5",
                },
            )
        return self._client

    async def initialize_browser(self) -> BrowserHealth:
        return await self._browser.initialize()

    def browser_health(self) -> BrowserHealth:
        return self._browser.health()

    async def fetch_browser(self, url: str) -> BrowserFetchResult:
        limit = self._config().page_reading.max_response_mb * 1024 * 1024
        return await self._browser.fetch(url, limit)

    async def close(self) -> None:
        await self._browser.close()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, timeout: float | None = None) -> FetchResult:
        started = time.monotonic()
        if url_has_credentials(url):
            return self._failure(
                "credentialed_url", "policy", "security", False, started
            )
        cfg = self._config()
        page_cfg = cfg.page_reading
        cache = self._cache()
        cache_key = cache.key(url)
        cached = await cache.read("raw", cache_key) if page_cfg.cache_enabled else None
        if cached:
            return self._cached_result(url, cache_key, cached)
        stale = (
            await cache.read("raw", cache_key, allow_expired=True)
            if page_cfg.cache_enabled
            else None
        )
        headers: dict[str, str] = {}
        if stale:
            if stale.get("etag"):
                headers["If-None-Match"] = stale["etag"]
            if stale.get("last_modified"):
                headers["If-Modified-Since"] = stale["last_modified"]

        attempts = page_cfg.max_retries + 1
        last_failure: FetchResult | None = None
        for attempt in range(1, attempts + 1):
            result = await self._fetch_once(
                url,
                cache_key,
                headers,
                stale,
                timeout or cfg.timeout_seconds,
                started,
                attempt,
            )
            if result.artifact is not None:
                await self._scheduler.record_success(url)
                if page_cfg.cache_enabled and result.artifact.cache_hit and stale:
                    await cache.write(
                        "raw",
                        cache_key,
                        {
                            key: value
                            for key, value in stale.items()
                            if key != "cached_at"
                        },
                    )
                elif page_cfg.cache_enabled:
                    await cache.write(
                        "raw",
                        cache_key,
                        {
                            "artifact": result.artifact.model_dump(mode="json"),
                            "body_b64": base64.b64encode(result.body).decode(),
                            "etag": result.artifact.etag,
                            "last_modified": result.artifact.last_modified,
                        },
                    )
                return result
            last_failure = result
            failure = result.failure
            if failure is None or not failure.retryable:
                break
            await self._scheduler.record_failure(
                url,
                page_cfg.circuit_failure_threshold,
                page_cfg.circuit_cooldown_seconds,
            )
            if attempt >= attempts:
                break
            delay = self._retry_delay(failure.retry_after_seconds, attempt, page_cfg)
            self._logger.info(
                "Fetch retry phase=%s code=%s attempt=%d delay_ms=%d",
                failure.phase,
                failure.code,
                attempt,
                int(delay * 1000),
            )
            await asyncio.sleep(delay)

        return last_failure or self._failure(
            "network_error", "http", "network", True, started
        )

    async def _fetch_once(
        self,
        url: str,
        cache_key: str,
        headers: dict[str, str],
        stale: dict[str, Any] | None,
        timeout: float,
        started: float,
        attempt: int,
    ) -> FetchResult:
        page_cfg = self._config().page_reading
        current = url
        downloaded = 0
        for _ in range(page_cfg.max_redirects + 1):
            if not await url_is_safe(current):
                return self._failure(
                    "unsafe_url", "policy", "security", False, started, attempts=attempt
                )
            try:
                lease = await self._scheduler.acquire(
                    current,
                    page_cfg.per_domain_concurrency,
                    page_cfg.min_domain_interval_ms / 1000,
                )
            except DomainCircuitOpen as exc:
                return self._failure(
                    "circuit_open",
                    "schedule",
                    "remote",
                    True,
                    started,
                    retry_after=exc.retry_after_seconds,
                    attempts=attempt,
                )
            try:
                async with lease:
                    async with self._get_client().stream(
                        "GET", current, headers=headers, timeout=timeout
                    ) as response:
                        status = response.status_code
                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                return self._failure(
                                    "invalid_redirect",
                                    "http",
                                    "protocol",
                                    False,
                                    started,
                                    attempts=attempt,
                                )
                            current = urljoin(current, location)
                            if url_has_credentials(current):
                                return self._failure(
                                    "credentialed_url",
                                    "policy",
                                    "security",
                                    False,
                                    started,
                                    attempts=attempt,
                                )
                            headers = {}
                            continue
                        if status == 304 and stale:
                            return self._cached_result(url, cache_key, stale)

                        limit = page_cfg.max_response_mb * 1024 * 1024
                        declared = response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > limit:
                            return self._failure(
                                "response_too_large",
                                "http",
                                "resource",
                                False,
                                started,
                                int(declared),
                                attempts=attempt,
                            )
                        chunks: list[bytes] = []
                        async for chunk in response.aiter_bytes():
                            downloaded += len(chunk)
                            if downloaded > limit:
                                return self._failure(
                                    "response_too_large",
                                    "http",
                                    "resource",
                                    False,
                                    started,
                                    downloaded,
                                    attempts=attempt,
                                )
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        mime = (
                            response.headers.get(
                                "content-type", "application/octet-stream"
                            )
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        response_headers = {
                            key.casefold(): value
                            for key, value in response.headers.items()
                        }
            except httpx.TimeoutException:
                return self._failure(
                    "timeout", "http", "network", True, started, attempts=attempt
                )
            except httpx.HTTPError as exc:
                self._logger.warning(
                    "Structured fetch failed phase=http url=%s error=%s",
                    current[:100],
                    exc,
                )
                return self._failure(
                    "network_error",
                    "http",
                    "network",
                    True,
                    started,
                    attempts=attempt,
                )

            detection = await asyncio.to_thread(
                detect_blockage, status, response_headers, body, mime
            )
            if detection is not None:
                return self._detected_failure(
                    detection,
                    started,
                    downloaded,
                    status,
                    response_headers,
                    attempt,
                )
            if status >= 400:
                return self._failure(
                    "http_error",
                    "http",
                    "remote",
                    status >= 500,
                    started,
                    downloaded,
                    status,
                    attempts=attempt,
                )

            content_hash = hashlib.sha256(body).hexdigest()
            artifact = FetchArtifact(
                requested_url=url,
                final_url=current,
                fetched_at=datetime.now(timezone.utc),
                mime_type=mime,
                strategy="http",
                content_hash=content_hash,
                raw_size=downloaded,
                cache_key=cache_key,
                etag=response_headers.get("etag", ""),
                last_modified=response_headers.get("last-modified", ""),
            )
            self._logger.info(
                "Fetch complete phase=http status=%d elapsed_ms=%.1f bytes=%d attempt=%d",
                status,
                (time.monotonic() - started) * 1000,
                downloaded,
                attempt,
            )
            return FetchResult(artifact=artifact, body=body)
        return self._failure(
            "redirect_limit",
            "http",
            "protocol",
            False,
            started,
            downloaded,
            attempts=attempt,
        )

    @staticmethod
    def _retry_delay(retry_after: float | None, attempt: int, cfg: Any) -> float:
        if retry_after is not None:
            return min(retry_after, cfg.max_retry_after_seconds)
        exponential = cfg.backoff_base_seconds * (2 ** (attempt - 1))
        return min(exponential + random.uniform(0, exponential * 0.2), 10.0)

    @staticmethod
    def _retry_after(headers: dict[str, str]) -> float | None:
        value = headers.get("retry-after", "").strip()
        if not value:
            return None
        if value.isdigit():
            return float(value)
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def _detected_failure(
        self,
        detection: BlockageDetection,
        started: float,
        downloaded: int,
        status: int,
        headers: dict[str, str],
        attempt: int,
    ) -> FetchResult:
        self._logger.info(
            "Fetch blocked phase=detect code=%s confidence=%.2f status=%d signals=%s",
            detection.code,
            detection.confidence,
            status,
            ",".join(detection.signals),
        )
        return self._failure(
            detection.code,
            "detect",
            "remote",
            detection.retryable,
            started,
            downloaded,
            status,
            retry_after=self._retry_after(headers),
            confidence=detection.confidence,
            signals=detection.signals,
            attempts=attempt,
        )

    @staticmethod
    def _cached_result(url: str, cache_key: str, data: dict[str, Any]) -> FetchResult:
        artifact = FetchArtifact.model_validate(data["artifact"])
        artifact.requested_url = url
        artifact.cache_hit = True
        artifact.cache_key = cache_key
        body_raw = data.get("body_b64") or data.get("body_hex", "")
        body = base64.b64decode(body_raw) if body_raw else b""
        return FetchResult(artifact=artifact, body=body)

    @staticmethod
    def _failure(
        code: str,
        phase: str,
        category: str,
        retryable: bool,
        started: float,
        downloaded: int = 0,
        status: int | None = None,
        retry_after: float | None = None,
        confidence: float | None = None,
        signals: list[str] | None = None,
        attempts: int = 1,
    ) -> FetchResult:
        return FetchResult(
            failure=FetchFailure(
                code=code,
                phase=phase,
                category=category,
                retryable=retryable,
                status_code=status,
                message=code.replace("_", " "),
                elapsed_ms=(time.monotonic() - started) * 1000,
                downloaded_bytes=downloaded,
                retry_after_seconds=retry_after,
                confidence=confidence,
                signals=signals or [],
                attempts=attempts,
            )
        )

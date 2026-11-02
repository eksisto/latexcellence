"""Optional, resource-bounded Playwright fallback for JavaScript documents."""

import asyncio
import hashlib
import importlib.util
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.errors import FetchArtifact, FetchFailure

from .detector import detect_blockage
from .policy import url_is_safe


def _playwright_error_type() -> type:
    """Return the Playwright ``Error`` type, or a harmless fallback if unavailable."""
    try:
        from playwright.async_api import Error as PwError

        return PwError
    except (ImportError, ModuleNotFoundError):

        class _FallbackError(Exception):
            """Placeholder that never matches real Playwright errors."""

        return _FallbackError


_PLAYWRIGHT_ERROR = _playwright_error_type()


class BrowserHealth(BaseModel):
    """Runtime browser capability state exposed for health reporting."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    package_available: bool
    ready: bool
    active_pages: int = Field(ge=0)
    last_error: str = ""


class BrowserFetchResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    artifact: FetchArtifact | None = None
    body: bytes = b""
    failure: FetchFailure | None = None


async def browser_url_is_allowed(url: str) -> bool:
    """Allow inert browser URLs locally and revalidate every network request."""
    scheme = urlsplit(url).scheme.casefold()
    if scheme in {"about", "data", "blob"}:
        return True
    if scheme != "https":
        return False
    return await url_is_safe(url)


class BrowserPool:
    """Lazy Chromium pool using isolated contexts and a hard page semaphore."""

    def __init__(self) -> None:
        self._logger = get_logger("web_browser")
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._semaphore: asyncio.Semaphore | None = None
        self._pool_size = 0
        self._active_pages = 0
        self._last_error = ""

    def _config(self) -> Any:
        return get_config().features.function_calling.web_search.page_reading.browser

    async def initialize(self) -> BrowserHealth:
        cfg = self._config()
        if cfg.enabled:
            await self._ensure_started(cfg.pool_size)
        return self.health()

    def health(self) -> BrowserHealth:
        cfg = self._config()
        return BrowserHealth(
            enabled=cfg.enabled,
            package_available=self._package_available(),
            ready=self._browser is not None,
            active_pages=self._active_pages,
            last_error=self._last_error,
        )

    async def _ensure_started(self, pool_size: int) -> bool:
        async with self._lock:
            if self._browser is not None and self._pool_size == pool_size:
                return True
            if self._browser is not None:
                if self._active_pages > 0:
                    return True
                await self._close_locked()
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                self._pool_size = pool_size
                self._semaphore = asyncio.Semaphore(pool_size)
                self._last_error = ""
                self._logger.info("Browser fallback ready pool_size=%d", pool_size)
                return True
            except Exception as exc:
                self._last_error = str(exc)[:300]
                self._logger.warning("Browser fallback unavailable: %s", exc)
                await self._close_locked()
                return False

    async def fetch(self, url: str, max_bytes: int) -> BrowserFetchResult:
        started = time.monotonic()
        cfg = self._config()
        if not cfg.enabled:
            return self._failure("browser_disabled", started)
        if not await browser_url_is_allowed(url):
            return self._failure("unsafe_url", started, category="security")
        if not await self._ensure_started(cfg.pool_size) or self._semaphore is None:
            return self._failure("browser_unavailable", started, retryable=True)

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=cfg.page_timeout_seconds
            )
        except TimeoutError:
            return self._failure("browser_pool_timeout", started, retryable=True)

        context: Any = None
        self._active_pages += 1
        try:
            context = await self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                java_script_enabled=True,
                service_workers="block",
            )
            await context.clear_permissions()
            page = await context.new_page()

            async def route_request(route: Any) -> None:
                if await browser_url_is_allowed(route.request.url):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await context.route("**/*", route_request)
            page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=cfg.page_timeout_seconds * 1000,
            )
            await self._wait_for_stable_body(page, cfg.stable_window_ms)
            final_url = page.url
            if not await browser_url_is_allowed(final_url):
                return self._failure("unsafe_navigation", started, category="security")
            html = await page.content()
            body = html.encode("utf-8")
            if len(body) > max_bytes:
                return self._failure("response_too_large", started, category="resource")
            status = response.status if response is not None else 200
            headers = (
                {
                    key.casefold(): value
                    for key, value in (await response.all_headers()).items()
                }
                if response is not None
                else {}
            )
            # 拦截判定对全文跑 BS4 解析，CPU 密集，入线程池（与 coordinator
            # 同一函数的既有 to_thread 口径一致）
            detection = await asyncio.to_thread(
                detect_blockage, status, headers, body, "text/html"
            )
            if detection and detection.code != "javascript_required":
                return self._failure(
                    detection.code,
                    started,
                    confidence=detection.confidence,
                    signals=detection.signals,
                )
            content_hash = hashlib.sha256(body).hexdigest()
            artifact = FetchArtifact(
                requested_url=url,
                final_url=final_url,
                fetched_at=datetime.now(timezone.utc),
                mime_type="text/html",
                strategy="browser",
                content_hash=content_hash,
                raw_size=len(body),
                cache_key=hashlib.sha256(url.encode("utf-8")).hexdigest(),
            )
            self._logger.info(
                "Browser fetch complete phase=browser elapsed_ms=%.1f bytes=%d",
                (time.monotonic() - started) * 1000,
                len(body),
            )
            return BrowserFetchResult(artifact=artifact, body=body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)[:300]
            self._logger.warning("Browser fetch failed: %s", exc)
            return self._failure("browser_error", started, retryable=True)
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                self._active_pages = max(0, self._active_pages - 1)
                self._semaphore.release()

    @staticmethod
    async def _wait_for_stable_body(page: Any, stable_window_ms: int) -> None:
        interval = max(0.1, stable_window_ms / 3000)
        stable_samples = 0
        previous = -1
        _pw_error = _PLAYWRIGHT_ERROR
        for _ in range(6):
            try:
                current = len(await page.locator("body").inner_text(timeout=2000))
            except (_pw_error, TimeoutError, OSError):
                return
            if current == previous and current > 0:
                stable_samples += 1
                if stable_samples >= 2:
                    return
            else:
                stable_samples = 0
            previous = current
            await asyncio.sleep(interval)

    @staticmethod
    def _failure(
        code: str,
        started: float,
        retryable: bool = False,
        category: str = "browser",
        confidence: float | None = None,
        signals: list[str] | None = None,
    ) -> BrowserFetchResult:
        return BrowserFetchResult(
            failure=FetchFailure(
                code=code,
                phase="browser",
                category=category,
                retryable=retryable,
                message=code.replace("_", " "),
                elapsed_ms=(time.monotonic() - started) * 1000,
                downloaded_bytes=0,
                confidence=confidence,
                signals=signals or [],
            )
        )

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    @staticmethod
    def _package_available() -> bool:
        try:
            return importlib.util.find_spec("playwright.async_api") is not None
        except (ImportError, ModuleNotFoundError):
            return False

    async def _close_locked(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._semaphore = None
        self._pool_size = 0

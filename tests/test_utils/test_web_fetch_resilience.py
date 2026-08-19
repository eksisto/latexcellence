"""Stage 3 blocked-page detection, retry, scheduling, and browser policy tests."""

import asyncio

import httpx
import pytest

from backend.core.config import get_config
from backend.utils.web_search.fetch.browser import (
    BrowserPool,
    browser_url_is_allowed,
)
from backend.utils.web_search.fetch.coordinator import FetchCoordinator
from backend.utils.web_search.fetch.detector import detect_blockage
from backend.utils.web_search.fetch.scheduler import (
    DomainCircuitOpen,
    DomainScheduler,
)


def test_blockage_detector_requires_structural_captcha_signal() -> None:
    article = b"""
    <html><head><title>Security research</title></head>
    <body><main><h1>How CAPTCHA systems work</h1>
    <p>This article discusses captcha accessibility and evaluation methods.</p>
    </main></body></html>
    """
    challenge = b"""
    <html><head><title>Verify you are human</title></head>
    <body><form><div class="g-recaptcha"></div></form></body></html>
    """

    assert detect_blockage(200, {}, article, "text/html") is None
    result = detect_blockage(403, {}, challenge, "text/html")

    assert result is not None
    assert result.code == "captcha_required"
    assert result.confidence >= 0.9


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b"<html><body><div id='root'></div><noscript>Enable JavaScript to continue</noscript><script src='app.js'></script></body></html>",
            "javascript_required",
        ),
        (
            b"<html><body><div class='paywall'>Subscribe to continue reading</div></body></html>",
            "paywall_detected",
        ),
        (
            b"<html><head><title>Log in</title></head><body><form><input type='password'></form></body></html>",
            "access_controlled",
        ),
    ],
)
def test_blockage_detector_classifies_explicit_barriers(
    body: bytes, expected: str
) -> None:
    result = detect_blockage(200, {}, body, "text/html")

    assert result is not None
    assert result.code == expected


@pytest.mark.asyncio
async def test_fetch_honors_retry_after_and_returns_second_attempt(
    monkeypatch, tmp_path
) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading
    monkeypatch.setattr(cfg, "cache_enabled", False)
    monkeypatch.setattr(cfg, "cache_dir", str(tmp_path / "web-cache"))
    monkeypatch.setattr(cfg, "max_retries", 1)
    monkeypatch.setattr(cfg, "backoff_base_seconds", 0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                request=request,
                headers={"Retry-After": "0", "Content-Type": "text/plain"},
                content=b"slow down",
            )
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/plain"},
            content=b"ready",
        )

    coordinator = FetchCoordinator()
    coordinator._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def safe_url(_url: str) -> bool:
        return True

    monkeypatch.setattr(
        "backend.utils.web_search.fetch.coordinator.url_is_safe", safe_url
    )
    result = await coordinator.fetch("https://example.test/retry")
    await coordinator.close()

    assert result.artifact is not None
    assert result.body == b"ready"
    assert calls == 2


@pytest.mark.asyncio
async def test_domain_scheduler_opens_and_recovers_circuit(monkeypatch) -> None:
    scheduler = DomainScheduler()
    lease = await scheduler.acquire("https://example.test/a", 1, 0)
    await lease.__aexit__()
    await scheduler.record_failure("https://example.test/a", 1, 0.01)

    with pytest.raises(DomainCircuitOpen):
        await scheduler.acquire("https://example.test/b", 1, 0)

    await asyncio.sleep(0.02)
    recovered = await scheduler.acquire("https://example.test/c", 1, 0)
    await recovered.__aexit__()


@pytest.mark.asyncio
async def test_fetch_final_retryable_failure_counts_toward_circuit(
    monkeypatch, tmp_path
) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading
    monkeypatch.setattr(cfg, "cache_enabled", False)
    monkeypatch.setattr(cfg, "cache_dir", str(tmp_path / "web-cache"))
    monkeypatch.setattr(cfg, "max_retries", 0)
    monkeypatch.setattr(cfg, "circuit_failure_threshold", 1)
    monkeypatch.setattr(cfg, "circuit_cooldown_seconds", 30)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, content=b"unavailable")

    coordinator = FetchCoordinator()
    coordinator._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def safe_url(_url: str) -> bool:
        return True

    monkeypatch.setattr(
        "backend.utils.web_search.fetch.coordinator.url_is_safe", safe_url
    )
    first = await coordinator.fetch("https://example.test/a")
    second = await coordinator.fetch("https://example.test/b")
    await coordinator.close()

    assert first.failure is not None and first.failure.code == "http_error"
    assert second.failure is not None and second.failure.code == "circuit_open"


@pytest.mark.asyncio
async def test_browser_policy_rejects_cross_protocol_and_private_navigation(
    monkeypatch,
) -> None:
    async def only_public(url: str) -> bool:
        return "127.0.0.1" not in url

    monkeypatch.setattr(
        "backend.utils.web_search.fetch.browser.url_is_safe", only_public
    )

    assert await browser_url_is_allowed("https://example.test/page")
    assert not await browser_url_is_allowed("http://example.test/page")
    assert not await browser_url_is_allowed("file:///etc/passwd")
    assert not await browser_url_is_allowed("https://127.0.0.1/admin")


@pytest.mark.asyncio
async def test_disabled_browser_fails_without_starting_runtime(monkeypatch) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading.browser
    monkeypatch.setattr(cfg, "enabled", False)
    pool = BrowserPool()

    result = await pool.fetch("https://example.test/page", 1024)

    assert result.failure is not None
    assert result.failure.code == "browser_disabled"
    assert pool._browser is None

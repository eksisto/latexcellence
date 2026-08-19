"""Structured page-reading service used by Function Calling tools."""

import asyncio
import hashlib
from pathlib import Path
from typing import Literal

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.errors import FetchFailure
from backend.utils.web_search.extract.chunking import collect_evidence, select_evidence
from backend.utils.web_search.extract.models import EvidenceChunk, ExtractedDocument
from backend.utils.web_search.extract.registry import ExtractionError, ExtractorRegistry
from backend.utils.web_search.fetch.cache import WebCache
from backend.utils.web_search.fetch.browser import BrowserHealth
from backend.utils.web_search.fetch.coordinator import FetchCoordinator


class StructuredPageReader:
    """Fetch, extract, cache, and retrieve evidence from web documents."""

    def __init__(self) -> None:
        self.fetcher = FetchCoordinator()
        self.extractors = ExtractorRegistry()
        self._logger = get_logger("structured_page_reader")

    def _cache(self) -> WebCache:
        cfg = get_config().features.function_calling.web_search.page_reading
        return WebCache(
            Path(cfg.cache_dir),
            cfg.cache_ttl_seconds,
            cfg.max_cache_entry_mb * 1024 * 1024,
        )

    async def get_document(
        self, url: str
    ) -> tuple[ExtractedDocument | None, FetchFailure | None, bool]:
        fetch_url = self.extractors.preferred_fetch_url(url)
        fetched = await self.fetcher.fetch(fetch_url)
        page_cfg = get_config().features.function_calling.web_search.page_reading
        if (
            fetched.failure
            and fetched.failure.code in {"javascript_required", "challenge_required"}
            and page_cfg.browser.enabled
        ):
            browser_result = await self.fetcher.fetch_browser(fetch_url)
            if browser_result.artifact is not None:
                fetched.artifact = browser_result.artifact
                fetched.body = browser_result.body
                fetched.failure = None
            else:
                return None, browser_result.failure, False
        if fetched.failure or fetched.artifact is None:
            return None, fetched.failure, False
        extraction_key = hashlib.sha256(
            f"{fetched.artifact.content_hash}\0{page_cfg.extractor_version}".encode(
                "utf-8"
            )
        ).hexdigest()
        cache = self._cache()
        cached = (
            await cache.read("extracted", extraction_key)
            if page_cfg.cache_enabled
            else None
        )
        if cached:
            document = ExtractedDocument.model_validate(cached["document"])
            document.url = url
            return document, None, True
        try:
            document = await asyncio.to_thread(
                self.extractors.extract,
                fetched.body,
                fetched.artifact.mime_type,
                fetched.artifact.final_url,
                fetched.artifact.content_hash,
            )
            document.url = url
        except (ExtractionError, ValueError) as exc:
            code = exc.code if isinstance(exc, ExtractionError) else "extraction_failed"
            return (
                None,
                FetchFailure(
                    code=code,
                    phase="extract",
                    category="content",
                    retryable=False,
                    message=str(exc),
                    elapsed_ms=0,
                    downloaded_bytes=fetched.artifact.raw_size,
                ),
                False,
            )
        if (
            document.quality.get("overall", 0) < page_cfg.quality_threshold
            and fetched.artifact.strategy == "http"
            and page_cfg.browser.enabled
        ):
            browser_result = await self.fetcher.fetch_browser(fetch_url)
            if browser_result.artifact is not None:
                try:
                    document = await asyncio.to_thread(
                        self.extractors.extract,
                        browser_result.body,
                        browser_result.artifact.mime_type,
                        browser_result.artifact.final_url,
                        browser_result.artifact.content_hash,
                    )
                    document.url = url
                    fetched.artifact = browser_result.artifact
                    extraction_key = hashlib.sha256(
                        f"{fetched.artifact.content_hash}\0{page_cfg.extractor_version}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                except (ExtractionError, ValueError) as exc:
                    self._logger.info("Browser extraction failed: %s", exc)
            elif browser_result.failure is not None:
                self._logger.info(
                    "Browser fallback stopped code=%s", browser_result.failure.code
                )
                if browser_result.failure.code in {
                    "challenge_required",
                    "captcha_required",
                    "access_controlled",
                    "paywall_detected",
                }:
                    return None, browser_result.failure, False
        if document.quality.get("overall", 0) < page_cfg.quality_threshold:
            return (
                document,
                FetchFailure(
                    code="low_quality_extraction",
                    phase="extract",
                    category="content",
                    retryable=True,
                    message="low quality extraction",
                    elapsed_ms=0,
                    downloaded_bytes=fetched.artifact.raw_size,
                ),
                False,
            )
        if page_cfg.cache_enabled:
            await cache.write(
                "extracted",
                extraction_key,
                {
                    "document": document.model_dump(mode="json"),
                    "extractor_version": page_cfg.extractor_version,
                },
            )
        return document, None, False

    async def read(
        self,
        url: str,
        mode: Literal["outline", "relevant", "full"] = "outline",
        query: str = "",
        max_chars: int = 10000,
        section_ids: list[str] | None = None,
    ) -> tuple[
        ExtractedDocument | None, list[EvidenceChunk], str, FetchFailure | None, bool
    ]:
        document, failure, cache_hit = await self.get_document(url)
        if document is None:
            return None, [], "", failure, cache_hit
        if section_ids:
            wanted = {
                block_id
                for section in document.outline
                if section.section_id in section_ids
                for block_id in section.block_ids
            }
            selected = [block for block in document.blocks if block.block_id in wanted]
            content = "\n\n".join(block.text for block in selected)[:max_chars]
            evidence = collect_evidence(document, block_ids=wanted, max_chars=max_chars)
            return document, evidence, content, failure, cache_hit
        if mode == "full":
            content = "\n\n".join(block.text for block in document.blocks)[:max_chars]
            evidence = collect_evidence(document, max_chars=max_chars)
            return document, evidence, content, failure, cache_hit
        evidence = (
            select_evidence(document, query, limit=5) if mode == "relevant" else []
        )
        content = "\n\n".join(item.text for item in evidence)[:max_chars]
        return document, evidence, content, failure, cache_hit

    async def close(self) -> None:
        await self.fetcher.close()

    async def initialize(self) -> BrowserHealth:
        return await self.fetcher.initialize_browser()

    def browser_health(self) -> BrowserHealth:
        return self.fetcher.browser_health()


_structured_reader: StructuredPageReader | None = None
_structured_reader_lock = asyncio.Lock()


async def get_structured_page_reader() -> StructuredPageReader:
    global _structured_reader
    async with _structured_reader_lock:
        if _structured_reader is None:
            _structured_reader = StructuredPageReader()
    return _structured_reader


async def initialize_structured_page_reader() -> BrowserHealth:
    return await get_structured_page_reader().initialize()


async def close_structured_page_reader() -> None:
    global _structured_reader
    if _structured_reader is not None:
        await _structured_reader.close()
        _structured_reader = None

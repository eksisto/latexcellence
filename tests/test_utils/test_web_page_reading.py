"""Stage 2 structured page fetching, extraction, caching, and retrieval tests."""

import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from backend.core.config import get_config
from backend.utils.web_search.errors import FetchArtifact
from backend.utils.web_search.extract.chunking import chunk_document, select_evidence
from backend.utils.web_search.extract.html import extract_html
from backend.utils.web_search.extract.registry import ExtractorRegistry
from backend.utils.web_search.fetch.coordinator import FetchCoordinator, FetchResult
from backend.utils.web_search.fetch.policy import url_has_credentials
from backend.utils.web_search.page_reader import StructuredPageReader

HTML = """
<html><head><title>Structured article</title></head><body>
<nav>Home Login Subscribe</nav>
<main>
  <h1>Web extraction</h1>
  <p>This introduction explains semantic extraction and document structure.</p>
  <h2>Evaluation</h2>
  <p>BM25 retrieval finds passages relevant to a user query.</p>
  <ul><li>Preserve lists</li><li>Preserve links</li></ul>
  <table><tr><th>Metric</th><th>Score</th></tr><tr><td>Recall</td><td>0.95</td></tr></table>
  <p>Read the <a href="/method">complete method</a> for details.</p>
  <p><a href="javascript:alert(1)">unsafe action</a></p>
</main><footer>Privacy Cookie settings</footer>
</body></html>
"""


def _document():
    digest = hashlib.sha256(HTML.encode()).hexdigest()
    return extract_html(HTML, "https://example.test/article", digest)


def test_registry_prefers_github_raw_endpoint() -> None:
    registry = ExtractorRegistry()

    assert (
        registry.preferred_fetch_url(
            "https://github.com/acme/project/blob/main/docs/guide.md"
        )
        == "https://raw.githubusercontent.com/acme/project/main/docs/guide.md"
    )
    assert (
        registry.preferred_fetch_url("https://example.test/article")
        == "https://example.test/article"
    )


def test_html_extractor_preserves_structure_and_removes_boilerplate() -> None:
    document = _document()

    assert document.title == "Structured article"
    assert {"heading", "paragraph", "list", "table"} <= {
        block.kind for block in document.blocks
    }
    assert document.outline[1].heading_path == ["Web extraction", "Evaluation"]
    links = [link for block in document.blocks for link in block.links]
    assert links[0].url == "https://example.test/method"
    assert all(link.url.startswith(("http://", "https://")) for link in links)
    text = " ".join(block.text for block in document.blocks)
    assert "Cookie settings" not in text
    assert document.quality["overall"] > 0


def test_heading_aware_chunks_and_bm25_evidence_keep_source_blocks() -> None:
    document = _document()
    chunks = chunk_document(document, max_chars=140)
    evidence = select_evidence(document, "BM25 retrieval user query", limit=2)

    assert len(chunks) > 1
    assert all(chunk.block_ids for chunk in chunks)
    assert evidence
    assert "BM25 retrieval" in evidence[0].text
    assert evidence[0].heading_path == ["Web extraction", "Evaluation"]
    assert evidence[0].evidence_id.startswith(f"{document.document_id}#")
    assert select_evidence(document, "term-not-present-anywhere", limit=2) == []


@pytest.mark.asyncio
async def test_fetch_rejects_credentialed_url_before_network(monkeypatch) -> None:
    coordinator = FetchCoordinator()
    failure = await coordinator.fetch("https://example.test/page?token=secret-value")

    assert failure.failure and failure.failure.code == "credentialed_url"
    assert coordinator._client is None
    assert url_has_credentials("https://example.test/file?X-Amz-Signature=secret-value")


@pytest.mark.asyncio
async def test_fetch_stops_stream_over_size_limit(monkeypatch, tmp_path) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading
    monkeypatch.setattr(cfg, "cache_dir", str(tmp_path / "web-cache"))
    monkeypatch.setattr(cfg, "max_response_mb", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={
                "Content-Type": "text/plain",
                "Content-Length": str(2 * 1024 * 1024),
            },
            content=b"not downloaded",
        )

    coordinator = FetchCoordinator()
    coordinator._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "backend.utils.web_search.fetch.coordinator.url_is_safe",
        lambda _url: _async_true(),
    )
    result = await coordinator.fetch("https://example.test/large")
    await coordinator.close()

    assert result.failure and result.failure.code == "response_too_large"


@pytest.mark.asyncio
async def test_fetch_cache_uses_conditional_request_without_credentials(
    monkeypatch, tmp_path
) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading
    monkeypatch.setattr(cfg, "cache_dir", str(tmp_path / "web-cache"))
    monkeypatch.setattr(cfg, "cache_enabled", True)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, request=request)
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html", "ETag": '"v1"'},
            content=HTML.encode(),
        )

    coordinator = FetchCoordinator()
    coordinator._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "backend.utils.web_search.fetch.coordinator.url_is_safe",
        lambda _url: _async_true(),
    )
    first = await coordinator.fetch("https://example.test/article")
    second = await coordinator.fetch("https://example.test/article")
    await coordinator.close()

    assert first.artifact and not first.artifact.cache_hit
    assert second.artifact and second.artifact.cache_hit
    assert len(calls) == 1
    cache_text = "".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json")
    )
    assert "Authorization" not in cache_text
    assert "Cookie" not in cache_text


async def _async_true() -> bool:
    return True


@pytest.mark.asyncio
async def test_extractor_version_invalidates_extracted_cache(
    monkeypatch, tmp_path
) -> None:
    cfg = get_config().features.function_calling.web_search.page_reading
    monkeypatch.setattr(cfg, "cache_dir", str(tmp_path / "web-cache"))
    monkeypatch.setattr(cfg, "cache_enabled", True)
    monkeypatch.setattr(cfg, "quality_threshold", 0.0)
    artifact = FetchArtifact(
        requested_url="https://example.test/article",
        final_url="https://example.test/article",
        fetched_at=datetime.now(timezone.utc),
        mime_type="text/html",
        strategy="http",
        content_hash=hashlib.sha256(HTML.encode()).hexdigest(),
        raw_size=len(HTML),
        cache_key="key",
    )
    reader = StructuredPageReader()

    async def fake_fetch(_url: str) -> FetchResult:
        return FetchResult(artifact=artifact, body=HTML.encode())

    calls = 0
    original_extract = reader.extractors.extract

    def counted_extract(*args):
        nonlocal calls
        calls += 1
        return original_extract(*args)

    monkeypatch.setattr(reader.fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(reader.extractors, "extract", counted_extract)
    monkeypatch.setattr(cfg, "extractor_version", "html-test-v1")
    await reader.get_document(artifact.final_url)
    _document_cached, _failure, cache_hit = await reader.get_document(
        artifact.final_url
    )
    monkeypatch.setattr(cfg, "extractor_version", "html-test-v2")
    await reader.get_document(artifact.final_url)

    assert cache_hit is True
    assert calls == 2

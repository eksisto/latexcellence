"""MIME-oriented extraction registry."""

import hashlib
import json
import re
from urllib.parse import quote, urlsplit

from .html import extract_html
from .models import ContentBlock, ExtractedDocument
from .quality import score_blocks


class ExtractionError(Exception):
    """Content extraction failed with a typed error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExtractorRegistry:
    """Route fetched bytes to an extractor with a common document contract."""

    @staticmethod
    def preferred_fetch_url(url: str) -> str:
        """Prefer a public structured/raw endpoint for known repository URLs."""
        parsed = urlsplit(url)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            return url
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] != "blob":
            return url
        owner, repository, _blob, revision, *file_parts = parts
        if revision not in {"main", "master"} and not re.fullmatch(
            r"[0-9a-fA-F]{7,40}", revision
        ):
            return url
        safe_parts = [
            quote(part, safe="") for part in (owner, repository, revision, *file_parts)
        ]
        return "https://raw.githubusercontent.com/" + "/".join(safe_parts)

    def extract(
        self, body: bytes, mime_type: str, url: str, content_hash: str
    ) -> ExtractedDocument:
        if mime_type in {"text/html", "application/xhtml+xml"}:
            return extract_html(
                body.decode("utf-8", errors="replace"), url, content_hash
            )
        if mime_type == "application/json":
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise ExtractionError(
                    "invalid_json", "response is not valid JSON"
                ) from exc
            if isinstance(payload, dict) and (
                {"error", "errors"} & {key.casefold() for key in payload}
            ):
                raise ExtractionError(
                    "api_error", "structured endpoint returned an error"
                )
        if mime_type.startswith("text/") or mime_type in {
            "application/json",
            "application/xml",
            "text/xml",
        }:
            text = body.decode("utf-8", errors="replace").strip()
            document_id = f"web-{content_hash[:16]}"
            blocks = []
            for ordinal, paragraph in enumerate(
                part.strip() for part in text.split("\n\n") if part.strip()
            ):
                digest = hashlib.sha256(
                    f"{document_id}\0{ordinal}\0{paragraph}".encode("utf-8")
                ).hexdigest()[:12]
                blocks.append(
                    ContentBlock(
                        block_id=f"b{ordinal:04d}-{digest}",
                        kind="paragraph",
                        text=paragraph,
                        ordinal=ordinal,
                    )
                )
            return ExtractedDocument(
                document_id=document_id,
                url=url,
                title="",
                blocks=blocks,
                quality=score_blocks(blocks),
                extraction_strategy="plain_text",
            )
        raise ExtractionError("unsupported_mime", f"unsupported MIME type: {mime_type}")

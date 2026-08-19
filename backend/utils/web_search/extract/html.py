"""Structure-preserving HTML extraction."""

import hashlib
import re
from urllib.parse import urljoin, urlsplit

import trafilatura
from bs4 import BeautifulSoup, Tag

from .models import ContentBlock, DocumentSection, ExtractedDocument, LinkRef
from .quality import score_blocks

_BLOCK_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "table",
    "figure",
}
_DROP_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "aside",
    "form",
    "noscript",
    "template",
}


def _clean_text(node: Tag) -> str:
    return re.sub(r"[ \t\f\v]+", " ", node.get_text(" ", strip=True)).strip()


def _block_id(document_id: str, ordinal: int, kind: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{document_id}\0{ordinal}\0{kind}\0{text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"b{ordinal:04d}-{digest}"


def _safe_link(base_url: str, href: str) -> str | None:
    resolved = urljoin(base_url, href)
    return resolved if urlsplit(resolved).scheme in {"http", "https"} else None


def extract_html(html: str, url: str, content_hash: str) -> ExtractedDocument:
    """Extract semantic blocks from the best available DOM content root."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(_DROP_TAGS):
        node.decompose()
    title = _clean_text(soup.title) if soup.title else ""
    root = soup.find("article") or soup.find("main") or soup.body or soup
    document_id = f"web-{content_hash[:16]}"
    heading_path: list[str] = []
    blocks: list[ContentBlock] = []
    seen: set[int] = set()

    for node in root.find_all(_BLOCK_TAGS):
        if any(id(parent) in seen for parent in node.parents):
            continue
        text = _clean_text(node)
        if not text:
            continue
        name = node.name.lower()
        if name.startswith("h") and name[1:].isdigit():
            level = int(name[1:])
            heading_path = heading_path[: level - 1]
            heading_path.append(text)
            kind = "heading"
        elif name in {"ul", "ol"}:
            kind = "list"
        elif name == "table":
            rows = [
                [_clean_text(cell) for cell in row.find_all(["th", "td"])]
                for row in node.find_all("tr")
            ]
            text = "\n".join(
                " | ".join(cell for cell in row if cell) for row in rows if any(row)
            )
            kind = "table"
        elif name == "pre":
            text = node.get_text("\n", strip=True)
            kind = "code"
        elif name == "blockquote":
            kind = "quote"
        elif name == "figure":
            kind = "caption"
        else:
            kind = "paragraph"
        links = []
        for link in node.find_all("a", href=True):
            link_text = _clean_text(link)
            resolved = _safe_link(url, link.get("href", ""))
            if link_text and resolved:
                links.append(LinkRef(text=link_text, url=resolved))
        ordinal = len(blocks)
        blocks.append(
            ContentBlock(
                block_id=_block_id(document_id, ordinal, kind, text),
                kind=kind,
                text=text,
                heading_path=list(heading_path),
                links=links,
                ordinal=ordinal,
            )
        )
        seen.add(id(node))

    strategy = "dom_semantic"
    semantic_quality = score_blocks(blocks)
    fallback_blocks: list[ContentBlock] = []
    fallback = trafilatura.extract(html, output_format="txt", include_tables=True) or ""
    for paragraph in (part.strip() for part in fallback.splitlines()):
        if paragraph:
            ordinal = len(fallback_blocks)
            fallback_blocks.append(
                ContentBlock(
                    block_id=_block_id(document_id, ordinal, "paragraph", paragraph),
                    kind="paragraph",
                    text=paragraph,
                    ordinal=ordinal,
                )
            )
    fallback_quality = score_blocks(fallback_blocks)
    if fallback_blocks and fallback_quality["overall"] > semantic_quality["overall"]:
        blocks = fallback_blocks
        strategy = "trafilatura"
    elif not blocks:
        for paragraph in (part.strip() for part in fallback.splitlines()):
            if paragraph:
                ordinal = len(blocks)
                blocks.append(
                    ContentBlock(
                        block_id=_block_id(
                            document_id, ordinal, "paragraph", paragraph
                        ),
                        kind="paragraph",
                        text=paragraph,
                        ordinal=ordinal,
                    )
                )
        strategy = "trafilatura_fallback"

    outline: list[DocumentSection] = []
    for block in blocks:
        if block.kind != "heading":
            continue
        level = max(1, len(block.heading_path))
        section_blocks = [
            candidate.block_id
            for candidate in blocks[block.ordinal :]
            if candidate.heading_path[:level] == block.heading_path
        ]
        section_text = " ".join(
            candidate.text
            for candidate in blocks
            if candidate.block_id in section_blocks
        )
        outline.append(
            DocumentSection(
                section_id=f"s{len(outline):04d}",
                title=block.text,
                level=level,
                heading_path=block.heading_path,
                block_ids=section_blocks,
                word_count=len(section_text.split()),
            )
        )
    return ExtractedDocument(
        document_id=document_id,
        url=url,
        title=title,
        blocks=blocks,
        outline=outline,
        quality=score_blocks(blocks),
        extraction_strategy=strategy,
    )

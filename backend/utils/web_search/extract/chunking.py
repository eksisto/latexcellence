"""Heading-aware chunking and deterministic BM25/MMR evidence selection."""

import hashlib
import math
import re
from collections import Counter

from .models import DocumentChunk, EvidenceChunk, ExtractedDocument

_TERM = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


def _terms(text: str) -> list[str]:
    return [term.casefold() for term in _TERM.findall(text)]


def chunk_document(
    document: ExtractedDocument, max_chars: int = 2400
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    pending = []
    pending_chars = 0
    pending_path: list[str] = []
    for block in document.blocks:
        path_changed = pending and block.heading_path != pending_path
        if pending and (path_changed or pending_chars + len(block.text) > max_chars):
            chunks.append(
                _make_chunk(document.document_id, pending_path, pending, len(chunks))
            )
            pending, pending_chars = [], 0
        pending.append(block)
        pending_chars += len(block.text)
        pending_path = block.heading_path
    if pending:
        chunks.append(
            _make_chunk(document.document_id, pending_path, pending, len(chunks))
        )
    return chunks


def _make_chunk(
    document_id: str, path: list[str], blocks: list, ordinal: int
) -> DocumentChunk:
    text = "\n".join(block.text for block in blocks)
    digest = hashlib.sha256(
        f"{document_id}\0{ordinal}\0{text}".encode("utf-8")
    ).hexdigest()[:12]
    return DocumentChunk(
        chunk_id=f"c{ordinal:04d}-{digest}",
        document_id=document_id,
        heading_path=list(path),
        block_ids=[block.block_id for block in blocks],
        text=text,
        token_estimate=max(1, math.ceil(len(text) / 4)),
    )


def select_evidence(
    document: ExtractedDocument, query: str, limit: int = 5
) -> list[EvidenceChunk]:
    chunks = chunk_document(document)
    if not chunks:
        return []
    query_terms = _terms(query)
    if not query_terms:
        ranked = [(1 / (index + 1), chunk) for index, chunk in enumerate(chunks)]
    else:
        documents = [
            _terms(" ".join(chunk.heading_path) + " " + chunk.text) for chunk in chunks
        ]
        avg_len = sum(map(len, documents)) / max(1, len(documents))
        dfs = Counter(term for terms in documents for term in set(terms))
        scored = []
        for chunk, terms in zip(chunks, documents):
            counts = Counter(terms)
            score = 0.0
            for term in query_terms:
                tf = counts[term]
                if not tf:
                    continue
                idf = math.log(1 + (len(chunks) - dfs[term] + 0.5) / (dfs[term] + 0.5))
                score += (
                    idf
                    * tf
                    * 2.5
                    / (tf + 1.5 * (0.25 + 0.75 * len(terms) / max(avg_len, 1)))
                )
            scored.append((score, chunk))
        ranked = [
            item
            for item in sorted(scored, key=lambda item: (-item[0], item[1].chunk_id))
            if item[0] > 0
        ]
    selected: list[tuple[float, DocumentChunk]] = []
    remaining = list(ranked)
    while remaining and len(selected) < limit:
        best_index = 0
        best_score = float("-inf")
        for index, (relevance, chunk) in enumerate(remaining):
            terms = set(_terms(chunk.text))
            overlap = max(
                (
                    len(terms & set(_terms(existing.text)))
                    / max(1, len(terms | set(_terms(existing.text))))
                    for _, existing in selected
                ),
                default=0.0,
            )
            mmr = 0.8 * relevance - 0.2 * overlap
            if mmr > best_score:
                best_index, best_score = index, mmr
        _relevance, chunk = remaining.pop(best_index)
        selected.append((best_score, chunk))
    return [
        EvidenceChunk(
            evidence_id=f"{document.document_id}#{chunk.chunk_id}",
            document_id=document.document_id,
            source_url=document.url,
            title=document.title,
            heading_path=chunk.heading_path,
            block_ids=chunk.block_ids,
            text=chunk.text,
            retrieval_score=round(score, 6),
        )
        for score, chunk in selected
    ]


def collect_evidence(
    document: ExtractedDocument,
    *,
    block_ids: set[str] | None = None,
    max_chars: int = 10000,
) -> list[EvidenceChunk]:
    """按文档顺序收集指定区块的稳定证据片段，并受字符预算约束。"""
    collected: list[EvidenceChunk] = []
    remaining = max_chars
    for chunk in chunk_document(document):
        if block_ids is not None and not block_ids.intersection(chunk.block_ids):
            continue
        if remaining <= 0:
            break
        text = chunk.text[:remaining]
        if not text:
            continue
        collected.append(
            EvidenceChunk(
                evidence_id=f"{document.document_id}#{chunk.chunk_id}",
                document_id=document.document_id,
                source_url=document.url,
                title=document.title,
                heading_path=chunk.heading_path,
                block_ids=chunk.block_ids,
                text=text,
                retrieval_score=1.0,
            )
        )
        remaining -= len(text)
    return collected

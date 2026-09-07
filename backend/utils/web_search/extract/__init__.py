"""网页结构化抽取的数据契约。"""

from .models import (
    ContentBlock,
    DocumentChunk,
    DocumentSection,
    EvidenceBundle,
    EvidenceChunk,
    ExtractedDocument,
    LinkRef,
)
from .registry import ExtractionError

__all__ = [
    "ContentBlock",
    "DocumentSection",
    "DocumentChunk",
    "EvidenceBundle",
    "EvidenceChunk",
    "ExtractionError",
    "ExtractedDocument",
    "LinkRef",
]

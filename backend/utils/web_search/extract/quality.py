"""Deterministic extraction quality scoring."""

import re

from .models import ContentBlock

_NAV_WORDS = re.compile(r"\b(home|menu|login|sign in|cookie|privacy|subscribe)\b", re.I)


def score_blocks(blocks: list[ContentBlock]) -> dict[str, float]:
    """Return normalized quality components and a weighted overall score."""
    text = "\n".join(block.text for block in blocks)
    chars = len(text)
    if not chars:
        return {"overall": 0.0, "length": 0.0, "structure": 0.0, "noise": 1.0}
    length_score = min(1.0, chars / 1200)
    structured = sum(block.kind != "paragraph" for block in blocks)
    structure_score = min(1.0, structured / max(1, len(blocks) * 0.25))
    noise_matches = len(_NAV_WORDS.findall(text))
    noise = min(1.0, noise_matches * 20 / max(chars, 1))
    unique_ratio = len({block.text.casefold() for block in blocks}) / max(
        1, len(blocks)
    )
    overall = (
        0.55 * length_score
        + 0.2 * structure_score
        + 0.2 * unique_ratio
        + 0.05 * (1 - noise)
    )
    return {
        "overall": round(max(0.0, min(1.0, overall)), 4),
        "length": round(length_score, 4),
        "structure": round(structure_score, 4),
        "noise": round(noise, 4),
    }

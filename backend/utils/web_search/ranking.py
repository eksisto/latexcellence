"""通用搜索候选合并、RRF 排序与域名多样性。"""

import re
from collections import defaultdict

from backend.utils.web_search.models import SearchCandidate, SearchResponse
from backend.utils.web_search.url_utils import (
    canonicalize_url,
    classify_source,
    url_domain,
)

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _text_key(title: str, snippet: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(f"{title} {snippet}".casefold()))


def _is_near_duplicate(
    tokens: frozenset[str], seen: list[frozenset[str]], threshold: float = 0.9
) -> bool:
    if not tokens:
        return False
    for previous in seen:
        union = tokens | previous
        if union and len(tokens & previous) / len(union) >= threshold:
            return True
    return False


def rank_responses(
    lanes: list[tuple[str, SearchResponse]],
    *,
    mode: str,
    domain_result_cap: int,
    source_type_boosts: dict[str, float],
) -> tuple[list[SearchCandidate], int]:
    """聚合 lane 响应，返回多样化候选和去重前有效结果数。"""
    candidates: dict[str, SearchCandidate] = {}
    insertion_order: dict[str, int] = {}
    near_duplicate_tokens: list[frozenset[str]] = []
    valid_count = 0

    for lane_name, response in lanes:
        for rank, result in enumerate(response.results, 1):
            canonical = canonicalize_url(result.url)
            if canonical is None:
                continue
            valid_count += 1
            candidate = candidates.get(canonical)
            if candidate is None:
                tokens = _text_key(result.title, result.snippet)
                if _is_near_duplicate(tokens, near_duplicate_tokens):
                    continue
                near_duplicate_tokens.append(tokens)
                source_type = classify_source(canonical)
                candidate = SearchCandidate(
                    url=result.url,
                    canonical_url=canonical,
                    title=result.title,
                    snippet=result.snippet,
                    source_type=source_type,
                    transport_sources=[],
                    origin_ranks={},
                    score_components={},
                    final_score=0.0,
                )
                candidates[canonical] = candidate
                insertion_order[canonical] = len(insertion_order)
            if response.engine and response.engine not in candidate.transport_sources:
                candidate.transport_sources.append(response.engine)
            candidate.origin_ranks[lane_name] = rank

    for candidate in candidates.values():
        rrf = sum(1.0 / (60 + rank) for rank in candidate.origin_ranks.values())
        consensus = max(0, len(candidate.transport_sources) - 1) * 0.01
        source_boost = source_type_boosts.get(candidate.source_type, 0.0)
        candidate.score_components = {
            "rrf": rrf,
            "consensus": consensus,
            "source_type_boost": source_boost,
        }
        candidate.final_score = rrf + consensus + source_boost

    if mode == "legacy":
        ordered = sorted(
            candidates.values(), key=lambda c: insertion_order[c.canonical_url]
        )
    else:
        ordered = sorted(
            candidates.values(),
            key=lambda c: (-c.final_score, insertion_order[c.canonical_url]),
        )

    diversified: list[SearchCandidate] = []
    domain_counts: dict[str, int] = defaultdict(int)
    for candidate in ordered:
        domain = url_domain(candidate.canonical_url)
        if domain_counts[domain] >= domain_result_cap:
            continue
        domain_counts[domain] += 1
        diversified.append(candidate)
    return diversified, valid_count

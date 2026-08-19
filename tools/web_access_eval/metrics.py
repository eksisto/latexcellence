"""网络访问评测的确定性指标。"""

import math
import re
from collections import Counter
from statistics import mean, median
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import (
    ExtractionDataset,
    ExtractionPredictions,
    GroundingDataset,
    GroundingPredictions,
    ScholarlyDataset,
    ScholarlyPredictions,
    SearchDataset,
    SearchPredictions,
)

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def _canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_PARAMS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _ndcg(relevances: list[int], ideal: list[int], limit: int = 10) -> float:
    def dcg(values: list[int]) -> float:
        return sum(
            (2**relevance - 1) / math.log2(index + 2)
            for index, relevance in enumerate(values[:limit])
        )

    denominator = dcg(sorted(ideal, reverse=True))
    return dcg(relevances) / denominator if denominator else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(fraction * len(ordered)) - 1
    return ordered[max(0, index)]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    numeric_keys = sorted(
        {
            key
            for row in rows
            for key, value in row.items()
            if key != "case_id" and isinstance(value, (int, float))
        }
    )
    result = {
        key: round(mean(float(row[key]) for row in rows if key in row), 6)
        for key in numeric_keys
    }
    latencies = [float(row["latency_ms"]) for row in rows if "latency_ms" in row]
    if latencies:
        result["latency_p50_ms"] = round(median(latencies), 3)
        result["latency_p95_ms"] = round(_percentile(latencies, 0.95), 3)
    return result


def _lcs_ratio(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 1.0
    previous = [0] * (len(actual) + 1)
    for expected_id in expected:
        current = [0]
        for index, actual_id in enumerate(actual, 1):
            if expected_id == actual_id:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1] / len(expected)


def evaluate_search(
    dataset: SearchDataset, predictions: SearchPredictions
) -> dict[str, Any]:
    predicted = {case.case_id: case for case in predictions.cases}
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        result = predicted.get(case.case_id)
        ranked = result.results if result else []
        judgments = {_canonical_url(item.url): item for item in case.judgments}
        urls = [_canonical_url(item.canonical_url or item.url) for item in ranked]
        seen_urls: set[str] = set()
        relevance: list[int] = []
        for url in urls:
            relevance.append(
                judgments[url].relevance
                if url in judgments and url not in seen_urls
                else 0
            )
            seen_urls.add(url)
        relevant_urls = {url for url, item in judgments.items() if item.relevance > 0}
        first = next(
            (index + 1 for index, value in enumerate(relevance[:10]) if value), 0
        )
        top_twenty = set(urls[:20])
        authoritative = {
            url
            for url, item in judgments.items()
            if item.authoritative and item.relevance > 0
        }
        domains = {urlsplit(url).netloc for url in urls[:10] if urlsplit(url).netloc}
        row = {
            "case_id": case.case_id,
            "prediction_coverage": float(result is not None),
            "ndcg_at_10": _ndcg(relevance, [item.relevance for item in case.judgments]),
            "mrr_at_10": 1 / first if first else 0.0,
            "recall_at_20": (
                len(top_twenty & relevant_urls) / len(relevant_urls)
                if relevant_urls
                else 1.0
            ),
            "authority_hit": float(bool(top_twenty & authoritative)),
            "duplicate_rate_at_10": (
                1 - len(set(urls[:10])) / len(urls[:10]) if urls[:10] else 0.0
            ),
            "domain_diversity_at_10": float(len(domains)),
            "engine_failure_count": (
                float(len(result.engine_failures)) if result else 0.0
            ),
        }
        if result:
            row["latency_ms"] = result.latency_ms
        rows.append(row)
    return {
        "suite": "search",
        "system": predictions.system,
        "cases": rows,
        "aggregate": _aggregate(rows),
    }


def _normalize_identifier(name: str, value: str) -> str:
    normalized = value.strip().casefold()
    if name.casefold() == "doi":
        normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
        normalized = re.sub(r"^doi:\s*", "", normalized)
    elif name.casefold() == "arxiv":
        normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized)
        normalized = re.sub(r"^arxiv:\s*", "", normalized)
        normalized = re.sub(r"v\d+$", "", normalized).removesuffix(".pdf")
    return normalized


def _identifier_keys(identifiers: dict[str, str]) -> set[str]:
    return {
        f"{name.casefold()}:{_normalize_identifier(name, value)}"
        for name, value in identifiers.items()
        if value.strip()
    }


def evaluate_scholarly(
    dataset: ScholarlyDataset, predictions: ScholarlyPredictions
) -> dict[str, Any]:
    predicted = {case.case_id: case for case in predictions.cases}
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        result = predicted.get(case.case_id)
        ranked = result.results if result else []
        gold = [(_identifier_keys(item.identifiers), item) for item in case.judgments]
        relevance: list[int] = []
        matched_relevant: set[int] = set()
        complete = 0
        for work in ranked[:20]:
            keys = _identifier_keys(work.identifiers)
            complete += int(bool(keys))
            matched_index = next(
                (
                    index
                    for index, (gold_keys, _) in enumerate(gold)
                    if keys & gold_keys
                ),
                None,
            )
            if (
                matched_index is None
                or matched_index in matched_relevant
                or gold[matched_index][1].relevance == 0
            ):
                relevance.append(0)
            else:
                relevance.append(gold[matched_index][1].relevance)
                matched_relevant.add(matched_index)
        relevant_count = sum(item.relevance > 0 for item in case.judgments)
        row = {
            "case_id": case.case_id,
            "prediction_coverage": float(result is not None),
            "ndcg_at_10": _ndcg(relevance, [item.relevance for item in case.judgments]),
            "recall_at_20": (
                len(matched_relevant) / relevant_count if relevant_count else 1.0
            ),
            "identifier_completeness": (
                complete / min(len(ranked), 20) if ranked else 0.0
            ),
            "oa_hit": float(
                any(
                    work.open_access
                    and any(
                        _identifier_keys(work.identifiers) & gold_keys
                        and item.open_access
                        and item.relevance > 0
                        for gold_keys, item in gold
                    )
                    for work in ranked[:20]
                )
            ),
            "provider_failure_count": (
                float(len(result.provider_failures)) if result else 0.0
            ),
        }
        if result:
            row["latency_ms"] = result.latency_ms
        rows.append(row)
    return {
        "suite": "scholarly",
        "system": predictions.system,
        "cases": rows,
        "aggregate": _aggregate(rows),
    }


def _normalized_chars(text: str) -> Counter[str]:
    return Counter(re.sub(r"\s+", "", text).casefold())


def _match_extraction_blocks(
    gold_blocks: list, actual_blocks: list
) -> list[tuple[Any, Any]]:
    """Match annotation IDs first, then exact normalized text for production IDs."""
    by_id = {block.block_id: block for block in actual_blocks}
    used: set[int] = set()
    matches: list[tuple[Any, Any]] = []
    for gold in gold_blocks:
        actual = by_id.get(gold.block_id)
        if actual is None:
            gold_text = re.sub(r"\s+", "", gold.text).casefold()
            actual = next(
                (
                    block
                    for block in actual_blocks
                    if id(block) not in used
                    and re.sub(r"\s+", "", block.text).casefold() == gold_text
                ),
                None,
            )
        if actual is not None and id(actual) not in used:
            used.add(id(actual))
            matches.append((gold, actual))
    return matches


def evaluate_extraction(
    dataset: ExtractionDataset, predictions: ExtractionPredictions
) -> dict[str, Any]:
    predicted = {case.case_id: case for case in predictions.cases}
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        result = predicted.get(case.case_id)
        blocks = result.blocks if result else []
        gold_chars = _normalized_chars(
            "".join(block.text for block in case.gold_blocks)
        )
        actual_chars = _normalized_chars("".join(block.text for block in blocks))
        overlap = sum((gold_chars & actual_chars).values())
        matched = _match_extraction_blocks(case.gold_blocks, blocks)
        kind_hits = sum(actual.kind == gold.kind for gold, actual in matched)
        heading_hits = sum(
            actual.heading_path == gold.heading_path for gold, actual in matched
        )
        actual_text = " ".join(block.text for block in blocks).casefold()
        boilerplate_hits = sum(
            len(text)
            for text in case.boilerplate_text
            if text.casefold() in actual_text
        )
        row = {
            "case_id": case.case_id,
            "prediction_coverage": float(result is not None),
            "status_accuracy": float(
                bool(result and result.status == case.expected_status)
            ),
            "strategy_accuracy": float(
                bool(result and result.strategy == case.expected_strategy)
            ),
            "char_precision": (
                overlap / sum(actual_chars.values()) if actual_chars else 0.0
            ),
            "char_recall": overlap / sum(gold_chars.values()) if gold_chars else 1.0,
            "block_recall": (
                len(matched) / len(case.gold_blocks) if case.gold_blocks else 1.0
            ),
            "kind_preservation": kind_hits / len(matched) if matched else 0.0,
            "heading_preservation": heading_hits / len(matched) if matched else 0.0,
            "block_order": _lcs_ratio(
                [gold.block_id for gold in case.gold_blocks],
                [
                    gold.block_id
                    for block in blocks
                    for gold, actual in matched
                    if actual is block
                ],
            ),
            "boilerplate_ratio": boilerplate_hits / max(1, len(actual_text)),
        }
        if result:
            row["latency_ms"] = result.latency_ms
        rows.append(row)
    return {
        "suite": "extraction",
        "system": predictions.system,
        "cases": rows,
        "aggregate": _aggregate(rows),
    }


def evaluate_grounding(
    dataset: GroundingDataset, predictions: GroundingPredictions
) -> dict[str, Any]:
    predicted = {case.case_id: case for case in predictions.cases}
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        result = predicted.get(case.case_id)
        answer = result.answer.casefold() if result else ""
        expected_terms = [term.casefold() for term in case.expected_answer_terms]
        gold_pairs = {
            (item.evidence_id, _canonical_url(item.url)) for item in case.evidence
        }
        cited_pairs = {
            (item.evidence_id, _canonical_url(item.url))
            for item in (result.citations if result else [])
        }
        row = {
            "case_id": case.case_id,
            "prediction_coverage": float(result is not None),
            "answer_term_recall": sum(term in answer for term in expected_terms)
            / len(expected_terms),
            "evidence_recall": len(gold_pairs & cited_pairs) / len(gold_pairs),
            "citation_precision": (
                len(gold_pairs & cited_pairs) / len(cited_pairs) if cited_pairs else 0.0
            ),
        }
        if result:
            row["latency_ms"] = result.latency_ms
            if result.token_count is not None:
                row["token_count"] = float(result.token_count)
            if result.tool_rounds is not None:
                row["tool_rounds"] = float(result.tool_rounds)
        rows.append(row)
    return {
        "suite": "grounding",
        "system": predictions.system,
        "cases": rows,
        "aggregate": _aggregate(rows),
    }


EVALUATORS = {
    "search": evaluate_search,
    "scholarly": evaluate_scholarly,
    "extraction": evaluate_extraction,
    "grounding": evaluate_grounding,
}

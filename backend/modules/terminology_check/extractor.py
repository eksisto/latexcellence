"""
术语提取器。

通过 LLM 从段落文本中提取关键术语（概念、缩写、专有名词）。
提取结果用于跨段落的一致性检测。
"""

import json
import re
from typing import Any

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.model_router import get_client_for
from backend.core.multiuser import llm_governance
from backend.modules.terminology_check.models import TermClusterResult
from backend.utils.llm_cache import cached_llm_validate


class TermItem(BaseModel):
    """LLM 返回的单个术语"""

    term: str
    term_type: str = "concept"
    context: str = ""


class TermExtractOutput(BaseModel):
    """LLM 术语抽取响应"""

    terms: list[TermItem] = []


_TERM_EXTRACT_PROMPT = (
    "You are a terminology extractor for academic LaTeX documents. "
    "Your task is to identify key terms from the given paragraph.\n\n"
    "DEFINITIONS:\n"
    '- "concept": A technical concept, domain-specific term, or key idea '
    "that has a specific meaning in the document's field.\n"
    '- "abbreviation": An abbreviated form (e.g. "NLP", "CNN"). '
    "The abbreviation itself, not its expanded form.\n"
    '- "proper_noun": A named entity such as a person name, institution, '
    "algorithm name, dataset name, or trademark.\n\n"
    "CRITICAL RULES:\n"
    "1. The text contains markers like __CMD_0000__ or __MATH_0000__ "
    "which represent LaTeX commands. These are NOT text and must NOT be "
    "extracted as terms.\n"
    "2. Only extract terms that are meaningful for cross-paragraph "
    "consistency checking. Skip common words, stop words, and generic terms.\n"
    "3. For each term, provide the exact wording as it appears in the text "
    "(preserve capitalization and spacing).\n"
    "4. Provide a short context (surrounding 10-15 characters) to help "
    "identify where the term occurs.\n"
    "5. If no meaningful terms are found, return an empty terms list.\n"
    "6. Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format:\n"
    "{\n"
    '  "terms": [\n'
    '    {"term": "<exact term>", "term_type": "<concept|abbreviation|proper_noun>", "context": "<surrounding text>"}\n'
    "  ]\n"
    "}"
)

_MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4}__")


_CLUSTER_SYSTEM_PROMPT = (
    "You are a terminology consistency analyzer for academic documents.\n\n"
    "Given a list of unique terms extracted from a document, your task is to:\n"
    "1. Group terms that refer to the SAME concept (semantically identical).\n"
    "   Examples of valid groups:\n"
    '   - "Deep Learning" / "deep learning" / "深度学习" / "DL"\n'
    '   - "Machine Learning" / "machine learning" / "ML"\n'
    "2. For each group, select the most standard and appropriate term as "
    'the "recommended" value — prefer the form most commonly used in '
    "academic writing for the document's language.\n"
    '3. List ALL terms in the group as "variants", INCLUDING the '
    "recommended term itself if it was one of the extracted terms.\n\n"
    "RULES:\n"
    "- Only group terms that are truly semantically IDENTICAL.\n"
    "- Do NOT group terms that are merely related or similar but distinct.\n"
    "- Only output groups with 2 or more variants. Single terms are NOT "
    "inconsistencies.\n"
    "- The recommended term MUST be one of the variants.\n"
    "- Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format:\n"
    "{\n"
    '  "clusters": [\n'
    '    {"recommended": "推荐术语", "variants": ["术语1", "术语2", "..."]}\n'
    "  ]\n"
    "}\n\n"
    "If there are no inconsistencies (all terms are unique with no "
    'synonyms), return: {"clusters": []}'
)


class TermExtractor:
    """通过 LLM 从段落文本中提取关键术语。"""

    def __init__(self, logger_name: str = "term_extractor"):
        self._logger = get_logger(logger_name)

    async def extract_terms(
        self,
        paragraph_index: int,
        plain_text: str,
        batch_label: str = "",
    ) -> list[TermItem]:
        """
        从单个段落中提取术语。

        参数：
            paragraph_index: 段落索引（用于结果关联）
            plain_text: 剥离 LaTeX 后的纯文本（含 marker）
            batch_label: 批次标签（用于日志）

        返回：
            TermItem 列表
        """
        messages = [
            {"role": "system", "content": _TERM_EXTRACT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Extract key terms from the following paragraph:\n\n"
                    f"{plain_text}"
                ),
            },
        ]

        try:
            validated: TermExtractOutput = await cached_llm_validate(
                get_client_for("terminology_check"),
                messages=messages,
                model_cls=TermExtractOutput,
                max_retries=3,
                logger_name="term_extractor",
                temperature=0.2,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )

            filtered = [
                t
                for t in validated.terms
                if not _MARKER_PATTERN.fullmatch(t.term.strip())
            ]

            self._logger.debug(
                f"{batch_label}P{paragraph_index}: extracted {len(filtered)} terms"
            )
            return filtered

        except ValueError as e:
            self._logger.warning(
                f"{batch_label}P{paragraph_index}: term extraction failed — {e}"
            )
            return []
        except Exception as e:
            # LLM 每用户治理超限（429 语义）原样重抛：静默降级为"无术语"
            # 会让用户看到"检查完成、无不一致"的假结果（对齐基类口径）
            if llm_governance.governance_code_of(e) is not None:
                raise
            self._logger.error(
                f"{batch_label}P{paragraph_index}: LLM call failed — {e}"
            )
            return []

    async def cluster_terms(
        self,
        unique_terms: list[str],
    ) -> list[dict[str, Any]]:
        """
        将去重后的术语列表发送至 LLM 进行语义聚类。

        参数：
            unique_terms: 去重后的术语列表

        返回：
            聚类列表，每个聚类为 {"recommended": str, "variants": list[str]}
        """
        if not unique_terms:
            return []

        messages = [
            {"role": "system", "content": _CLUSTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Analyze the following unique terms extracted from a "
                    "document and group semantically identical terms:\n\n"
                    f'{json.dumps({"terms": unique_terms}, ensure_ascii=False)}'
                ),
            },
        ]

        try:
            validated: TermClusterResult = await cached_llm_validate(
                get_client_for("terminology_check"),
                messages=messages,
                model_cls=TermClusterResult,
                max_retries=3,
                logger_name="term_extractor",
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )

            clusters = [c.model_dump() for c in validated.clusters]
            self._logger.info(
                f"Term clustering completed: {len(clusters)} clusters "
                f"from {len(unique_terms)} unique terms"
            )
            return clusters

        except ValueError as e:
            self._logger.warning(f"Term clustering validation failed — {e}")
            return []
        except Exception as e:
            # 同提取：治理超限原样重抛，不得静默降级为"无聚类"
            if llm_governance.governance_code_of(e) is not None:
                raise
            self._logger.error(f"Term clustering LLM call failed — {e}")
            return []

    def extract_terms_fallback(
        self,
        plain_text: str,
    ) -> list[TermItem]:
        """
        无 LLM 时的规则降级：基于大写字母和常见模式提取术语。

        参数：
            plain_text: 剥离 LaTeX 后的纯文本

        返回：
            TermItem 列表（可能为空）
        """
        terms: list[TermItem] = []

        abbrev_pattern = re.compile(r"\b[A-Z]{2,}(?:s)?\b")
        for m in abbrev_pattern.finditer(plain_text):
            term = m.group()
            if len(term) <= 6:
                start = max(0, m.start() - 15)
                end = min(len(plain_text), m.end() + 15)
                context = plain_text[start:end]
                terms.append(
                    TermItem(
                        term=term,
                        term_type="abbreviation",
                        context=context.strip(),
                    )
                )

        return terms

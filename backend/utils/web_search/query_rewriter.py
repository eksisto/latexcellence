"""查询重写 — 将用户自然语言搜索意图拆解为多条精准查询。"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.core.llm_client import get_llm_client
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt

# ── 数据模型 ──────────────────────────────────────────────────────────────────


class SearchIntent(BaseModel):
    """搜索意图的结构化表示。"""

    keywords: list[str] = Field(description="核心关键词列表")
    filters: dict[str, str] = Field(
        default_factory=dict,
        description="过滤条件，如 target_users, time_frame, domain 等",
    )
    expected_result: str = Field(
        default="", description="期望的结果类型，如教程、工具推荐、对比、官方文档等"
    )
    language: str = Field(default="zh", description="用户查询的语言")


# ── 查询重写器 ────────────────────────────────────────────────────────────────


class QueryRewriter:
    """
    搜索查询重写器。

    将用户口语化的搜索需求拆解为多条指向明确的搜索查询，
    通过多角度检索提高搜索结果覆盖率。
    """

    def __init__(self) -> None:
        self._logger = get_logger("query_rewriter")

    async def rewrite(self, query: str) -> list[str]:
        """重写用户查询，返回多条搜索查询字符串。

        先尝试 LLM 意图解析 + 变体生成，失败时降级为规则方法。
        """
        if not query or not query.strip():
            return [query]

        # LLM-based rewriting
        try:
            variants = await self._rewrite_with_llm(query)
            if variants:
                return variants
        except Exception as e:
            self._logger.warning(f"LLM query rewriting failed, falling back: {e}")

        # Fallback: rule-based rewriting
        return self._rewrite_rule_based(query)

    async def _rewrite_with_llm(self, query: str) -> list[str]:
        """使用 LLM 解析意图并生成查询变体。"""
        llm = get_llm_client()
        content = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": get_prompt("web_search", "query_rewriting_system"),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        data = json.loads(content)
        variants: list[str] = data.get("variants", [])

        valid = [v.strip() for v in variants if v and v.strip()]
        if not valid:
            self._logger.warning("LLM returned empty variants")
            return []

        # 去重（大小写不敏感）
        seen: set[str] = set()
        deduped: list[str] = []
        for v in valid:
            key = v.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(v)

        return deduped

    def _rewrite_rule_based(self, query: str) -> list[str]:
        """规则兜底：关键词提取 + 简单变体。"""
        cleaned = self._clean_query(query)
        keywords = self._extract_keywords(cleaned)

        if not keywords:
            return [query]

        variants: list[str] = []

        # 1. 空格分隔的关键词查询
        kw_query = " ".join(keywords)
        if kw_query != cleaned:
            variants.append(kw_query)

        # 2. 原始 cleaned 查询
        if cleaned != query:
            variants.append(cleaned)

        # 3. 原始查询
        variants.append(query)

        seen: set[str] = set()
        deduped: list[str] = []
        for v in variants:
            key = v.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(v)

        return deduped

    @staticmethod
    def _clean_query(query: str) -> str:
        """去除疑问词、语气词等噪音。"""
        q = query.strip()
        # 去除开头的疑问/请求模式
        q = re.sub(
            r"^(请[问你帮]|帮我|我想|请问|有没有|有什么|哪里|如何|怎么|怎样|啥|什么|哪些)",
            "",
            q,
        ).strip()
        # 去除结尾的语气词
        q = re.sub(r"[吗呢么吧啊呀]$", "", q).strip()
        return q

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从清理后的文本中提取核心关键词。"""
        # 以空格、逗号、分号、顿号分词
        parts = re.split(r"[\s,，；;、]+", text)
        stop_words = {
            "的",
            "了",
            "在",
            "是",
            "我",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "上",
            "也",
            "很",
            "到",
            "说",
            "要",
            "去",
            "你",
            "会",
            "着",
            "没有",
            "看",
            "好",
            "自己",
            "这",
            "他",
            "她",
            "它",
            "们",
            "那",
            "些",
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "have",
            "has",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "can",
            "may",
            "might",
            "shall",
            "should",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "about",
            "what",
            "how",
            "where",
            "when",
            "why",
            "which",
            "who",
            "whom",
            "find",
            "show",
            "give",
            "tell",
            "list",
            "search",
            "look",
            "get",
        }
        keywords = []
        for p in parts:
            p = p.strip().lower()
            if p and p not in stop_words and len(p) > 1:
                keywords.append(p)
        return keywords

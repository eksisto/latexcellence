"""通用搜索查询计划契约与带校验重试的规划器。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# default 槽位（multi-model §6）：查询重写/规划为低频辅助调用；
# get_llm_client 即 get_client_for("default") 的薄别名（P2 起语义等价）
from backend.core.llm_client import get_llm_client
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation


class SearchQueryPlan(BaseModel):
    """LLM 与规则查询规划器必须共同返回的结构化计划。"""

    model_config = ConfigDict(extra="forbid")

    original_query: str = Field(min_length=1)
    intent: Literal["fact", "current", "official_docs", "comparison", "academic"]
    languages: list[str] = Field(min_length=1)
    variants: list[str] = Field(min_length=1, max_length=4)
    required_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    freshness: Literal["any", "day", "week", "month", "year"] = "any"
    date_from: date | None = None
    date_to: date | None = None

    @field_validator(
        "languages",
        "variants",
        "required_terms",
        "excluded_terms",
        "preferred_domains",
    )
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        """清理空项并按大小写不敏感键稳定去重。"""
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                normalized.append(item)
        return normalized

    @field_validator("languages", "variants")
    @classmethod
    def require_non_empty_lists(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("list must contain at least one non-empty value")
        return values

    @model_validator(mode="after")
    def validate_date_range(self) -> "SearchQueryPlan":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class SearchQueryPlanner:
    """生成结构化查询计划；LLM 失败时返回保守的规则计划。"""

    def __init__(self) -> None:
        self._logger = get_logger("search_query_planner")

    async def plan(self, query: str) -> SearchQueryPlan:
        normalized = query.strip()
        if not normalized:
            raise ValueError("Search query must not be empty")
        try:
            llm = get_llm_client()
            result = await retry_with_validation(
                chat_fn=llm.chat,
                messages=[
                    {
                        "role": "system",
                        "content": get_prompt("web_search", "query_rewriting_system"),
                    },
                    {"role": "user", "content": normalized},
                ],
                model_cls=SearchQueryPlan,
                max_retries=3,
                logger_name="search_query_planner",
                temperature=0.2,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            result.original_query = normalized
            return result
        except Exception as exc:
            self._logger.warning("LLM query planning failed, falling back: %s", exc)
            return self._fallback_plan(normalized)

    @staticmethod
    def _fallback_plan(query: str) -> SearchQueryPlan:
        lowered = query.casefold()
        current_terms = ("latest", "current", "today", "最新", "当前", "今天")
        academic_terms = ("paper", "doi", "arxiv", "论文", "文献")
        official_terms = ("docs", "documentation", "官方文档", "api reference")
        comparison_terms = (" vs ", "compare", "comparison", "对比", "比较")
        if any(term in lowered for term in academic_terms):
            intent = "academic"
        elif any(term in lowered for term in current_terms):
            intent = "current"
        elif any(term in lowered for term in official_terms):
            intent = "official_docs"
        elif any(term in lowered for term in comparison_terms):
            intent = "comparison"
        else:
            intent = "fact"
        language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"
        return SearchQueryPlan(
            original_query=query,
            intent=intent,
            languages=[language],
            variants=[query],
        )

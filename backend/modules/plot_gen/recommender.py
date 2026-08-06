"""图表类型推荐：确定性规则 + LLM 结构化推荐。"""

from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.modules.plot_gen.data_parser import (
    build_profile_context,
    redundancy_hint_pairs,
)
from backend.modules.plot_gen.models import ChartPlan, DataProfile
from backend.utils.json_utils import retry_with_validation

logger = get_logger(__name__)


def _match_named_column(
    profile: DataProfile, description: str, text: str, inferred_types: tuple[str, ...]
) -> str | None:
    """按名称在描述中匹配指定类型的列，用于优先选择与用户语义相关的列。"""
    lowered = text
    for c in profile.column_profiles:
        if c.inferred_type not in inferred_types:
            continue
        if c.name.lower() in lowered or (description and c.name in description):
            return c.name
    return None


def deterministic_chart_plan(profile: DataProfile, description: str = "") -> ChartPlan:
    """根据数据形状和用户描述返回规则兜底方案。"""
    numeric = [
        c.name for c in profile.column_profiles if c.inferred_type == "continuous"
    ]
    # id 列是唯一标识，不能作为分组/着色维度
    categorical = [
        c.name for c in profile.column_profiles if c.inferred_type == "categorical"
    ]
    datetime_cols = [
        c.name for c in profile.column_profiles if c.inferred_type == "datetime"
    ]
    text = (description or "").lower()

    if datetime_cols and numeric:
        plan = ChartPlan(
            chart_type="line",
            x=datetime_cols[0],
            y=numeric[0],
            color=categorical[0] if categorical else None,
            rationale="时间列和数值列适合折线图，可同时展示趋势。",
        )
    elif "trend" in text or "趋势" in description or datetime_cols:
        plan = ChartPlan(
            chart_type="line",
            x=datetime_cols[0] if datetime_cols else numeric[0],
            y=numeric[-1] if numeric else None,
            color=categorical[0] if categorical else None,
            rationale="用户关注趋势，推荐折线图。",
        )
    elif _is_distribution_request(description, text) and categorical and numeric:
        x_col = (
            _match_named_column(profile, description, text, ("categorical",))
            or categorical[0]
        )
        y_col = (
            _match_named_column(profile, description, text, ("continuous",))
            or numeric[0]
        )
        plan = ChartPlan(
            chart_type="box",
            x=x_col,
            y=y_col,
            color=None,
            aggregation="none",
            error_bar=None,
            rationale="用户关注分组分布/偏态/异常值，推荐箱线图并叠加原始数据点。",
            risks=["每组样本量较小时须线退化为最小/最大值，需结合散点判断。"],
        )
    elif "correl" in text or "相关" in description or len(numeric) >= 2:
        plan = ChartPlan(
            chart_type="scatter",
            x=numeric[0],
            y=numeric[1] if len(numeric) > 1 else None,
            color=categorical[0] if categorical else None,
            rationale="多个数值列适合散点图展示相关关系。",
            risks=["散点只能提示相关，不能证明因果。"],
        )
    elif "distrib" in text or "分布" in description:
        plan = ChartPlan(
            chart_type="histogram",
            x=numeric[0] if numeric else None,
            color=categorical[0] if categorical else None,
            rationale="用户关注分布，推荐直方图。",
        )
    elif categorical and numeric:
        plan = ChartPlan(
            chart_type="bar",
            x=categorical[0],
            y=numeric[0],
            color=categorical[1] if len(categorical) > 1 else None,
            aggregation="mean",
            error_bar="sem",
            rationale="分类变量和数值变量适合带误差线的柱状图。",
            risks=["默认使用均值，请确认数据语义允许聚合。"],
        )
    else:
        plan = ChartPlan(
            chart_type="histogram",
            x=numeric[0] if numeric else None,
            rationale="没有更明确的维度组合，默认展示首个数值列分布。",
        )
    _drop_noop_aggregation(plan, profile)
    return plan


def _drop_noop_aggregation(plan: ChartPlan, profile: DataProfile) -> None:
    """当推荐方案的 (x, color) 组合每单元格只有一行时，取消聚合与误差棒。"""
    pairs = redundancy_hint_pairs(profile)
    if not pairs:
        return
    x = plan.x
    color = plan.color
    if any(x == other and color == cat for other, cat in pairs) or any(
        x == other and color is None for other, cat in pairs
    ):
        plan.aggregation = "none"
        plan.error_bar = None
        plan.risks = [r for r in plan.risks if "聚合" not in r]


def _is_distribution_request(description: str, text: str) -> bool:
    """判断用户是否关注分布形态（偏态/异常值/分组比较）。"""
    zh_hints = ("分布", "偏态", "异常值", "离群", "箱线", "小提琴", "分位数")
    en_hints = (
        "distribution",
        "skew",
        "outlier",
        "boxplot",
        "violin",
        "spread",
        "spreadness",
    )
    return any(h in description for h in zh_hints) or any(h in text for h in en_hints)


async def recommend_chart_plan(
    profile: DataProfile,
    description: str,
    style: str,
    language: str,
    llm: Any | None = None,
) -> ChartPlan:
    """优先用 LLM 生成结构化图表方案，失败时回退到规则。"""
    fallback = deterministic_chart_plan(profile, description)
    if llm is None:
        return fallback
    cfg = get_config()
    if not cfg.llm.api_key:
        return fallback

    system_prompt = get_prompt(
        "plot_gen",
        "chart_recommendation_system",
        language=language,
    )
    pairs = redundancy_hint_pairs(profile)
    hint_text = ""
    if pairs:
        lines = []
        for other, cat in pairs[:3]:
            lines.append(
                f"- {other} x {cat}: one row per cell (rows == unique product)"
            )
        hint_text = (
            "\n\nREDUNDANCY HINT:\n"
            + "\n".join(lines)
            + "\nFor these pairs set aggregation='none' and error_bar='none'."
        )
    user_content = (
        f"User request:\n{description}\n\n"
        f"Target style: {style}\n\n"
        f"Data profile:\n{build_profile_context(profile)}"
        f"{hint_text}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        return await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=ChartPlan,
            max_retries=3,
            logger_name="plot_gen.recommender",
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning("Chart recommendation failed, fallback to rules: %s", e)
        return fallback

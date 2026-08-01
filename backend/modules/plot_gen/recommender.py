"""图表类型推荐：确定性规则 + LLM 结构化推荐。"""

from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.modules.plot_gen.models import ChartPlan, DataProfile
from backend.utils.json_utils import retry_with_validation

logger = get_logger(__name__)


def deterministic_chart_plan(
    profile: DataProfile, description: str = ""
) -> ChartPlan:
    """根据数据形状和用户描述返回规则兜底方案。"""
    numeric = [
        c.name
        for c in profile.column_profiles
        if c.inferred_type == "continuous"
    ]
    categorical = [
        c.name
        for c in profile.column_profiles
        if c.inferred_type in ("categorical", "id")
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
    return plan


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
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"User request:\n{description}\n\n"
                f"Target style: {style}\n\n"
                f"Data summary:\n{profile.raw_summary}"
            ),
        },
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

"""Writing Chat 工具调用执行前门禁（主对话流与编辑器助手共享）。

ToolRegistry 全局存有各管理器注册的全部工具，``execute()`` 只查运行时
enabled 标志、不复查工具来源，而执行器一经注册即常驻（单例重置不清
注册表条目）。若只在 schema 收集层把关功能开关，模型幻觉调用的任意
全局已注册工具会被直接执行，绕过开关产生真实网络请求或写盘。
故每条 tool_call 执行前必须双重把关：

1. 白名单：工具名必须出现在本轮注入 LLM 的 schema 中（schema 收集层
   已按各功能开关过滤），消费方按各自来源传入；
2. 源开关复核：联网 / 学术工具按与 schema 收集同口径复核源开关，
   兜住 schema 收集与执行之间配置热重载的窗口。

未过门禁的调用不执行、不推送 tool_status（无真实执行可指示），注入
模型可自纠的失败 tool result 并记 warning 日志。
"""

from backend.core.config import get_config

# 联网搜索 / 学术检索工具名分组（执行期源开关复核的判定依据）。
# 编辑器侧的确认流分组与静态白名单由两组派生，保持单一来源。
WEB_TOOL_NAMES = frozenset(
    {"web_search", "open_page", "read_page_sections", "find_in_page"}
)
SCHOLARLY_TOOL_NAMES = frozenset(
    {
        "search_scholarly_works",
        "get_work_details",
        "find_related_works",
        "get_full_text_or_sections",
        "search_research_data_resources",
    }
)


def tool_source_gate_open(tool_name: str) -> bool:
    """执行期复核联网/学术工具的源开关。

    web 源为全局开关 AND Writing Chat 灰度总闸，学术源为
    scholarly_research.enabled AND providers.openalex.enabled（与
    conversation.py / editor_chat.py 的 schema 收集同口径双重把关）。
    项目工具源只读且注入 schema 时已过 workspace 门，不在此复核。
    """
    cfg = get_config()
    if tool_name in WEB_TOOL_NAMES:
        return bool(
            cfg.features.function_calling.web_search.enabled
            and cfg.features.writing_chat.web_search_enabled
        )
    if tool_name in SCHOLARLY_TOOL_NAMES:
        research_cfg = cfg.features.function_calling.scholarly_research
        return bool(research_cfg.enabled and research_cfg.providers.openalex.enabled)
    return True


def blocked_tool_result_text(tool_name: str, consumer: str) -> str:
    """构造被门禁拦截的 tool result 文本（模型侧消息，不面向用户、无需 i18n）。

    文案需让模型可自纠：明确该工具本轮不可用、只准使用本轮提供的工具，
    引导其基于已有信息继续或调整策略，而非原样重试。
    """
    return (
        f'Tool "{tool_name}" is not available to the {consumer}. '
        "Use only the tools provided in this conversation."
    )

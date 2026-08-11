"""
联网搜索模块 — 多引擎搜索抽象层。

职责：
    1. 定义标准化的搜索结果数据模型（SearchResult / SearchResponse）
    2. 提供搜索引擎抽象基类（BaseSearchEngine），便于扩展 DuckDuckGo、SearXNG 等
    3. 通过 SearchManager 统一管理引擎注册、优先级、降级与健康检查
    4. 对外暴露 get_tools_schema()，返回可直接注入 LLM Function Calling 的工具定义

设计原则：
    - 搜索能力全局可用，不绑定特定业务模块
    - 引擎注册采用优先级 + 自动降级策略
    - 所有 I/O 操作均为异步，不阻塞事件循环
    - 全局单例模式，通过 get_search_manager() 获取
"""

from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.deep_read import DeepReader
from backend.utils.web_search.engines.ddg import (
    DDGChallengeError,
    DDGChallengeSubmitRequest,
    DuckDuckGoHtmlEngine,
    get_challenge,
    remove_challenge,
)
from backend.utils.web_search.engines.mock import MockSearchEngine
from backend.utils.web_search.engines.searxng import SearXNGEngine
from backend.utils.web_search.engines.tavily import TavilyEngine
from backend.utils.web_search.manager import (
    SearchManager,
    get_search_manager,
    reset_search_manager,
)
from backend.utils.web_search.models import SearchResponse, SearchResult
from backend.utils.web_search.query_rewriter import QueryRewriter, SearchIntent

__all__ = [
    # 数据模型
    "SearchResult",
    "SearchResponse",
    "SearchIntent",
    # Deep Read
    "DeepReader",
    # 查询重写
    "QueryRewriter",
    # 引擎基类
    "BaseSearchEngine",
    # 具体引擎
    "DuckDuckGoHtmlEngine",
    "SearXNGEngine",
    "TavilyEngine",
    "MockSearchEngine",
    # DDG 验证码
    "DDGChallengeError",
    "DDGChallengeSubmitRequest",
    "get_challenge",
    "remove_challenge",
    # 管理器
    "SearchManager",
    "get_search_manager",
    "reset_search_manager",
]

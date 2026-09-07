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
from backend.utils.web_search.deep_read import DeepReader, close_deep_reader
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
from backend.utils.web_search.errors import FetchArtifact, FetchFailure
from backend.utils.web_search.extract import (
    ContentBlock,
    DocumentChunk,
    DocumentSection,
    EvidenceBundle,
    EvidenceChunk,
    ExtractedDocument,
    LinkRef,
)
from backend.utils.web_search.fetch.browser import BrowserHealth
from backend.utils.web_search.manager import (
    SearchManager,
    close_search_manager,
    get_search_manager,
    reset_search_manager,
)
from backend.utils.web_search.models import (
    EngineStatus,
    FindInPageResult,
    PageReadResult,
    SearchCandidate,
    SearchResponse,
    SearchResult,
)
from backend.utils.web_search.page_reader import (
    StructuredPageReader,
    close_structured_page_reader,
    get_structured_page_reader,
    initialize_structured_page_reader,
)
from backend.utils.web_search.planner import SearchQueryPlan, SearchQueryPlanner
from backend.utils.web_search.query_rewriter import QueryRewriter, SearchIntent
from backend.utils.web_search.ranking import rank_responses
from backend.utils.web_search.url_utils import (
    canonicalize_url,
    classify_source,
    url_is_safe,
)

__all__ = [
    # 数据模型
    "SearchResult",
    "SearchResponse",
    "SearchIntent",
    "SearchQueryPlan",
    "SearchQueryPlanner",
    "SearchCandidate",
    "EngineStatus",
    "FetchArtifact",
    "FetchFailure",
    "BrowserHealth",
    "ContentBlock",
    "DocumentSection",
    "DocumentChunk",
    "EvidenceBundle",
    "EvidenceChunk",
    "ExtractedDocument",
    "LinkRef",
    "PageReadResult",
    "FindInPageResult",
    # Deep Read
    "DeepReader",
    "close_deep_reader",
    "StructuredPageReader",
    "get_structured_page_reader",
    "close_structured_page_reader",
    "initialize_structured_page_reader",
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
    "close_search_manager",
    "get_search_manager",
    "reset_search_manager",
    "rank_responses",
    "canonicalize_url",
    "classify_source",
    "url_is_safe",
]

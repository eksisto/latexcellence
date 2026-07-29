"""搜索结果数据模型。"""

from pydantic import BaseModel


class SearchResult(BaseModel):
    """单条搜索结果。"""

    title: str
    snippet: str
    url: str
    source: str = ""
    full_content: str = ""  # Deep Read 抓取的页面正文，空字符串表示未抓取


class SearchResponse(BaseModel):
    """一次搜索的完整响应。"""

    query: str
    results: list[SearchResult] = []
    engine: str = ""
    total_results: int = 0
    search_time_ms: float = 0.0
    original_query: str = ""  # 查询重写前的原始用户查询
    rewritten_queries: list[str] = []  # 实际执行的多条查询

    def format_for_llm(self, max_results: int = 5) -> str:
        """将搜索结果格式化为适合注入 LLM 上下文的纯文本。"""
        items = self.results[:max_results]
        if not items:
            return f'No relevant results found for search "{self.query}".'
        header = f'Search "{self.query}" returned {len(items)} result(s)'
        if self.rewritten_queries:
            header += f' (from queries: {", ".join(self.rewritten_queries)})'
        header += ":\n"
        lines = [header]
        for i, r in enumerate(items, 1):
            lines.append(f"[{i}] {r.title}")
            if r.snippet:
                lines.append(f"    {r.snippet}")
            if r.url:
                lines.append(f"    Source: {r.url}")
            if r.full_content:
                lines.append(f"    Page Content ({len(r.full_content)} chars):")
                lines.append(f"    {r.full_content}")
            lines.append("")
        return "\n".join(lines)

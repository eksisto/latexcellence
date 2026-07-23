"""搜索结果数据模型。"""

from pydantic import BaseModel


class SearchResult(BaseModel):
    """单条搜索结果。"""
    title: str
    snippet: str
    url: str
    source: str = ""


class SearchResponse(BaseModel):
    """一次搜索的完整响应。"""
    query: str
    results: list[SearchResult] = []
    engine: str = ""
    total_results: int = 0
    search_time_ms: float = 0.0

    def format_for_llm(self, max_results: int = 5) -> str:
        """将搜索结果格式化为适合注入 LLM 上下文的纯文本。"""
        items = self.results[:max_results]
        if not items:
            return f"搜索 \"{self.query}\" 未找到相关结果。"
        lines = [f"搜索 \"{self.query}\" 返回 {len(items)} 条结果：\n"]
        for i, r in enumerate(items, 1):
            lines.append(f"[{i}] {r.title}")
            if r.snippet:
                lines.append(f"    {r.snippet}")
            if r.url:
                lines.append(f"    来源：{r.url}")
            lines.append("")
        return "\n".join(lines)

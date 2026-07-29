"""Deep Read — 抓取搜索结果页面正文并提取主要内容。"""

import asyncio
from urllib.parse import urlparse

import httpx
import trafilatura

from backend.core.config import get_config
from backend.core.logger import get_logger


class DeepReader:
    """抓取并提取网页正文内容，供 LLM 获取页面完整信息。"""

    def __init__(self) -> None:
        self._logger = get_logger("deep_read")

    async def fetch_content(
        self, url: str, max_chars: int = 4000, timeout: int = 10
    ) -> str:
        """抓取单页并提取正文。失败返回空字符串。"""
        if urlparse(url).scheme != "https":
            self._logger.warning(f"Deep read skipped non-https URL: {url[:80]}")
            return ""
        try:
            cfg = get_config()
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": cfg.http.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = self._extract_text(resp.text)
                if len(text) > max_chars:
                    text = text[:max_chars] + "..."
                return text
        except Exception as e:
            self._logger.warning(f"Deep read failed for {url[:80]}: {e}")
            return ""

    def _extract_text(self, html: str) -> str:
        """使用 trafilatura 从 HTML 中提取正文，去除导航/广告等噪音。"""
        text = trafilatura.extract(html, output_format="txt", include_tables=False)
        return text or ""

    async def fetch_top_results(
        self,
        results: list,
        max_pages: int = 3,
        max_chars_per_page: int = 4000,
        timeout: int = 10,
    ) -> None:
        """并发抓取前 N 条结果的正文，原地修改 SearchResult 对象。"""
        if not results:
            return
        targets = results[:max_pages]
        tasks = [
            self.fetch_content(r.url, max_chars_per_page, timeout) for r in targets
        ]
        contents = await asyncio.gather(*tasks)
        for r, content in zip(targets, contents):
            if content:
                r.full_content = content

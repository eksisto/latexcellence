"""Deep Read — 抓取搜索结果页面正文并提取主要内容。"""

import asyncio
import time
from urllib.parse import urljoin

import httpx
import trafilatura

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.url_utils import url_is_safe

#: 手动跟随重定向的最大跳数（每跳都重新做 SSRF 校验）
_MAX_REDIRECTS = 5


class DeepReader:
    """抓取并提取网页正文内容，供 LLM 获取页面完整信息。"""

    def __init__(self) -> None:
        self._logger = get_logger("deep_read")
        # URL -> (fetched_at, title, content) 内存缓存，open_page 与自动 Deep Read
        # 共用同一份抓取结果，避免同一页面重复请求；5 分钟过期
        self._cache: dict[str, tuple[float, str, str]] = {}
        self._cache_ttl = 300
        # 进程级共享客户端：连接复用，避免每次抓取新建（AGENTS.md 客户端生命周期）
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取共享 httpx 异步客户端（懒创建；重定向手动跟随，超时按请求传入）。"""
        if self._client is None or self._client.is_closed:
            cfg = get_config()
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                headers={
                    "User-Agent": cfg.http.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                },
            )
        return self._client

    async def close(self) -> None:
        """释放共享客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_content(
        self, url: str, max_chars: int = 4000, timeout: int = 10
    ) -> str:
        """抓取单页并提取正文。失败返回空字符串（兼容旧调用方）。"""
        _title, content = await self.fetch_page(url, max_chars, timeout)
        return content

    async def fetch_page(
        self, url: str, max_chars: int = 10000, timeout: int = 10
    ) -> tuple[str, str]:
        """
        抓取单页并提取正文与标题。失败返回 ("", "")。

        只允许公网 HTTPS；重定向手动跟随（最多 5 跳），每一跳重新校验目标，
        防止 https 页面把请求重定向到内网地址。
        """
        current = url
        for _ in range(_MAX_REDIRECTS):
            if not await url_is_safe(current):
                self._logger.warning(f"Deep read blocked unsafe URL: {current[:80]}")
                return "", ""

            cached = self._cache.get(current)
            if cached and time.monotonic() - cached[0] < self._cache_ttl:
                title, full_text = cached[1], cached[2]
                if len(full_text) > max_chars:
                    return title, full_text[:max_chars] + "..."
                return title, full_text

            try:
                resp = await self._get_client().get(current, timeout=timeout)
            except Exception as e:
                self._logger.warning(f"Deep read failed for {current[:80]}: {e}")
                return "", ""

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    return "", ""
                current = urljoin(current, location)
                continue

            try:
                resp.raise_for_status()
            except Exception as e:
                self._logger.warning(
                    f"Deep read returned error for {current[:80]}: {e}"
                )
                return "", ""

            title, text = self._extract_page(resp.text)
            # 缓存完整正文，按每次调用的 max_chars 截断，避免大页面被永久截短
            fetched_at = time.monotonic()
            self._cache[current] = (fetched_at, title, text)
            if current != url:
                self._cache[url] = (fetched_at, title, text)
            self._evict_expired()
            if len(text) > max_chars:
                return title, text[:max_chars] + "..."
            return title, text

        self._logger.warning(f"Deep read exceeded redirect limit: {url[:80]}")
        return "", ""

    def _evict_expired(self) -> None:
        """缓存超过阈值时清理过期条目，防止长会话内无界增长。"""
        if len(self._cache) <= 200:
            return
        now = time.monotonic()
        expired = [
            key
            for key, (fetched_at, _, _) in self._cache.items()
            if now - fetched_at >= self._cache_ttl
        ]
        for key in expired:
            self._cache.pop(key, None)

    def _extract_page(self, html: str) -> tuple[str, str]:
        """使用 trafilatura 从 HTML 中提取标题与正文，去除导航/广告等噪音。"""
        text = trafilatura.extract(html, output_format="txt", include_tables=False)
        if not text:
            return "", ""
        title = ""
        try:
            meta = trafilatura.extract_metadata(html)
            if meta is not None and meta.title:
                title = meta.title.strip()
        except Exception:
            pass
        return title, text

    async def find_in_page(
        self,
        url: str,
        query: str,
        context_chars: int = 600,
        max_matches: int = 5,
        max_chars: int = 10000,
        timeout: int = 10,
    ) -> list[str]:
        """
        在页面正文中定位关键词，返回匹配位置前后的上下文片段。

        使用朴素字符串查找（小写化 + find 循环），避免正则灾难性回溯。
        """
        _title, content = await self.fetch_page(
            url, max_chars=max_chars, timeout=timeout
        )
        if not content:
            return []
        needle = (query or "").strip().lower()
        if not needle:
            return []
        haystack = content.lower()
        matches: list[str] = []
        start = 0
        half = max(0, context_chars) // 2
        while len(matches) < max(1, max_matches):
            pos = haystack.find(needle, start)
            if pos == -1:
                break
            ctx_start = max(0, pos - half)
            ctx_end = min(len(content), pos + len(query) + half)
            prefix = "..." if ctx_start > 0 else ""
            suffix = "..." if ctx_end < len(content) else ""
            matches.append(prefix + content[ctx_start:ctx_end].strip() + suffix)
            start = pos + len(query)
        return matches

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
        tasks = [self.fetch_page(r.url, max_chars_per_page, timeout) for r in targets]
        pages = await asyncio.gather(*tasks)
        for r, (title, content) in zip(targets, pages):
            if content:
                r.full_content = content
                r.source = r.source or title


# ── 全局单例 ────────────────────────────────────────────────────────────────

_deep_reader: DeepReader | None = None


def get_deep_reader() -> DeepReader:
    """
    获取全局共享的 DeepReader 实例。

    open_page / find_in_page / 自动 Deep Read 共用同一份抓取缓存，
    避免同一页面在工具调用之间被重复请求。
    """
    global _deep_reader
    if _deep_reader is None:
        _deep_reader = DeepReader()
    return _deep_reader


async def close_deep_reader() -> None:
    """释放 DeepReader 共享客户端（服务关闭时调用）。"""
    global _deep_reader
    if _deep_reader is not None:
        await _deep_reader.close()
        _deep_reader = None

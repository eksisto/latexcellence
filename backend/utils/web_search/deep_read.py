"""Deep Read — 抓取搜索结果页面正文并提取主要内容。"""

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from backend.core.config import get_config
from backend.core.logger import get_logger

#: 手动跟随重定向的最大跳数（每跳都重新做 SSRF 校验）
_MAX_REDIRECTS = 5


def _ip_is_blocked(ip_text: str) -> bool:
    """判断 IP 是否属于不应抓取的私网/环回/保留地址。"""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True  # 非 IP 文本由调用方按 hostname 解析处理
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return _ip_is_blocked(str(ip.ipv4_mapped))
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _host_is_blocked(host: str) -> bool:
    """
    判断 host 是否指向内网/环回/保留地址。

    hostname 会做 DNS 解析并逐个检查解析结果，任一地址落在私网段即拦截；
    解析失败按不安全处理（fail closed）。
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _ip_is_blocked(host)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return True
    return any(_ip_is_blocked(info[4][0]) for info in infos)


async def _url_is_safe(url: str) -> bool:
    """open_page / Deep Read 只允许抓取公网 HTTPS 页面，防 SSRF。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.hostname or ""
        return bool(host) and not await _host_is_blocked(host)
    except Exception:
        return False


class DeepReader:
    """抓取并提取网页正文内容，供 LLM 获取页面完整信息。"""

    def __init__(self) -> None:
        self._logger = get_logger("deep_read")
        # URL -> (fetched_at, title, content) 内存缓存，open_page 与自动 Deep Read
        # 共用同一份抓取结果，避免同一页面重复请求；5 分钟过期
        self._cache: dict[str, tuple[float, str, str]] = {}
        self._cache_ttl = 300

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
            if not await _url_is_safe(current):
                self._logger.warning(f"Deep read blocked unsafe URL: {current[:80]}")
                return "", ""

            cached = self._cache.get(current)
            if cached and time.monotonic() - cached[0] < self._cache_ttl:
                title, full_text = cached[1], cached[2]
                if len(full_text) > max_chars:
                    return title, full_text[:max_chars] + "..."
                return title, full_text

            try:
                cfg = get_config()
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    headers={
                        "User-Agent": cfg.http.user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                    },
                ) as client:
                    resp = await client.get(current)
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

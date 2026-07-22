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

import base64
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.time_utils import get_current_time_context

# ── UA ─────────────────────────────────────────────────────────────────────


def _get_user_agent() -> str:
    """从配置获取 User-Agent。"""
    return get_config().features.web_search.user_agent

# ── 数据模型 ────────────────────────────────────────────────────────────────


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


# ── 引擎抽象 ────────────────────────────────────────────────────────────────


class BaseSearchEngine(ABC):
    """
    搜索引擎抽象基类。

    子类需实现：
        - name: 引擎唯一标识
        - search(): 执行搜索
        - health_check(): 检查引擎是否可用
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """检查引擎是否可用（网络连通性、API Key 有效性等）。"""
        ...


# ── Mock 引擎 ────────────────────────────────────────────────────────────────


class MockSearchEngine(BaseSearchEngine):
    """
    Mock 搜索引擎 — 用于测试和降级。

    始终返回空结果，保证架构可用但不发起真实网络请求。
    """

    name = "mock"

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=[],
            engine="mock",
            total_results=0,
        )

    async def health_check(self) -> bool:
        return True


# ── SearXNG 引擎 ────────────────────────────────────────────────────────


class SearXNGEngine(BaseSearchEngine):
    """
    SearXNG 搜索引擎。

    通过 SearXNG 实例的 JSON API 执行搜索。
    需要在 config.json 中配置 engines.searxng.base_url 指向可用的 SearXNG 实例。
    """

    name = "searxng"

    def __init__(self, base_url: str = "") -> None:
        self._logger = get_logger("searxng")
        self._base_url = base_url.rstrip("/")

    def _get_base_url(self) -> str:
        """获取 SearXNG 实例地址，优先使用运行时配置。"""
        cfg = get_config()
        url = cfg.features.web_search.engines.searxng.base_url or self._base_url
        return url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索，返回标准化结果。"""
        base_url = self._get_base_url()
        if not base_url:
            self._logger.warning("SearXNG 实例地址未配置")
            return SearchResponse(query=query, engine=self.name, results=[])

        cfg = get_config()
        client = httpx.AsyncClient(
            timeout=cfg.features.web_search.timeout_seconds,
            follow_redirects=True,
        )
        try:
            return await self._do_search(client, base_url, query, max_results)
        finally:
            await client.aclose()

    async def _do_search(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        """实际搜索逻辑。"""
        url = f"{base_url}/search"
        params = {
            "q": query,
            "format": "json",
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": _get_user_agent(),
        }

        self._logger.info(f"SearXNG search: {query[:60]} | instance={base_url}")
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.ConnectError as e:
            self._logger.error(f"SearXNG 连接失败：{e}")
            return SearchResponse(query=query, engine=self.name, results=[])

        if resp.status_code != 200:
            self._logger.warning(f"SearXNG HTTP {resp.status_code} | body={resp.text[:200]}")
            return SearchResponse(query=query, engine=self.name, results=[])

        data = resp.json()
        raw_results = data.get("results", [])
        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source=item.get("engine", ""),
            ))

        self._logger.info(f"SearXNG parsed {len(results)} results for: {query[:40]}")
        return SearchResponse(
            query=query,
            results=results,
            engine=self.name,
            total_results=len(raw_results),
        )

    async def health_check(self) -> bool:
        """健康检查：尝试访问 SearXNG 实例。"""
        base_url = self._get_base_url()
        if not base_url:
            return False
        try:
            client = httpx.AsyncClient(timeout=5)
            try:
                headers = {
                    "Accept": "application/json",
                    "User-Agent": _get_user_agent(),
                }
                resp = await client.get(
                    f"{base_url}/search",
                    params={"q": "test", "format": "json"},
                    headers=headers,
                )
                return resp.status_code == 200
            finally:
                await client.aclose()
        except Exception:
            return False


# ── DDG 验证码异常 ──────────────────────────────────────────────────────

_DD_GK = "d4cd0dabcf4caa22ad92fab40844c786"  # NA
_DD_IMAGE_BASE = "https://duckduckgo.com/assets/anomaly/images/challenge"
_DD_HTML_URL = "https://html.duckduckgo.com/html/"
_DD_SUBMIT_BASE = "https://duckduckgo.com/anomaly.js"
_DD_CHALLENGE_TTL = 300  # 5 分钟过期


class DDGChallengeError(Exception):
    """
    DDG 验证码异常。

    当 DDG 返回 HTTP 202（challenge 页面）时抛出，
    携带 challenge 元数据和图片 base64 数据，供上层推送到前端。
    """

    def __init__(
        self,
        challenge_id: str,
        images: list[dict[str, str]],
        query: str,
        submission_url: str,
        form_data: dict[str, str],
        engine_name: str = "",
        instruction: str = "",
        attempts_left: int = 3,
    ):
        self.challenge_id = challenge_id
        self.images = images          # [{"hash": "...", "base64": "..."}]
        self.query = query
        self.submission_url = submission_url
        self.form_data = form_data    # 额外的隐藏表单字段
        self.engine_name = engine_name  # 引擎显示名称（如 DuckDuckGo）
        self.instruction = instruction  # 引擎特定的验证提示文字
        self.attempts_left = attempts_left
        super().__init__(f"DDG challenge for query: {query}")


# ── DDG Challenge 存储（内存 + TTL） ─────────────────────────────────────────

_challenge_store: dict[str, dict[str, Any]] = {}


def _store_challenge(data: dict[str, Any]) -> str:
    """存储 challenge 数据，返回 challenge_id。"""
    _cleanup_challenge_store()
    cid = uuid.uuid4().hex[:12]
    _challenge_store[cid] = {**data, "created_at": time.monotonic()}
    return cid


def get_challenge(challenge_id: str) -> dict[str, Any] | None:
    """获取 challenge 数据，过期返回 None。"""
    entry = _challenge_store.get(challenge_id)
    if entry is None:
        return None
    if time.monotonic() - entry["created_at"] > _DD_CHALLENGE_TTL:
        _challenge_store.pop(challenge_id, None)
        return None
    return entry


def remove_challenge(challenge_id: str) -> None:
    """提交后移除 challenge 数据。"""
    _challenge_store.pop(challenge_id, None)


def _cleanup_challenge_store() -> None:
    """清理过期 challenge。"""
    now = time.monotonic()
    expired = [k for k, v in _challenge_store.items() if now - v["created_at"] > _DD_CHALLENGE_TTL]
    for k in expired:
        _challenge_store.pop(k, None)


# ── DDG HTML 引擎 ───────────────────────────────────────────────────────────


class DuckDuckGoHtmlEngine(BaseSearchEngine):
    """
    DuckDuckGo HTML（no-JS）搜索引擎。

    通过 POST 表单提交搜索请求，模拟浏览器行为：
    - 使用真实 Chrome User-Agent
    - 携带完整 Sec-Fetch-* 头
    - 设置 Referer: https://html.duckduckgo.com/

    当 DDG 触发 Challenge（HTTP 202）时抛出 DDGChallengeError，
    由上层 service 捕获并推送到前端。
    """

    name = "duckduckgo"

    def __init__(self) -> None:
        self._logger = get_logger("ddg_html")

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 httpx 异步客户端。"""
        cfg = get_config()
        return httpx.AsyncClient(
            timeout=cfg.features.web_search.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _get_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索。遇到 DDG 验证码时抛出 DDGChallengeError。"""
        if len(query) >= 500:
            return SearchResponse(query=query, engine=self.name, results=[])

        client = await self._get_client()
        try:
            return await self._do_search(client, query, max_results)
        finally:
            await client.aclose()

    async def _do_search(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> SearchResponse:
        """实际搜索逻辑。"""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": _DD_HTML_URL,
        }
        data = {"q": query, "b": ""}

        self._logger.info(f"DDG HTML search: {query[:60]}")
        resp = await client.post(
            _DD_HTML_URL,
            headers=headers,
            content=_urlencode(data),
        )
        self._logger.debug(f"DDG response: status={resp.status_code}")

        # ── Challenge（HTTP 202）───────────────────────────────────
        if resp.status_code == 202:
            raise await self._parse_challenge(resp.text, query, client)

        # ── 正常结果（HTTP 200）───────────────────────────────────
        if resp.status_code != 200:
            self._logger.warning(f"DDG non-200: {resp.status_code}")
            return SearchResponse(query=query, engine=self.name)

        return self._parse_results(resp.text, query, max_results)

    # ── 结果解析 ──────────────────────────────────────────────────────

    def _parse_results(self, html: str, query: str, max_results: int = 5) -> SearchResponse:
        """从 DDG HTML 页面提取搜索结果。"""
        soup = BeautifulSoup(html, "lxml")
        results: list[SearchResult] = []

        for div in soup.select("#links .web-result"):
            title_el = div.select_one("h2 a")
            snippet_el = div.select_one("a.result__snippet")
            if not title_el:
                continue
            url = title_el.get("href", "")
            # DDG 的链接可能被重定向，提取真实 URL
            if url.startswith("//duckduckgo.com/l/"):
                # 跳过广告等重定向链接
                continue
            results.append(SearchResult(
                title=title_el.get_text(strip=True),
                url=url,
                snippet=snippet_el.get_text(strip=True) if snippet_el else "",
            ))

        self._logger.info(f"DDG parsed {len(results)} results for: {query[:40]}")
        return SearchResponse(
            query=query,
            results=results[:max_results],
            engine=self.name,
            total_results=len(results),
        )

    # ── 验证码 解析 ──────────────────────────────────────────────────────

    async def _parse_challenge(
        self, html: str, query: str, client: httpx.AsyncClient
    ) -> DDGChallengeError:
        """解析 DDG challenge 页面，下载图片，返回 DDGChallengeError。"""
        soup = BeautifulSoup(html, "lxml")

        # 提取 challenge-form
        form = soup.select_one("form#challenge-form")
        if form is None:
            # 也可能是其他形式的 challenge
            self._logger.warning("DDG challenge form#challenge-form not found")
            return DDGChallengeError(
                challenge_id="",
                images=[],
                query=query,
                submission_url="",
                form_data={},
                engine_name="DuckDuckGo",
                instruction="",
            )

        action = form.get("action", "")
        if action.startswith("//"):
            action = "https:" + action

        # 从 action URL 解析隐藏参数
        parsed = urlparse(action)
        params = parse_qs(parsed.query)

        gk = params.get("gk", [_DD_GK])[0]
        encoded_state = params.get("o", [""])[0]
        region = params.get("r", [""])[0]
        cc = params.get("cc", ["sre"])[0]
        p_str = params.get("p", [""])[0]
        q_param = params.get("q", [query])[0]

        image_hashes = [h for h in p_str.split("-") if h]
        self._logger.warning(
            f"DDG challenge triggered | query={query[:40]} | "
            f"images={len(image_hashes)} | region={region}"
        )

        # 下载 challenge 图片并转 base64
        images = await self._fetch_challenge_images(image_hashes, client)

        # 构建提交 URL（不带 st，st 由提交时生成）
        base_params = {
            "sv": "html",
            "cc": cc,
            "gk": gk,
            "p": p_str,
            "q": q_param,
            "o": encoded_state,
            "r": region,
        }
        # 隐藏表单字段
        form_data: dict[str, str] = {
            "gk": gk,
            "challenge-submit": gk,
        }
        # 隐藏 input 中的其他字段
        for inp in form.select("input[type=hidden]"):
            name = inp.get("name", "")
            val = inp.get("value", "")
            if name and name not in ("gk", "challenge-submit"):
                form_data[name] = val

        challenge_id = _store_challenge({
            "query": query,
            "gk": gk,
            "image_hashes": image_hashes,
            "encoded_state": encoded_state,
            "region": region,
            "cc": cc,
            "p": p_str,
            "q_param": q_param,
            "base_params": base_params,
            "form_data": form_data,
        })

        return DDGChallengeError(
            challenge_id=challenge_id,
            images=images,
            query=query,
            submission_url=action,
            form_data=form_data,
            engine_name="DuckDuckGo",
            instruction="选择所有包含鸭子的图片",
        )

    async def _fetch_challenge_images(
        self, hashes: list[str], client: httpx.AsyncClient
    ) -> list[dict[str, str]]:
        """下载 challenge 图片并编码为 base64。"""
        images: list[dict[str, str]] = []
        for h in hashes:
            url = f"{_DD_IMAGE_BASE}/{h}.jpg"
            try:
                resp = await client.get(
                    url,
                    headers={"Referer": _DD_HTML_URL},
                )
                if resp.status_code == 200:
                    b64 = base64.b64encode(resp.content).decode("ascii")
                    images.append({"hash": h, "base64": b64})
                else:
                    self._logger.warning(f"DDG image fetch failed: {h} status={resp.status_code}")
            except Exception as e:
                self._logger.warning(f"DDG image fetch error: {h} {e}")
        return images

    async def health_check(self) -> bool:
        """健康检查：尝试访问 DDG HTML 页面。"""
        try:
            client = await self._get_client()
            try:
                resp = await client.get(_DD_HTML_URL)
                return resp.status_code == 200
            finally:
                await client.aclose()
        except Exception:
            return False

    # ── 验证码提交（静态方法，供 router 调用）──────────────────

    @staticmethod
    async def submit_challenge(
        challenge_id: str, selected_hashes: list[str]
    ) -> dict[str, Any]:
        """
        提交 DDG challenge 解答。

        返回：
            {"success": True, "results": [...]} 或
            {"success": False, "message": "...", "attempts_left": N}
        """
        entry = get_challenge(challenge_id)
        if entry is None:
            return {"success": False, "message": "Challenge expired or not found"}

        st = str(int(time.time()))

        # 构建提交 URL（对参数值做 URL 编码，防止换行符等非法字符）
        base_params = dict(entry["base_params"])
        base_params["st"] = st
        query_string = "&".join(
            f"{k}={quote(str(v), safe='')}" for k, v in base_params.items()
        )
        submit_url = f"{_DD_SUBMIT_BASE}?{query_string}"

        # 构建提交 body
        body_parts = [f"challenge-submit={entry['gk']}"]
        for h in selected_hashes:
            body_parts.append(f"image-check_{h}={h}")
        body = "&".join(body_parts)

        cfg = get_config()
        client = httpx.AsyncClient(
            timeout=cfg.features.web_search.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _get_user_agent(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": _DD_HTML_URL,
                "Origin": "https://html.duckduckgo.com",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        try:
            resp = await client.post(submit_url, content=body)
        finally:
            await client.aclose()

        if resp.status_code != 200:
            return {"success": False, "message": f"DDG returned HTTP {resp.status_code}"}

        text = resp.text

        # 检查是否仍有 challenge（提交失败）
        if "challenge-form" in text or "Please try again" in text:
            attempts_match = re.search(r"You have (\d+) attempt", text)
            attempts_left = int(attempts_match.group(1)) if attempts_match else 0
            return {
                "success": False,
                "message": "Verification failed, please retry",
                "attempts_left": attempts_left,
            }

        # 检查 IP 封禁
        if "error-lite@duckduckgo.com" in text:
            remove_challenge(challenge_id)
            return {
                "success": False,
                "message": "IP has been blocked, please try again later",
                "attempts_left": 0,
            }

        # 成功：解析搜索结果
        remove_challenge(challenge_id)
        soup = BeautifulSoup(text, "lxml")
        results: list[dict[str, str]] = []
        for div in soup.select("#links .web-result"):
            title_el = div.select_one("h2 a")
            snippet_el = div.select_one("a.result__snippet")
            if not title_el:
                continue
            url = title_el.get("href", "")
            if url.startswith("//duckduckgo.com/l/"):
                continue
            results.append({
                "title": title_el.get_text(strip=True),
                "url": url,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })

        return {"success": True, "results": results}


# ── 工具函数 ────────────────────────────────────────────────────────────────


def _urlencode(data: dict[str, str]) -> bytes:
    """将字典编码为 URL-encoded bytes（application/x-www-form-urlencoded）。"""
    return "&".join(f"{k}={v}" for k, v in data.items()).encode("utf-8")


# ── 搜索管理器 ──────────────────────────────────────────────────────────────


class SearchManager:
    """
    统一搜索接口。

    职责：
        - 管理多个搜索引擎的注册和优先级
        - 执行搜索时按优先级尝试，失败自动降级
        - 提供 Function Calling 工具 schema
    """

    def __init__(self) -> None:
        self._engines: list[BaseSearchEngine] = []
        self._primary: BaseSearchEngine | None = None
        self._logger = get_logger("web_search")

    def register_engine(
        self, engine: BaseSearchEngine, primary: bool = False
    ) -> None:
        """注册搜索引擎。primary=True 时设为主引擎。"""
        self._engines.append(engine)
        if primary:
            self._primary = engine
        self._logger.info(
            f"搜索引擎已注册：{engine.name} (primary={primary})"
        )

    def get_engine(self, name: str) -> BaseSearchEngine | None:
        """按名称获取已注册的引擎。"""
        for e in self._engines:
            if e.name == name:
                return e
        return None

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        engine_name: str | None = None,
    ) -> SearchResponse:
        """
        执行搜索。

        - 指定 engine_name 时使用指定引擎
        - 否则使用主引擎，失败时自动降级到其他可用引擎
        """
        cfg = get_config()
        if max_results is None:
            max_results = cfg.features.web_search.max_results

        # 指定引擎
        if engine_name:
            engine = self.get_engine(engine_name)
            if engine is None:
                return SearchResponse(
                    query=query,
                    engine="",
                )
            return await self._search_with_engine(engine, query, max_results)

        # 使用主引擎 + 降级
        if self._primary:
            try:
                return await self._search_with_engine(
                    self._primary, query, max_results
                )
            except DDGChallengeError:
                raise  # challenge 不降级，直接向上传播
            except Exception as e:
                self._logger.warning(
                    f"主引擎 {self._primary.name} 搜索失败：{e}，尝试降级"
                )

        # 降级到其他引擎
        for engine in self._engines:
            if engine is self._primary:
                continue
            try:
                return await self._search_with_engine(
                    engine, query, max_results
                )
            except Exception as e:
                self._logger.warning(f"引擎 {engine.name} 搜索失败：{e}")

        # 所有引擎均失败
        self._logger.error("所有搜索引擎均不可用")
        return SearchResponse(query=query, engine="none")

    async def _search_with_engine(
        self, engine: BaseSearchEngine, query: str, max_results: int
    ) -> SearchResponse:
        """使用指定引擎执行搜索，记录耗时。"""
        start = time.monotonic()
        result = await engine.search(query, max_results)
        elapsed_ms = (time.monotonic() - start) * 1000
        result.search_time_ms = elapsed_ms
        result.engine = engine.name
        self._logger.info(
            f"搜索完成 | engine={engine.name} | query={query[:50]} | "
            f"results={result.total_results} | time={elapsed_ms:.0f}ms"
        )
        return result

    def get_tools_schema(self) -> list[dict[str, Any]]:
        """
        返回可注入 LLM tools 参数的 Function Calling schema。

        供 ToolRegistry 或直接在 LLM 调用中使用。
        动态注入当前日期时间，帮助 LLM 理解相对时间概念。
        """
        t = get_current_time_context()

        description = (
            f"搜索互联网获取最新信息。当前日期：{t['date']}（{t['weekday']}），"
            f"时间：{t['time']}。当用户询问实时信息、最新数据、"
            f"或你不确定的事实时使用此工具。"
        )

        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索查询词，应简洁精准",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "返回结果数量上限，默认 5",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]


# ── 全局单例 ────────────────────────────────────────────────────────────────

_search_manager: SearchManager | None = None


async def _web_search_executor(tool_name: str, arguments: dict[str, Any]) -> SearchResponse:
    """web_search 工具执行函数，供 ToolRegistry 调用。"""
    manager = get_search_manager()
    cfg = get_config()
    result = await manager.search(
        query=arguments["query"],
        max_results=arguments.get("max_results", cfg.features.web_search.max_results),
    )
    return result


def reset_search_manager() -> None:
    """重置全局 SearchManager 单例，使配置变更（如切换引擎）立即生效。"""
    global _search_manager
    _search_manager = None


def get_search_manager() -> SearchManager:
    """获取全局唯一的 SearchManager 实例。"""
    global _search_manager
    if _search_manager is None:
        _search_manager = SearchManager()
        cfg = get_config()
        engines_cfg = cfg.features.web_search.engines

        # 根据每个引擎的 enabled 标志决定是否注册
        engine_registry: dict[str, BaseSearchEngine] = {}
        if engines_cfg.duckduckgo.enabled:
            engine_registry["duckduckgo"] = DuckDuckGoHtmlEngine()
        if engines_cfg.searxng.enabled:
            engine_registry["searxng"] = SearXNGEngine()

        primary = cfg.features.web_search.primary_engine
        for name, engine in engine_registry.items():
            _search_manager.register_engine(engine, primary=(name == primary))

        # 将搜索工具注册到 ToolRegistry，使 LLM tool_call 能正确分发
        from backend.core.tool_registry import get_tool_registry
        registry = get_tool_registry()
        schema = _search_manager.get_tools_schema()
        if schema:
            registry.register(
                name="web_search",
                schema=schema[0],
                executor=_web_search_executor,
            )

    return _search_manager


# ── DDG 验证码提交模型 ─────────────────────────────────────────────────

class DDGChallengeSubmitRequest(BaseModel):
    """提交 DDG 验证码解答。"""
    challenge_id: str
    selected_hashes: list[str]

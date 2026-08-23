"""DuckDuckGo HTML 搜索引擎实现。"""

import base64
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.web_search.base import BaseSearchEngine
from backend.utils.web_search.models import SearchResponse, SearchResult

# ── 常量 ─────────────────────────────────────────────────────────────────────

_DD_GK = "d4cd0dabcf4caa22ad92fab40844c786"  # NA
_DD_IMAGE_BASE = "https://duckduckgo.com/assets/anomaly/images/challenge"
_DD_HTML_URL = "https://html.duckduckgo.com/html/"
_DD_SUBMIT_BASE = "https://duckduckgo.com/anomaly.js"
_DD_CHALLENGE_TTL = 300  # 5 分钟过期


def _get_user_agent() -> str:
    """从配置获取 User-Agent。"""
    return get_config().http.user_agent


# ── 验证码异常 ────────────────────────────────────────────────────────────────


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
        self.images = images  # [{"hash": "...", "base64": "..."}]
        self.query = query
        self.submission_url = submission_url
        self.form_data = form_data  # 额外的隐藏表单字段
        self.engine_name = engine_name  # 引擎显示名称（如 DuckDuckGo）
        self.instruction = instruction  # 引擎特定的验证提示文字
        self.attempts_left = attempts_left
        super().__init__(f"DDG challenge for query: {query}")


# ── Challenge 存储 ───────────────────────────────────────────────────────────
#
# v2 P7 多实例外置：多用户模式下 challenge 数据存 pending_interactions 表
# （kind='ddg_challenge'，TTL 300s），验证码解答请求可落在任意副本；
# 单用户模式不建库，沿用进程内 dict（行为不变）。

_challenge_store: dict[str, dict[str, Any]] = {}
# 验证码提交共享客户端（懒创建，关闭时随引擎一起释放）
_challenge_client: httpx.AsyncClient | None = None


def _db_challenge_available() -> bool:
    """pending_interactions 表是否可用（多用户模式且库已初始化）。"""
    try:
        from backend.core.config import get_config
        from backend.core.multiuser import db

        if not get_config().multi_user.enabled:
            return False
        return db.is_initialized()
    except Exception:  # noqa: BLE001 - 配置未加载等场景回落进程内存储
        return False


def _get_challenge_client() -> httpx.AsyncClient:
    """获取提交 DDG 验证码的共享客户端。"""
    global _challenge_client
    if _challenge_client is None or _challenge_client.is_closed:
        _challenge_client = httpx.AsyncClient(
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
    return _challenge_client


async def close_challenge_client() -> None:
    """释放验证码提交共享客户端。"""
    global _challenge_client
    if _challenge_client is not None:
        await _challenge_client.aclose()
        _challenge_client = None


def _store_challenge(data: dict[str, Any]) -> str:
    """存储 challenge 数据，返回 challenge_id。"""
    cid = uuid.uuid4().hex[:12]
    if _db_challenge_available():
        from backend.core.multiuser import pending_interactions

        # 入库（TTL 与进程内口径一致：5 分钟）；过期由读取路径惰性过滤 +
        # 维护循环清理
        pending_interactions.create(
            cid,
            pending_interactions.KIND_DDG_CHALLENGE,
            data,
            _DD_CHALLENGE_TTL,
        )
        return cid
    _cleanup_challenge_store()
    _challenge_store[cid] = {**data, "created_at": time.monotonic()}
    return cid


def get_challenge(challenge_id: str) -> dict[str, Any] | None:
    """获取 challenge 数据，过期返回 None。"""
    if _db_challenge_available():
        from backend.core.multiuser import pending_interactions

        payload = pending_interactions.get(
            challenge_id, pending_interactions.KIND_DDG_CHALLENGE
        )
        return payload
    entry = _challenge_store.get(challenge_id)
    if entry is None:
        return None
    if time.monotonic() - entry["created_at"] > _DD_CHALLENGE_TTL:
        _challenge_store.pop(challenge_id, None)
        return None
    return entry


def remove_challenge(challenge_id: str) -> None:
    """提交后移除 challenge 数据。"""
    if _db_challenge_available():
        from backend.core.multiuser import pending_interactions

        pending_interactions.delete(
            challenge_id, pending_interactions.KIND_DDG_CHALLENGE
        )
        return
    _challenge_store.pop(challenge_id, None)


def _cleanup_challenge_store() -> None:
    """清理过期 challenge。"""
    now = time.monotonic()
    expired = [
        k
        for k, v in _challenge_store.items()
        if now - v["created_at"] > _DD_CHALLENGE_TTL
    ]
    for k in expired:
        _challenge_store.pop(k, None)


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _urlencode(data: dict[str, str]) -> bytes:
    """将字典编码为 URL-encoded bytes（application/x-www-form-urlencoded）。"""
    return urlencode(data).encode("utf-8")


# ── DDG HTML 引擎 ────────────────────────────────────────────────────────────


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
        # 进程级共享客户端：连接复用，避免每次请求新建（AGENTS.md 客户端生命周期）
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取共享 httpx 异步客户端（懒创建，超时按请求传入以支持热重载）。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
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
        return self._client

    async def close(self) -> None:
        """释放共享客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await close_challenge_client()

    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索。遇到 DDG 验证码时抛出 DDGChallengeError。"""
        if len(query) >= 500:
            return SearchResponse(query=query, engine=self.name, results=[])

        return await self._do_search(self._get_client(), query, max_results)

    async def _do_search(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> SearchResponse:
        """实际搜索逻辑。"""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": _DD_HTML_URL,
        }
        data = {"q": query, "b": ""}

        cfg = get_config()
        timeout = cfg.features.function_calling.web_search.timeout_seconds
        self._logger.info(f"DDG HTML search: {query[:60]}")
        resp = await client.post(
            _DD_HTML_URL,
            headers=headers,
            content=_urlencode(data),
            timeout=timeout,
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

    def _parse_results(
        self, html: str, query: str, max_results: int = 5
    ) -> SearchResponse:
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
            results.append(
                SearchResult(
                    title=title_el.get_text(strip=True),
                    url=url,
                    snippet=snippet_el.get_text(strip=True) if snippet_el else "",
                )
            )

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

        challenge_id = _store_challenge(
            {
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
            }
        )

        return DDGChallengeError(
            challenge_id=challenge_id,
            images=images,
            query=query,
            submission_url=action,
            form_data=form_data,
            engine_name="DuckDuckGo",
            instruction="Select all images containing ducks",
        )

    async def _fetch_challenge_images(
        self, hashes: list[str], client: httpx.AsyncClient
    ) -> list[dict[str, str]]:
        """下载 challenge 图片并编码为 base64。"""
        cfg = get_config()
        timeout = cfg.features.function_calling.web_search.timeout_seconds
        images: list[dict[str, str]] = []
        for h in hashes:
            url = f"{_DD_IMAGE_BASE}/{h}.jpg"
            try:
                resp = await client.get(
                    url,
                    headers={"Referer": _DD_HTML_URL},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    b64 = base64.b64encode(resp.content).decode("ascii")
                    images.append({"hash": h, "base64": b64})
                else:
                    self._logger.warning(
                        f"DDG image fetch failed: {h} status={resp.status_code}"
                    )
            except Exception as e:
                self._logger.warning(f"DDG image fetch error: {h} {e}")
        return images

    async def health_check(self) -> bool:
        """健康检查：尝试访问 DDG HTML 页面。"""
        try:
            resp = await self._get_client().get(_DD_HTML_URL, timeout=5)
            return resp.status_code == 200
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
        timeout = cfg.features.function_calling.web_search.timeout_seconds
        resp = await _get_challenge_client().post(
            submit_url,
            content=body,
            timeout=timeout,
        )

        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"DDG returned HTTP {resp.status_code}",
            }

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
            results.append(
                {
                    "title": title_el.get_text(strip=True),
                    "url": url,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                }
            )

        return {"success": True, "results": results}


# ── 验证码提交模型 ──────────────────────────────────────────────────────────


class DDGChallengeSubmitRequest(BaseModel):
    """提交 DDG 验证码解答。"""

    challenge_id: str
    selected_hashes: list[str]

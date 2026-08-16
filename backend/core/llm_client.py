"""
OpenAI API 统一封装模块。

功能：
    1. 非流式对话（chat）：发送消息、接收 JSON 解析结果，含自动重试 + 指数退避
    2. 流式对话（chat_stream）：逐 token 输出，用于 SSE 推送进度
    3. 请求频率控制（rate limiting），避免触发 API 限流

重试策略：
    - 网络/API 异常 → 重试（最多 3 次，间隔 2^n 秒指数退避）
    - 3 次均失败 → 抛出异常，由上层处理
    - JSON 解析与 schema 校验由 json_utils.retry_with_validation 统一负责
"""

import asyncio
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from backend.core.config import get_config
from backend.core.logger import get_logger


@dataclass
class UsageInfo:
    """单次 LLM 调用的 token 用量。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# ── 上下文窗口探测常量 ────────────────────────────────────────────────────

# 探测/模型表全部失败时的兜底窗口大小
_DEFAULT_CONTEXT_WINDOW = 200000

# 常见模型上下文窗口（前缀匹配，列表内按前缀长度降序排列，
# 避免 "gpt-4.1" 被 "gpt-4" 先命中）
_BUILTIN_MODEL_WINDOWS: list[tuple[str, int]] = [
    ("gemini-3.5-flash-lite", 1_048_576),
    ("gemini-3.1-flash-lite", 1_048_576),
    ("gemini-3-flash-preview", 1_048_576),
    ("qwen3.6-max-preview", 262_144),
    ("qwen3.5-397b-a17b", 262_144),
    ("qwen3.5-122b-a10b", 262_144),
    ("deepseek-v4-flash", 1_000_000),
    ("qwen3-max-preview", 262_144),
    ("deepseek-v4-pro", 1_000_000),
    ("gemini-3.7-flash", 1_048_576),
    ("gemini-3.6-flash", 1_048_576),
    ("gemini-3.5-flash", 1_048_576),
    ("gemini-2.5-flash", 1_048_576),
    ("qwen3.6-35b-a3b", 262_144),
    ("qwen3-235b-a22b", 131_072),
    ("claude-sonnet-5", 1_000_000),
    ("claude-fable-5", 1_000_000),
    ("claude-opus-5", 1_000_000),
    ("gemini-3.1-pro", 1_048_576),
    ("gemini-2.5-pro", 1_048_576),
    ("qwen3.7-flash", 1_000_000),
    ("qwen3.6-flash", 1_000_000),
    ("qwen3.5-flash", 1_000_000),
    ("qwen3.8-max", 1_000_000),
    ("qwen3.7-max", 1_000_000),
    ("qwen3.7-plus", 1_000_000),
    ("qwen3.6-plus", 1_000_000),
    ("qwen3.6-max", 262_144),
    ("qwen3.6-27b", 262_144),
    ("qwen3.5-plus", 1_000_000),
    ("qwen3.5-27b", 262_144),
    ("qwen3-max", 262_144),
    ("qwen3-32b", 131_072),
    ("kimi-k3", 1_048_576),
    ("glm-5.3", 1_000_000),
    ("glm-5.2", 1_000_000),
    ("gpt-5.6", 1_050_000),
    ("gpt-5.5", 1_050_000),
    ("gpt-5.4", 1_050_000),
]

# 超限错误 → 窗口值 的解析正则（vLLM / OpenAI 兼容错误信息）
_WINDOW_ERROR_PATTERNS = [
    re.compile(r"maximum context length is\s+(\d+)", re.IGNORECASE),
    re.compile(r"maximum context length[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"context length[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"context_window[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"too many tokens[^\d]{0,40}(\d{4,})", re.IGNORECASE),
]


def _parse_context_window_value(value: Any) -> int | None:
    """宽松读取 /models 响应中的窗口数值字段。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


@dataclass
class StreamChunk:
    """
    chat_stream_with_usage / chat_stream_with_tools 流式片段。

    content / usage / tool_calls 互斥：
        - 普通流片段：content 为字符串、usage=None、tool_calls=None
        - 末尾用量片段：content=None、usage=UsageInfo（若 API 未返回则为 None）
        - 工具调用片段：content=None、usage=None、tool_calls=[ChatCompletionMessageToolCall, ...]
    """

    content: str | None
    usage: UsageInfo | None
    tool_calls: list | None = None


class LLMClient:
    """
    LLM API 客户端。
    封装 OpenAI AsyncClient，提供统一的重试和限流逻辑。
    所有 LLM 调用（文本校对、表达优化、公式 OCR 等）都通过此模块进行。
    """

    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()
        self._last_request_time: float = 0
        self._rate_limit_lock = asyncio.Lock()
        # 全局并发上限：所有 LLM 调用（含流式）共享同一信号量，
        # 容量来自 config.llm.max_concurrency，热重载时随 reset() 重建。
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_capacity: int = 0
        self._logger = get_logger("llm_client")
        # 上下文窗口缓存：key=(base_url, model)，reset() 时随配置一起失效
        self._context_window_cache: dict[tuple[str, str], int] = {}
        self._window_cache_lock = threading.Lock()

    def reset(self) -> None:
        """重置缓存的 AsyncOpenAI 实例与上下文窗口探测结果，使下次访问重新读取配置。"""
        with self._client_lock:
            self._client = None
        with self._window_cache_lock:
            self._context_window_cache.clear()
        self._semaphore = None
        self._semaphore_capacity = 0

    def _get_semaphore(self) -> asyncio.Semaphore:
        """按当前 max_concurrency 配置获取全局并发信号量（容量变化时重建）。"""
        cap = get_config().llm.max_concurrency
        if self._semaphore is None or self._semaphore_capacity != cap:
            self._semaphore = asyncio.Semaphore(cap)
            self._semaphore_capacity = cap
        return self._semaphore

    # ── 上下文窗口探测 ─────────────────────────────────────────────────

    @staticmethod
    def _same_origin(base_url: str, probe_url: str) -> bool:
        """判断探测地址与主服务是否同源（scheme + host + 端口一致）。"""

        def _origin(url: str) -> tuple[str, str]:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            try:
                port = parsed.port
            except ValueError:
                port = None
            if port is None:
                port = 443 if parsed.scheme == "https" else 80
            return parsed.scheme.lower(), f"{host}:{port}"

        return _origin(base_url) == _origin(probe_url)

    @staticmethod
    def _probe_context_window_sync(
        base_url: str,
        model: str,
        api_key: str,
        probe_url: str,
        timeout: float = 5.0,
    ) -> int | None:
        """
        同步请求 OpenAI 兼容 /models 端点并解析模型上下文窗口。

        优先匹配 id == model（支持 :latest 等后缀），宽松读取：
        context_length / inputTokenLimit / outputTokenLimit / max_model_len /
        context_window / max_context_length。

        仅当探测地址与 base_url 同源时才携带 API key；跨域探测不发送密钥，
        避免主服务密钥泄漏给直连地址（vLLM 通常无需鉴权即可访问 /models）。

        任何失败（网络、鉴权、HTML 页面、字段缺失）都返回 None，绝不抛出。
        """
        base = (probe_url or base_url).rstrip("/")
        url = f"{base}/models"
        try:
            req = urllib.request.Request(url, method="GET")
            if api_key and LLMClient._same_origin(base_url, probe_url or base_url):
                req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", ""))
            if item_id != model and not item_id.startswith(f"{model}:"):
                continue
            for key in (
                "context_length",
                "inputTokenLimit",
                "outputTokenLimit",
                "max_model_len",
                "context_window",
                "max_context_length",
            ):
                window = _parse_context_window_value(item.get(key))
                if window is not None:
                    return window
        return None

    @staticmethod
    def _builtin_context_window(model: str) -> int | None:
        """按内置模型表前缀匹配上下文窗口；未命中返回 None。"""
        for prefix, window in _BUILTIN_MODEL_WINDOWS:
            if model == prefix or model.startswith(prefix + "-"):
                return window
        return None

    def learn_context_window_from_error(self, error_text: str) -> int | None:
        """
        从上下文超限错误信息中解析窗口值并缓存。

        供 vLLM / OpenAI 兼容网关（"This model's maximum context length is
        N tokens"）在探测不可用时学习真实窗口。解析成功返回窗口值，否则 None。
        """
        if not error_text:
            return None
        window = None
        for pattern in _WINDOW_ERROR_PATTERNS:
            m = pattern.search(error_text)
            if m:
                window = int(m.group(1))
                break
        if window is None:
            return None
        cfg = get_config().llm
        key = (cfg.base_url, cfg.model)
        with self._window_cache_lock:
            self._context_window_cache[key] = window
        self._logger.info(
            "Learned context window %s from error (model=%s)", window, cfg.model
        )
        return window

    def _learn_window_from_exception(self, exc: Exception) -> None:
        """异常路径统一尝试从错误文本学习窗口；失败静默。"""
        try:
            self.learn_context_window_from_error(str(exc))
        except Exception:
            pass

    async def get_context_window(self) -> int:
        """
        获取当前模型上下文窗口（token）。

        优先级链：
            1. 显式配置 llm.context_window_tokens（非 0）
            2. 缓存（含错误信息学习结果，按 (base_url, model)）
            3. GET {probe_base}/models 探测（默认 base_url，可用
               llm.models_probe_url 覆盖为直连地址）
            4. 内置模型表
            5. 默认值 200000

        探测失败只降级，绝不抛出；reset()（配置热重载）时缓存失效。
        """
        cfg = get_config().llm
        explicit = cfg.context_window_tokens
        if explicit > 0:
            return explicit

        key = (cfg.base_url, cfg.model)
        with self._window_cache_lock:
            cached = self._context_window_cache.get(key)
        if cached is not None:
            return cached

        window = await asyncio.to_thread(
            self._probe_context_window_sync,
            cfg.base_url,
            cfg.model,
            cfg.api_key,
            cfg.models_probe_url,
        )
        if window is None:
            window = self._builtin_context_window(cfg.model)
        if window is None:
            window = _DEFAULT_CONTEXT_WINDOW

        with self._window_cache_lock:
            self._context_window_cache[key] = window
        self._logger.info(
            "Context window resolved to %s for model=%s (probe_base=%s)",
            window,
            cfg.model,
            cfg.models_probe_url or cfg.base_url,
        )
        return window

    @property
    def client(self) -> AsyncOpenAI:
        """
        获取或创建 AsyncOpenAI 实例（懒加载 + 双检锁）。
        每次创建时实时读取 config，支持运行时热重载 api_key / base_url。
        """
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    cfg = get_config().llm
                    self._client = AsyncOpenAI(
                        api_key=cfg.api_key,
                        base_url=cfg.base_url,
                    )
        return self._client

    async def _rate_limit(self) -> None:
        """
        请求频率控制。
        确保两次请求之间的时间间隔不小于 config.llm.request_interval。
        如果上次请求距今不足间隔，则等待到足够时间后再放行。
        """
        async with self._rate_limit_lock:
            cfg = get_config().llm
            elapsed = time.time() - self._last_request_time
            if elapsed < cfg.request_interval:
                await asyncio.sleep(cfg.request_interval - elapsed)
            self._last_request_time = time.time()

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> str:
        """
        非流式 LLM 对话调用，返回原始文本。

        参数：
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            response_format: OpenAI response_format 参数，如 {"type": "json_object"}
            max_retries: 最大重试次数（默认 3）
            model: 覆盖使用的模型名（默认跟随 config.llm.model）

        返回：
            LLM 返回的原始文本（str），由调用方负责 JSON 解析和校验

        异常：
            重试耗尽后抛出最后一次异常的原始类型
        """
        content, _ = await self.chat_with_usage(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            max_retries=max_retries,
            model=model,
        )
        return content

    async def chat_with_usage(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> tuple[str, UsageInfo | None]:
        """非流式 LLM 对话调用，返回 (文本, usage)。受全局并发上限约束。"""
        async with self._get_semaphore():
            return await self._chat_with_usage_impl(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                max_retries=max_retries,
                model=model,
            )

    async def _chat_with_usage_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> tuple[str, UsageInfo | None]:
        """
        非流式 LLM 对话调用，返回 (文本, usage)。

        usage 为 None 表示 API 未返回用量信息（调用方降级估算）；
        其余行为与 chat() 一致。
        """
        cfg = get_config().llm
        model_name = model or cfg.model
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        for attempt in range(max_retries):
            try:
                await self._rate_limit()

                self._logger.info(
                    f"LLM request | model={model_name} | tokens={max_tok} "
                    f"| attempt={attempt + 1}"
                )

                kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                }
                if response_format:
                    kwargs["response_format"] = response_format

                response = await self.client.chat.completions.create(**kwargs)

                content = response.choices[0].message.content
                usage = response.usage
                usage_info: UsageInfo | None = None
                if usage:
                    usage_info = UsageInfo(
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens,
                    )
                    self._logger.info(
                        f"LLM response | prompt_tokens={usage.prompt_tokens} "
                        f"completion_tokens={usage.completion_tokens} "
                        f"total_tokens={usage.total_tokens}"
                    )

                return content, usage_info

            except Exception as e:
                self._learn_window_from_exception(e)
                # 其他异常（网络错误、API 错误、认证失败等）
                self._logger.error(
                    f"LLM request failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """流式 LLM 对话调用（Server-Sent Events）。受全局并发上限约束。"""
        async with self._get_semaphore():
            async for chunk in self._chat_stream_impl(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    async def _chat_stream_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """
        流式 LLM 对话调用（Server-Sent Events）。

        用于前端实时显示 LLM 生成进度，逐 token 返回字符串。
        每个 yield 返回一个 token 片段，由调用方自行拼接。

        参数：
            messages: OpenAI 格式的消息列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个文本片段（string），逐 token 流式返回
        """
        cfg = get_config().llm
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(f"LLM stream request | model={cfg.model} | tokens={max_tok}")

        stream = await self.client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=temp,
            max_tokens=max_tok,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
    ) -> ChatCompletion:
        """带 Function Calling 的非流式 LLM 调用。受全局并发上限约束。"""
        async with self._get_semaphore():
            return await self._chat_with_tools_impl(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )

    async def _chat_with_tools_impl(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
    ) -> ChatCompletion:
        """
        带 Function Calling 的非流式 LLM 调用。

        返回完整的 ChatCompletion 对象（含 tool_calls），
        由调用方决定如何处理 tool_call 循环。

        参数：
            messages: OpenAI 格式的消息列表
            tools: Function Calling 工具定义列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            max_retries: 最大重试次数（默认 3）

        返回：
            openai.ChatCompletion 对象

        异常：
            重试耗尽后抛出最后一次异常的原始类型
        """
        cfg = get_config().llm
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        for attempt in range(max_retries):
            try:
                await self._rate_limit()

                self._logger.info(
                    f"LLM request (with tools) | model={cfg.model} | "
                    f"tools={len(tools) if tools else 0} | attempt={attempt + 1}"
                )

                kwargs: dict[str, Any] = {
                    "model": cfg.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                }
                if tools:
                    kwargs["tools"] = tools

                response = await self.client.chat.completions.create(**kwargs)

                usage = response.usage
                if usage:
                    self._logger.info(
                        f"LLM response (with tools) | prompt_tokens={usage.prompt_tokens} "
                        f"completion_tokens={usage.completion_tokens} "
                        f"total_tokens={usage.total_tokens}"
                    )

                return response

            except Exception as e:
                self._learn_window_from_exception(e)
                self._logger.error(
                    f"LLM request with tools failed "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream_with_usage(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """流式 LLM 对话调用，并在流末尾附带 token 用量。受全局并发上限约束。"""
        async with self._get_semaphore():
            async for chunk in self._chat_stream_with_usage_impl(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    async def _chat_stream_with_usage_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """
        流式 LLM 对话调用，并在流的末尾附带 token 用量信息。

        与 chat_stream 的区别：
            - 每个 yield 是 StreamChunk（content 或 usage 二者之一）
            - 末尾会单独 yield 一个 StreamChunk(content=None, usage=Usage)
              用于上层累计 token 计数。

        使用 stream_options={"include_usage": True} 让 OpenAI 兼容 API
        在流末尾推一个仅含 usage 的 chunk。若第三方代理不支持该参数，
        会捕获异常后降级为 usage=None。

        参数：
            messages: OpenAI 格式的消息列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个 StreamChunk 实例。
        """
        cfg = get_config().llm
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(
            f"LLM stream-with-usage request | model={cfg.model} | tokens={max_tok}"
        )

        kwargs = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
        }
        try:
            stream = await self.client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True}
            )
        except Exception as e:
            self._logger.warning(f"stream_options unsupported, fallback: {e}")
            stream = await self.client.chat.completions.create(**kwargs)

        async for chunk in stream:
            content = None
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
            usage = getattr(chunk, "usage", None)
            # 仅在末次 chunk 会有 usage，且此时通常 content 为 None
            if content is not None:
                yield StreamChunk(content=content, usage=None)
            if usage is not None:
                yield StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                )

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """流式 LLM 对话调用，支持 Function Calling 工具检测。受全局并发上限约束。"""
        async with self._get_semaphore():
            async for chunk in self._chat_stream_with_tools_impl(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    async def _chat_stream_with_tools_impl(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """
        流式 LLM 对话调用，支持 Function Calling 工具检测。

        与 chat_stream_with_usage 的区别：
            - 传入 tools 参数用于 LLM 工具调用
            - 流中同时产出 content（文本）和 tool_calls（工具调用）
            - 文本 token 一到就 yield，前端可立即显示
            - 工具调用在流末尾累积完整后，以一条 tool_calls 片段 yield

        参数：
            messages: OpenAI 格式的消息列表
            tools: OpenAI 格式的 Function Calling 工具定义列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个 StreamChunk 实例。
        """
        cfg = get_config().llm
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(
            f"LLM stream-with-tools request | model={cfg.model} | tokens={max_tok} | tools={len(tools)}"
        )

        kwargs = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
            "tools": tools,
        }
        try:
            stream = await self.client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True}
            )
        except Exception as e:
            self._logger.warning(f"stream_options unsupported, fallback: {e}")
            stream = await self.client.chat.completions.create(**kwargs)

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            content = None
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content

            if chunk.choices and chunk.choices[0].delta.tool_calls:
                for tc in chunk.choices[0].delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"][
                                "arguments"
                            ] += tc.function.arguments

            usage = getattr(chunk, "usage", None)
            if content is not None:
                yield StreamChunk(content=content, usage=None)
            if usage is not None:
                yield StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                )

        if tool_calls_acc:
            from openai.types.chat.chat_completion_message_tool_call import (
                ChatCompletionMessageToolCall,
                Function,
            )

            collected: list[ChatCompletionMessageToolCall] = []
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                collected.append(
                    ChatCompletionMessageToolCall(
                        id=tc["id"],
                        function=Function(
                            name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"],
                        ),
                        type="function",
                    )
                )
            yield StreamChunk(content=None, usage=None, tool_calls=collected)


# 全局单例：LLM 客户端实例
_llm_client: LLMClient | None = None
_llm_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """
    获取全局唯一的 LLMClient 实例（单例模式）。
    """
    global _llm_client
    if _llm_client is None:
        with _llm_client_lock:
            if _llm_client is None:
                _llm_client = LLMClient()
    return _llm_client

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
import threading
import time
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


@dataclass
class StreamChunk:
    """
    chat_stream_with_usage 流式片段。

    content 与 usage 互斥：
        - 普通流片段：content 为字符串、usage=None
        - 末尾用量片段：content=None、usage=UsageInfo（若 API 未返回则为 None）
    """
    content: str | None
    usage: UsageInfo | None


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
        self._logger = get_logger("llm_client")

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
    ) -> str:
        """
        非流式 LLM 对话调用，返回原始文本。

        参数：
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            response_format: OpenAI response_format 参数，如 {"type": "json_object"}
            max_retries: 最大重试次数（默认 3）

        返回：
            LLM 返回的原始文本（str），由调用方负责 JSON 解析和校验

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
                    f"LLM request | model={cfg.model} | tokens={max_tok} | attempt={attempt + 1}"
                )

                kwargs = {
                    "model": cfg.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                }
                if response_format:
                    kwargs["response_format"] = response_format

                response = await self.client.chat.completions.create(**kwargs)

                content = response.choices[0].message.content
                usage = response.usage
                if usage:
                    self._logger.info(
                        f"LLM response | prompt_tokens={usage.prompt_tokens} "
                        f"completion_tokens={usage.completion_tokens} "
                        f"total_tokens={usage.total_tokens}"
                    )

                return content

            except Exception as e:
                # 其他异常（网络错误、API 错误、认证失败等）
                self._logger.error(
                    f"LLM request failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream(
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

        self._logger.info(
            f"LLM stream request | model={cfg.model} | tokens={max_tok}")

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
                self._logger.error(
                    f"LLM request with tools failed "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream_with_usage(
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
            f"LLM stream-with-usage request | model={cfg.model} | tokens={max_tok}")

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
            self._logger.warning(
                f"stream_options unsupported, fallback: {e}"
            )
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

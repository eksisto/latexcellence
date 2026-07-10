"""
OpenAI API 统一封装模块。

功能：
    1. 非流式对话（chat）：发送消息、接收 JSON 解析结果，含自动重试 + 指数退避
    2. 流式对话（chat_stream）：逐 token 输出，用于 SSE 推送进度
    3. 请求频率控制（rate limiting），避免触发 API 限流

重试策略：
    - JSON 解析失败 → 重试（最多 3 次，间隔 2^n 秒指数退避）
    - 网络/API 异常 → 重试（最多 3 次，间隔 2^n 秒指数退避）
    - 3 次均失败 → 抛出异常，由上层处理
"""

import asyncio
import json
import time
from typing import Optional, AsyncIterator

from openai import AsyncOpenAI

from backend.core.config import get_config
from backend.core.logger import get_logger


class LLMClient:
    """
    LLM API 客户端。
    封装 OpenAI AsyncClient，提供统一的重试和限流逻辑。
    所有 LLM 调用（文本校对、表达优化、公式 OCR 等）都通过此模块进行。
    """

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None       # OpenAI 客户端（懒加载）
        self._last_request_time: float = 0                # 上次请求的时间戳，用于限流
        self._logger = get_logger("llm_client")

    @property
    def client(self) -> AsyncOpenAI:
        """
        获取或创建 AsyncOpenAI 实例（懒加载单例）。
        使用 config 中的 api_key 和 base_url 初始化。
        """
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
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        max_retries: int = 3,
    ) -> dict:
        """
        非流式 LLM 对话调用。

        参数：
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            response_format: OpenAI response_format 参数，如 {"type": "json_object"}
            max_retries: 最大重试次数（默认 3）

        返回：
            LLM 返回的 JSON 解析结果（dict）

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

                # 强制 JSON 输出模式下，将字符串解析为 Python dict
                return json.loads(content)

            except json.JSONDecodeError as e:
                # JSON 解析失败：LLM 输出不符合 JSON 格式
                self._logger.warning(
                    f"JSON parse failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                # 指数退避：第一次重试前等待 2^0 = 1 秒，第二次 2^1 = 2 秒，第三次 2^2 = 4 秒
                await asyncio.sleep(2 ** attempt)

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
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
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


# 全局单例：LLM 客户端实例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """
    获取全局唯一的 LLMClient 实例（单例模式）。
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client

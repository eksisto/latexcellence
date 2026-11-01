"""OCR 分隔符协议的「识别-校验-回喂重试」共享循环。

formula_ocr / table_ocr 的差异只在协议解析与提示语，重试骨架一致：

    - 首次调用 → parse() 成功即返回；
    - ValueError / ValidationError → 记录错误，把错误回喂给模型重试；
    - 其他异常 → 记日志后立即 RuntimeError 上抛（不重试）；
    - 重试耗尽 → RuntimeError（带最后一次错误）。

整个循环按 cache_key 经 llm_cache.cached_call 缓存：同一图片重复识别命中
缓存，不再产生识别调用。缓存值与「模型原始识别结果」同构，格式化 / 包裹 /
宏包注释等后处理由调用方负责（见 llm_cache 的值隔离说明）。
"""

import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.utils.json_utils import format_validation_error
from backend.utils.llm_cache import CacheableLLM, cached_call


async def cached_protocol_loop(
    llm: CacheableLLM,
    messages: list[dict],
    *,
    cache_key: str,
    logger: logging.Logger,
    label: str,
    retry_hint: str,
    max_tokens: int,
    parse: Callable[[str], dict[str, Any]],
    max_attempts: int,
) -> dict[str, Any]:
    """按分隔符协议调用视觉模型，解析失败时回喂错误重试，成功结果缓存。

    参数：
        llm: LLM 客户端（提供 chat）
        messages: 首次请求消息
        cache_key: 缓存键，用 build_cache_key(client_cache_identity(llm), ...) 生成
        logger: 调用方 logger，保证日志归属与原实现一致
        label: 错误信息前缀（Formula / Table）
        retry_hint: 回喂消息中的输出格式提示
        max_tokens: 单次生成上限
        parse: 解析并校验一次响应；协议不符时抛 ValueError / ValidationError
        max_attempts: 最大尝试次数（含首次）
    """

    async def _run() -> dict[str, Any]:
        current = messages
        last_error: str | None = None
        for attempt in range(max_attempts):
            try:
                response_text = await llm.chat(
                    current,
                    temperature=0.1,
                    max_tokens=max_tokens,
                )
                return parse(response_text)
            except ValidationError as e:
                last_error = format_validation_error(e.errors())
            except ValueError as e:
                last_error = str(e)
            except Exception as e:
                logger.error(f"{label} OCR error: {e}")
                raise RuntimeError(f"{label} recognition failed: {e}") from e

            logger.warning(
                f"{label} OCR protocol/validation failed on attempt "
                f"{attempt + 1}/{max_attempts}: {last_error}"
            )
            current = current + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed the output protocol. "
                        f"Error: {last_error}\n\n"
                        f"{retry_hint}"
                    ),
                }
            ]

        raise RuntimeError(
            f"{label} recognition failed after {max_attempts} attempts. "
            f"Last error: {last_error}"
        )

    return await cached_call(cache_key, _run)

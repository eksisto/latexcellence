"""
LLM JSON 解析/校验/重试模块。

职责：
    1. 安全解析 LLM 返回的 JSON 字符串（处理常见格式问题）
    2. 使用 Pydantic 模型校验 JSON 结构是否符合预期 schema
    3. 校验失败时构造错误信息供 LLM 重试使用（最多 3 次）

设计理念：
    response_format={"type": "json_object"} 只保证输出是合法 JSON，
    不保证字段符合预期 schema。因此必须在收到回复后执行 Pydantic 校验。
"""

import json
import re
from typing import Any, Callable, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from backend.core.logger import get_logger

T = TypeVar("T", bound=BaseModel)


def safe_parse_json(text: str) -> dict[str, Any]:
    """
    安全解析 LLM 输出的 JSON 文本。

    处理以下常见问题：
        - 文本中包含 Markdown 代码块标记（```json ... ```）
        - 前后有多余的空白/换行
        - 数字末尾有逗号（trailing commas）
        - 值中使用单引号

    参数：
        text: LLM 返回的原始文本

    返回：
        解析后的 dict

    异常：
        ValueError: JSON 解析失败
    """
    if isinstance(text, dict):
        return text

    if not text or not text.strip():
        raise ValueError("Empty response text")

    original = text.strip()

    # 1. 移除 Markdown 代码块标记
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", original, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()
    else:
        text = original

    # 2. 尝试提取第一个完整 JSON 对象
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start : brace_end + 1]

    # 3. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 4. 修复常见问题后重试
    # 移除 trailing commas（对象和数组中的逗号后跟结束符号）
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 替换单引号为双引号（仅替换 JSON 键/值中的单引号）
    # 注意：不在字符串内容中错误替换
    text = text.replace("'", '"')

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse JSON after cleanup. Original error: {e}\n"
            f"Processed text (first 500 chars): {text[:500]}"
        )


def validate_model(data: dict[str, Any], model_cls: Type[T]) -> T:
    """
    使用 Pydantic 模型校验 JSON 数据。

    参数：
        data: 已解析的 JSON dict
        model_cls: Pydantic BaseModel 子类

    返回：
        校验通过后的模型实例

    异常：
        ValidationError: 校验失败
    """
    return model_cls.model_validate(data)


def format_validation_error(errors: list[dict[str, Any]]) -> str:
    """
    将 Pydantic 校验错误格式化为 LLM 可读的错误提示。

    参数：
        errors: Pydantic ValidationError.errors() 返回的列表

    返回：
        格式化的错误描述字符串
    """
    lines = ["JSON validation failed. Please fix the following issues:"]
    for err in errors:
        loc = " -> ".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "Unknown error")
        error_type = err.get("type", "unknown")
        lines.append(f"  Field '{loc}': {msg} (type: {error_type})")
    return "\n".join(lines)


def parse_and_validate(
    text: Union[str, dict[str, Any]],
    model_cls: Type[T],
    raise_on_error: bool = True,
) -> tuple[Optional[T], Optional[str]]:
    """
    一步完成 JSON 解析 + Pydantic 校验。

    参数：
        text: LLM 返回的原始文本（str）或已解析的 dict
        model_cls: Pydantic BaseModel 子类
        raise_on_error: 是否在校验失败时抛出异常

    返回：
        (模型实例, 错误消息) 元组。成功时错误消息为 None。

    异常：
        ValueError: JSON 解析失败（raise_on_error=True 时）
        ValidationError: Pydantic 校验失败（raise_on_error=True 时）
    """
    try:
        if isinstance(text, dict):
            data = text
        else:
            data = safe_parse_json(text)
    except ValueError as e:
        if raise_on_error:
            raise
        return None, str(e)

    try:
        model = validate_model(data, model_cls)
        return model, None
    except ValidationError as e:
        error_msg = format_validation_error(e.errors())
        if raise_on_error:
            raise
        return None, error_msg


async def retry_with_validation(
    chat_fn: Callable,
    messages: list[dict[str, Any]],
    model_cls: Type[T],
    max_retries: int = 3,
    logger_name: str = "json_utils",
    **chat_kwargs,
) -> T:
    """
    带 Pydantic 校验的 LLM 调用重试循环。

    流程：
        1. 调用 LLM（通过 chat_fn）
        2. 解析返回的 JSON
        3. 用 Pydantic 校验
        4. 校验失败时，将错误信息附加到消息中，重新调用 LLM
        5. 最多重试 max_retries 次

    参数：
        chat_fn: LLM 调用函数（签名: (messages, **kwargs) -> str）
        messages: LLM 消息列表
        model_cls: Pydantic 模型类
        max_retries: 最大重试次数（包括首次调用）
        logger_name: 日志记录器名称
        **chat_kwargs: 传递给 chat_fn 的额外参数

    返回：
        校验通过的 Pydantic 模型实例

    异常：
        ValueError: 超过最大重试次数仍失败
    """
    logger = get_logger(logger_name)
    current_messages = list(messages)  # 不修改原始消息

    last_error = None
    for attempt in range(max_retries):
        try:
            response_text = await chat_fn(current_messages, **chat_kwargs)
            result, error = parse_and_validate(response_text, model_cls, raise_on_error=False)
            if error is None:
                logger.debug(
                    f"Validation succeeded on attempt {attempt + 1}/{max_retries}"
                )
                return result

            last_error = error
            logger.warning(
                f"Validation failed on attempt {attempt + 1}/{max_retries}: {error[:200]}"
            )

            # 将错误信息反馈给 LLM 进行修正
            correction_prompt = {
                "role": "user",
                "content": (
                    f"Your previous response failed validation. "
                    f"Please fix the JSON to match the required schema.\n\n"
                    f"Errors:\n{error}\n\n"
                    f"Please respond with corrected JSON only."
                ),
            }
            current_messages = current_messages + [correction_prompt]

        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed: {last_error[:200]}"
            )
            if attempt < max_retries - 1:
                continue
            raise

    raise ValueError(
        f"Validation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )

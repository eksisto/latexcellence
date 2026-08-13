"""视觉评审工具的 OpenAI Responses API 请求适配。"""

from typing import Any


def convert_messages_to_responses(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """将 Chat Completions 消息转换为 Responses API 输入。"""
    system_parts: list[str] = []
    input_messages: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str):
            converted_content = [{"type": "input_text", "text": content}]
        elif isinstance(content, list):
            converted_content = []
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("Unsupported Responses message content")
                part_type = part.get("type")
                if part_type in {"text", "input_text"}:
                    text = part.get("text")
                    if not isinstance(text, str):
                        raise ValueError("Text content must be a string")
                    converted_content.append({"type": "input_text", "text": text})
                elif part_type == "image_url":
                    image_url = part.get("image_url")
                    detail = None
                    if isinstance(image_url, dict):
                        detail = image_url.get("detail")
                        image_url = image_url.get("url")
                    if not isinstance(image_url, str) or not image_url:
                        raise ValueError("Image content must include an image URL")
                    converted_image = {
                        "type": "input_image",
                        "image_url": image_url,
                    }
                    if detail is not None:
                        converted_image["detail"] = detail
                    converted_content.append(converted_image)
                elif part_type == "input_image":
                    image_url = part.get("image_url")
                    if not isinstance(image_url, str) or not image_url:
                        raise ValueError("Image content must include an image URL")
                    converted_image = {
                        "type": "input_image",
                        "image_url": image_url,
                    }
                    if part.get("detail") is not None:
                        converted_image["detail"] = part["detail"]
                    converted_content.append(converted_image)
                else:
                    raise ValueError(f"Unsupported message content type: {part_type}")
        else:
            raise ValueError("Message content must be text or a content list")

        if role == "system":
            if any(part["type"] != "input_text" for part in converted_content):
                raise ValueError("System messages must contain text only")
            system_parts.extend(part["text"] for part in converted_content)
        else:
            input_messages.append({"role": role, "content": converted_content})

    instructions = "\n\n".join(system_parts) or None
    return instructions, input_messages


def build_responses_request(
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 Responses API 的非流式评审请求参数。"""
    instructions, input_messages = convert_messages_to_responses(messages)
    request: dict[str, Any] = {
        "model": model,
        "input": input_messages,
    }
    if instructions is not None:
        request["instructions"] = instructions
    if temperature is not None:
        request["temperature"] = temperature
    if max_tokens is not None:
        request["max_output_tokens"] = max_tokens
    # 部分 Responses 兼容的视觉端点不接受 input_image 与旧 JSON mode
    # （text.format={type:json_object}）同时出现，会直接返回 400。视觉评审
    # 的 system prompt 已要求 JSON，调用方还会用 retry_with_validation 做
    # Pydantic 校验，因此对含图像的请求省略这个可选参数；纯文本请求仍保留
    # 结构化输出约束。
    has_image = any(
        part.get("type") == "input_image"
        for message in input_messages
        for part in message.get("content", [])
        if isinstance(part, dict)
    )
    if response_format and not has_image:
        request["text"] = {"format": response_format}
    return request

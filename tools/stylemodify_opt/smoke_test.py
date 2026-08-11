"""LLM 端点 + Function Calling 冒烟测试（style_modify 优化闭环前置检查）。"""

import asyncio
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TOOLS_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TOOLS_ROOT))

from backend.core.config import load_config  # noqa: E402

load_config("config.json")

from backend.core.llm_client import get_llm_client  # noqa: E402


async def main() -> None:
    llm = get_llm_client()
    print("--- plain chat ---")
    text = await llm.chat(
        [{"role": "user", "content": "Reply with exactly: OK"}],
        temperature=0.1,
        max_tokens=20,
    )
    print("chat reply:", text)

    print("--- chat_with_tools ---")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Test tool",
                "parameters": {
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
            },
        }
    ]
    resp = await llm.chat_with_tools(
        [
            {
                "role": "user",
                "content": "Call the ping tool with msg=hello and nothing else.",
            }
        ],
        tools=tools,
        temperature=0.1,
        max_tokens=200,
    )
    msg = resp.choices[0].message
    print("content:", msg.content)
    print("tool_calls:", msg.tool_calls)


if __name__ == "__main__":
    asyncio.run(main())

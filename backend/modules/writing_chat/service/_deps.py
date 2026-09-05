"""writing_chat service 内部共享依赖（便于测试统一 patch）。

LLM 客户端经模型路由器按 writing_chat 槽位实时解析
（multi-model §6）；保持 get_llm_client 函数名不变，
使既有测试的 patch 点与本模块消费方（edits / editor_chat /
conversation / compaction 的 `_deps.get_llm_client()`）无需改动。
"""

from backend.core.llm_client import LLMClient
from backend.core.model_router import get_client_for

__all__ = ["get_llm_client"]


def get_llm_client() -> LLMClient:
    """获取 writing_chat 槽位的 LLM 客户端（每次调用实时解析，不缓存）。"""
    return get_client_for("writing_chat")

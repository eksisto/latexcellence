"""writing_chat service 内部共享依赖（便于测试统一 patch）。"""

from backend.core.llm_client import get_llm_client

__all__ = ["get_llm_client"]

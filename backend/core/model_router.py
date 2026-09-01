"""
模型路由器：按任务槽位（role）路由到模型档案的 LLM 客户端池。

multi-model-plan §5.1（P2 交付）。职责：
    1. get_client_for(role)：按槽位两级解析（config.LLMConfig.resolve_role）
       得到目标档案，从客户端池 {profile_id: LLMClient} 取实例；
    2. 池复用：同档案全局共享同一 LLMClient（各自持有独立 AsyncOpenAI、
       Semaphore 与请求间隔状态，节流按档案隔离）；
    3. reset_client_pool()：llm 配置变更时清空整个池（config.
       update_config_from_dict 的热重载接线点）。

失效双保险：主路径为热重载清池；兜底为池中客户端在每次取用底层
AsyncOpenAI 时按档案指纹（base_url / api_key / model）惰性校验，指纹变化
自动重建（见 LLMClient.client）。

并发语义：与既有 get_llm_client 单例一致，采用"双检锁 + 惰性创建"的简单
模型（正确性优先）；清池后新请求即取新配置构建的实例，进行中请求在旧
实例引用上收尾（注意：reset 会关闭旧实例底层连接，恰好在途的请求可能
因传输层关闭而失败，非流式路径由内置重试在新实例上自动恢复，见
reset_client_pool 注释）。
"""

import threading
from typing import Any

from backend.core.config import LLM_ROLE_KEYS, ModelProfile, get_config
from backend.core.llm_client import LLMClient

# 客户端池：profile_id → LLMClient（惰性创建，llm 配置变更时整体清空）
_client_pool: dict[str, LLMClient] = {}
_pool_lock = threading.Lock()


def get_client_for(role: str) -> LLMClient:
    """按任务槽位获取 LLM 客户端（multi-model-plan §5.1 统一入口）。

    槽位两级解析：assignments[role] → assignments["default"] → 内置默认档案
    （顶层字段合成）。未知槽位抛 ValueError（支持的槽位见
    config.LLM_ROLE_KEYS）。每次调用实时解析配置，不做任何缓存。
    """
    profile = get_config().llm.resolve_role(role)
    return _client_for_profile(profile)


def _client_for_profile(profile: ModelProfile) -> LLMClient:
    """按档案 id 从池中取客户端（惰性创建 + 双检锁）。"""
    client = _client_pool.get(profile.id)
    if client is not None:
        return client
    with _pool_lock:
        client = _client_pool.get(profile.id)
        if client is None:
            client = LLMClient(profile_id=profile.id)
            _client_pool[profile.id] = client
        return client


def resolve_role_routes() -> list[dict[str, Any]]:
    """解析全部角色槽位当前生效的档案信息（GET /api/llm/routes 数据源）。

    固定覆盖 LLM_ROLE_KEYS 全部槽位，每项为该角色经两级解析（指派槽 →
    default 槽 → 内置档案）得到的展示信息，不含 api_key 等敏感字段；
    每次调用实时读取配置解析，不做任何缓存（与 get_client_for 相同的
    热重载语义）。
    """
    llm_cfg = get_config().llm
    routes: list[dict[str, Any]] = []
    for role in LLM_ROLE_KEYS:
        profile = llm_cfg.resolve_role(role)
        routes.append(
            {
                "role": role,
                "profile_id": profile.id,
                "profile_name": profile.name,
                "model": profile.model,
                "capabilities": list(profile.capabilities),
            }
        )
    return routes


def reset_client_pool() -> None:
    """清空整个客户端池（llm 配置变更时的热重载接线点）。

    旧实例经 reset() 释放内部 AsyncOpenAI / Semaphore / 探测缓存；池条目
    立即失效，新请求即取新配置构建的实例。注意：reset 会关闭旧实例底层
    连接，恰好在途的旧实例请求可能因传输层关闭而中断——非流式路径由内置
    重试在新实例上自动恢复，流式路径该次调用报错。热重载为低频用户操作，
    该权衡可接受；如需请求级无损排空属于跨阶段改造，此处从简。
    """
    with _pool_lock:
        clients = list(_client_pool.values())
        _client_pool.clear()
    for client in clients:
        client.reset()

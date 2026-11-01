"""
LLM 响应缓存：确定性调用的结果复用（降低 API 使用成本）。

适用对象是「纯函数式」的 LLM 调用——给定 (模型, 消息, 采样参数) 必然
期望相同结果，且结果只依赖输入文本本身：

    - 逐段检查族（typo / expression / translate / code_review）：段落文本
      未变时重查（修完个别段落后整篇复查是高频路径）不再产生任何 API 调用；
    - 术语抽取 / 聚类：同文档重跑直接命中；
    - 编译错误翻译：同一错误反复编译不重复计费；
    - bib_manager 格式识别 / 元数据抽取：同一条目重复解析免费；
    - OCR（formula / table）：同一图片重新识别免费；
    - LaTeX 宏包分析兜底：同一段代码（OCR 后处理、格式转换重跑）直接命中。

不适用（禁止挂接）的对象：

    - writing_chat / editor 助手等多轮对话——上下文含时间与用户意图，
      缓存会返回过期回复；
    - Agent 循环（template_gen / plot_gen 等）——工具副作用使输入看似
      相同实则语义不同。

缓存键 = sha256(模型名 + 完整 messages + 采样参数)。段落文本、语言、
自定义指令、提示词模板版本任一变化都会改变 key，因此无需 TTL；模型
切换经 reset_client_pool()（配置热重载接线点）整体清空。多用户语义：
key 含完整输入哈希，命中意味着另一用户提交了逐字节相同的输入，结果
是输入的确定性函数，不泄露任何额外信息。

并发语义：同 key 的并发未命中请求（批检查中的重复段落）经 in-flight
锁合流为一次真实调用，共享结果；调用失败不缓存，重试路径行为不变。

值隔离：写入时存深拷贝快照、读取时返回深拷贝副本，缓存内部对象永远不
交给调用方。调用方拿到结果后普遍会就地加工（OCR 的格式化 / 包裹 / 宏包
注释前置都是 `result["latex"] = ...`），共享同一对象会把「后处理过的值」
写回缓存，下次命中再加工一次——表现为重复包裹的数学环境、重复的宏包
注释。缓存边界负责切断别名，消费方无需逐处防御。
"""

import asyncio
import copy
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.utils.json_utils import retry_with_validation

# 缓存条目上限（LRU 淘汰）。条目为段落级 JSON（典型几百字节～几 KB），
# 1024 条峰值内存仅数 MB；单篇文档逐段检查通常远小于该规模。
_CACHE_MAX_ENTRIES = 1024

_cache: OrderedDict[str, Any] = OrderedDict()
_cache_lock = threading.Lock()

# 同 key 并发合流：key → asyncio.Lock。事件循环内创建，跨请求复用；
# 释放且无等待者时移除，防止字典无界增长。
_inflight_locks: dict[str, asyncio.Lock] = {}
_inflight_guard = threading.Lock()

_logger = get_logger("llm_cache")


def build_cache_key(model: str, messages: list[dict], **params: Any) -> str:
    """构造缓存键：模型 + 完整消息 + 采样参数的稳定哈希。

    messages / params 经 json 序列化（sort_keys 保证字段顺序无关），
    非可序列化对象由 default=str 兜底（测试中 llm 为 MagicMock 时
    resolved_model 属性同理稳定）。
    """
    payload = json.dumps(
        {"model": model, "messages": messages, "params": params},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_get(key: str) -> Any | None:
    """读取缓存条目（LRU 触碰）；未命中返回 None。

    返回深拷贝副本：调用方就地改写返回值不会污染缓存条目（见模块
    「值隔离」说明）。条目为段落级 JSON / 识别结果 dict，复制成本可忽略。
    """
    with _cache_lock:
        value = _cache.get(key)
        if value is None:
            return None
        _cache.move_to_end(key)
    return copy.deepcopy(value)


def cache_put(key: str, value: Any) -> None:
    """写入缓存条目，超限时淘汰最旧条目。

    存深拷贝快照：调用方随后改写自己持有的对象不会改变缓存内容。
    """
    snapshot = copy.deepcopy(value)
    with _cache_lock:
        _cache[key] = snapshot
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_llm_cache() -> None:
    """清空缓存（配置热重载 / 测试隔离）。"""
    with _cache_lock:
        _cache.clear()


async def cached_call(key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
    """按 key 取结果：先查缓存，未命中则真实调用并缓存，并发合流。

    fetch 抛出异常时不缓存、不合流结果，异常原样上抛（调用方重试 /
    回退路径行为与无缓存时一致）。

    返回值始终是调用方私有的：命中时是缓存条目的深拷贝副本，未命中时
    是 fetch 的返回值本身（缓存已另存快照）。调用方可以就地改写。
    """
    cached = cache_get(key)
    if cached is not None:
        return cached

    with _inflight_guard:
        lock = _inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _inflight_locks[key] = lock
    try:
        async with lock:
            # 双检：排队期间先到者可能已填充缓存
            cached = cache_get(key)
            if cached is not None:
                return cached
            value = await fetch()
            cache_put(key, value)
            return value
    finally:
        _release_inflight_lock(key, lock)


def _release_inflight_lock(key: str, lock: asyncio.Lock) -> None:
    """锁不再被持有时移除登记，避免字典无界增长。

    竞态（另一协程刚好要取该锁）的后果只是双方各建一把锁、各自调用
    一次——退化为无合流的普通缓存行为，正确性不受影响。
    """
    if not lock.locked():
        with _inflight_guard:
            if _inflight_locks.get(key) is lock:
                del _inflight_locks[key]


async def cached_llm_validate(
    llm: Any,
    *,
    messages: list[dict],
    model_cls: type[BaseModel],
    max_retries: int = 3,
    logger_name: str = "llm_cache",
    **chat_kwargs: Any,
) -> BaseModel:
    """retry_with_validation 的缓存版（参数语义完全一致）。

    命中缓存时用 model_cls 重新校验一次（防 schema 漂移，成本可忽略）；
    未命中时走原 retry_with_validation 全流程，成功后缓存
    model_dump(mode="json")。校验失败的重试在 retry_with_validation
    内部进行，只有最终成功的结果进入缓存。
    """
    model = str(getattr(llm, "resolved_model", "") or "")
    key = build_cache_key(model, messages, **chat_kwargs)

    cached = cache_get(key)
    if cached is not None:
        try:
            validated = model_cls.model_validate(cached)
            _logger.info("LLM cache hit | key=%s… | module=%s", key[:12], logger_name)
            return validated
        except Exception:
            # 模型 schema 变化导致旧条目失效，按未命中处理并覆盖
            _logger.warning("LLM cache entry failed revalidation, refetching")

    async def _fetch() -> Any:
        validated = await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=model_cls,
            max_retries=max_retries,
            logger_name=logger_name,
            **chat_kwargs,
        )
        return validated.model_dump(mode="json")

    with _inflight_guard:
        lock = _inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _inflight_locks[key] = lock
    try:
        async with lock:
            # 双检：排队期间先到者可能已填充缓存；条目校验失败（schema
            # 漂移）同样按未命中处理，回源并覆盖
            cached = cache_get(key)
            if cached is not None:
                try:
                    return model_cls.model_validate(cached)
                except Exception:
                    _logger.warning("LLM cache entry failed revalidation, refetching")
            dump = await _fetch()
            cache_put(key, dump)
            return model_cls.model_validate(dump)
    finally:
        _release_inflight_lock(key, lock)

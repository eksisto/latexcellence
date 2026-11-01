"""
LLM 响应缓存：确定性调用的结果复用（降低 API 使用成本）。

适用对象是「纯函数式」的 LLM 调用——给定 (客户端, 消息, 采样参数) 必然
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

缓存键 = sha256(客户端身份 + 完整 messages + 采样参数)。客户端身份含
档案 id、base_url、模型与 extra_body（见 LLMClient.cache_identity），
同模型名但不同端点 / 档案的客户端不会串用结果；api_key 不参与。段落
文本、语言、自定义指令、提示词模板版本任一变化都会改变 key，因此无需
TTL；模型 / 档案切换经 reset_client_pool()（配置热重载接线点）整体清空。
多用户语义：key 含完整输入哈希，命中意味着另一用户提交了逐字节相同的
输入，结果是输入的确定性函数，不泄露任何额外信息。

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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

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


class CacheableLLM(Protocol):
    """llm_cache 依赖的最小客户端契约（测试替身只需实现 chat）。"""

    async def chat(self, messages: list[dict], **kwargs: Any) -> str: ...


def client_cache_identity(llm: Any) -> str:
    """客户端身份串：参与缓存键，隔离同模型名的不同档案 / 端点。

    优先取 LLMClient.cache_identity（档案 id + base_url + 模型 + extra_body）。
    测试替身等没有该属性的对象按 resolved_model 退化为 model:<名称>；连
    模型名都没有时用对象标识隔离，避免所有匿名客户端挤进同一 keyspace
    （空串会把不同客户端的结果串在一起，是最坏的一种“静默兜底”）。
    """
    identity = getattr(llm, "cache_identity", None)
    if isinstance(identity, str) and identity:
        return identity
    model = getattr(llm, "resolved_model", None)
    if isinstance(model, str) and model:
        return f"model:{model}"
    return f"anonymous:{id(llm):x}"


def build_cache_key(client: str, messages: list[dict], **params: Any) -> str:
    """构造缓存键：客户端身份 + 完整消息 + 采样参数的稳定哈希。

    client 由 client_cache_identity() 提供（档案 + 端点 + 模型 + extra_body）。
    messages / params 必须 JSON 可序列化：不可序列化时直接抛 TypeError，
    不再用 default=str 静默折叠（不同类型可能经 str() 撞成同一个 key）。
    sort_keys 保证字段顺序无关。
    """
    payload = json.dumps(
        {"client": client, "messages": messages, "params": params},
        ensure_ascii=False,
        sort_keys=True,
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
    """清空缓存与 in-flight 锁登记（配置热重载 / 测试隔离）。

    清锁登记是安全的：在途调用各自持有旧锁继续生效，清空后新的同 key
    请求可能另起一次真实调用，只退化成功率 / 命中率，不影响正确性。
    一并清理可避免跨事件循环（pytest 每用例换 loop）遗留锁。
    """
    with _cache_lock:
        _cache.clear()
    with _inflight_guard:
        _inflight_locks.clear()


@asynccontextmanager
async def _single_flight(key: str) -> AsyncIterator[None]:
    """同 key 未命中请求的合流锁：进入即独占，退出时释放并清理登记。"""
    with _inflight_guard:
        lock = _inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _inflight_locks[key] = lock
    try:
        async with lock:
            yield
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

    async with _single_flight(key):
        # 双检：排队期间先到者可能已填充缓存
        cached = cache_get(key)
        if cached is not None:
            return cached
        value = await fetch()
        cache_put(key, value)
        return value


def _revalidate(
    model_cls: type[BaseModel], cached: Any, key: str, logger_name: str
) -> BaseModel | None:
    """命中后按当前模型重新校验；旧条目 schema 漂移时返回 None 走回源。"""
    try:
        validated = model_cls.model_validate(cached)
    except Exception:
        _logger.warning("LLM cache entry failed revalidation, refetching")
        return None
    # 整篇复查一次可达上百次命中，逐条 INFO 会淹没日志，压到 debug
    _logger.debug("LLM cache hit | key=%s… | module=%s", key[:12], logger_name)
    return validated


async def cached_llm_validate(
    llm: CacheableLLM,
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

    缓存键含客户端身份（见 client_cache_identity），同模型名但不同档案 /
    端点的客户端不会串用结果。
    """
    key = build_cache_key(client_cache_identity(llm), messages, **chat_kwargs)

    cached = cache_get(key)
    if cached is not None:
        validated = _revalidate(model_cls, cached, key, logger_name)
        if validated is not None:
            return validated

    async with _single_flight(key):
        # 双检：排队期间先到者可能已填充缓存；校验失败（schema 漂移）
        # 同样按未命中处理，回源并覆盖
        cached = cache_get(key)
        if cached is not None:
            validated = _revalidate(model_cls, cached, key, logger_name)
            if validated is not None:
                return validated
        validated = await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=model_cls,
            max_retries=max_retries,
            logger_name=logger_name,
            **chat_kwargs,
        )
        cache_put(key, validated.model_dump(mode="json"))
        return validated

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
合流为一次真实调用，成功结果共享；leader 失败时同批等待者共享该异常
（不再各自串行重试），本批结束后新请求重新尝试。调用失败不缓存。

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
from typing import Any, Protocol

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.utils.json_utils import retry_with_validation

# 缓存条目上限（LRU 淘汰）。条目为段落级 JSON（典型几百字节～几 KB），
# 1024 条峰值内存仅数 MB；单篇文档逐段检查通常远小于该规模。
_CACHE_MAX_ENTRIES = 1024

_cache: OrderedDict[str, Any] = OrderedDict()
_cache_lock = threading.Lock()

# 同 key 并发合流：key → 在途 flight 的 Future。leader 的结果（成功或
# 失败）共享给排队者，本批结束后移除登记；leader 被取消时等待者收到
# _RETRY 哨兵后重新竞争，取消不扩散到其他请求。
_inflight_flights: dict[str, asyncio.Future[Any]] = {}
_inflight_guard = threading.Lock()

_RETRY = object()

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


def cache_delete(key: str) -> None:
    """删除单条缓存条目（schema 漂移 / 内容损坏的条目失效用）。"""
    with _cache_lock:
        _cache.pop(key, None)


def clear_llm_cache() -> None:
    """清空缓存与 in-flight 合流登记（配置热重载 / 测试隔离）。

    清登记是安全的：在途 flight 的 Future 仍被等待者持有，结果照常送达；
    清空后新的同 key 请求另起一批，只影响命中率 / 合流率，不影响正确性。
    一并清理可避免跨事件循环（pytest 每用例换 loop）遗留 Future。
    """
    with _cache_lock:
        _cache.clear()
    with _inflight_guard:
        _inflight_flights.clear()


async def _single_flight(key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
    """同 key 合流：leader 执行 factory，排队者共享其结果或异常。

    - leader 成功 / 失败：排队者得到同一结果 / 同一异常，失败路径不再
      退化为 N 次串行重试（延迟从 N 次超时降为 1 次）；
    - leader 被取消（客户端断连）：排队者收到 _RETRY 哨兵后重新竞争，
      取消不会扩散到其他请求；
    - 排队者拿到结果的深拷贝副本，不与 leader / 彼此共享可变对象；
    - 本批结束后移除登记，后续请求重新成为 leader（失败仍可重试）。
    """
    while True:
        with _inflight_guard:
            future = _inflight_flights.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                _inflight_flights[key] = future
                leader = True
            else:
                leader = False

        if not leader:
            outcome = await asyncio.shield(future)
            if outcome is _RETRY:
                continue
            # 与缓存读取同口径：等待者拿私有副本，不共享 leader 的可变对象
            return copy.deepcopy(outcome)

        try:
            value = await factory()
        except asyncio.CancelledError:
            # 只让本批等待者重新竞争，不把取消传播给其他请求
            future.set_result(_RETRY)
            raise
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # 标记已获取，避免无等待者时的未消费警告
            raise
        else:
            future.set_result(value)
            return value
        finally:
            with _inflight_guard:
                if _inflight_flights.get(key) is future:
                    del _inflight_flights[key]


async def cached_call(key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
    """按 key 取结果：先查缓存，未命中则真实调用并缓存，并发合流。

    fetch 抛出异常时不缓存；同批排队者共享该异常（fail fast），本批结束
    后新请求重新尝试，重试语义不变。

    返回值始终是调用方私有的：命中时是缓存条目的深拷贝副本，未命中时
    是 fetch 的返回值本身；同批排队者拿到的是它的深拷贝副本。调用方
    可以就地改写。
    """
    cached = cache_get(key)
    if cached is not None:
        return cached

    async def _load() -> Any:
        # 双检：排队期间上一批可能已填充缓存
        cached = cache_get(key)
        if cached is not None:
            return cached
        value = await fetch()
        cache_put(key, value)
        return value

    return await _single_flight(key, _load)


def _revalidate(
    model_cls: type[BaseModel], cached: Any, key: str, logger_name: str
) -> BaseModel | None:
    """命中后按当前模型重新校验；schema 漂移的坏条目删除并回源。"""
    try:
        validated = model_cls.model_validate(cached)
    except Exception:
        cache_delete(key)
        _logger.warning(
            "LLM cache entry failed revalidation, evicted | key=%s…", key[:12]
        )
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

    命中缓存时用 model_cls 重新校验一次（防 schema 漂移，成本可忽略），
    校验失败的坏条目直接删除并回源；未命中时走原 retry_with_validation
    全流程，成功后缓存 model_dump(mode="json")。校验失败的重试在
    retry_with_validation 内部进行，只有最终成功的结果进入缓存。

    缓存键含客户端身份（见 client_cache_identity），同模型名但不同档案 /
    端点的客户端不会串用结果；同批并发未命中请求共享唯一一次真实调用，
    失败时也共享同一个异常（不再各自串行重试）。
    """
    key = build_cache_key(client_cache_identity(llm), messages, **chat_kwargs)

    cached = cache_get(key)
    if cached is not None:
        validated = _revalidate(model_cls, cached, key, logger_name)
        if validated is not None:
            return validated

    async def _load() -> BaseModel:
        # 双检：排队期间上一批可能已填充缓存；校验失败（schema 漂移）
        # 的条目已被 _revalidate 删除，这里按未命中回源并覆盖
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

    return await _single_flight(key, _load)

"""
OpenAI API 统一封装模块。

功能：
    1. 非流式对话（chat）：发送消息、接收 JSON 解析结果，含自动重试 + 指数退避
    2. 流式对话（chat_stream）：逐 token 输出，用于 SSE 推送进度
    3. 请求频率控制（rate limiting），避免触发 API 限流
    4. 模型档案感知（multi-model §5.1）：每个实例绑定一个档案
       （profile_id），请求时经 _resolve_cfg() 实时解析「顶层字段 + 档案
       覆盖项」合并视图；档案间的连接、节流与上下文窗口探测彼此隔离

推理模型统一兼容（multi-model §5.2，封装层处理、service 无感知）：
    - 请求侧：参数类 4xx（拒绝 temperature / 要求 max_completion_tokens）
      自动降参重试一次；厂商私有参数走档案级 extra_body 显式透传
    - 响应侧：JSON 路径（chat / chat_with_usage）剥离 <think>...</think>；
      reasoning_content 天然不参与解析，流式协议不变

重试策略：
    - 网络/API 异常 → 重试（最多 3 次，间隔 2^n 秒指数退避）
    - 3 次均失败 → 抛出异常，由上层处理
    - JSON 解析与 schema 校验由 json_utils.retry_with_validation 统一负责
"""

import asyncio
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletion

from backend.core.config import BUILTIN_PROFILE_ID, LLMConfig, get_config
from backend.core.logger import get_logger
from backend.core.multiuser import llm_governance


@dataclass
class UsageInfo:
    """单次 LLM 调用的 token 用量。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# ── 上下文窗口探测常量 ────────────────────────────────────────────────────

# 探测/模型表全部失败时的兜底窗口大小
_DEFAULT_CONTEXT_WINDOW = 200000

# 常见模型上下文窗口（前缀匹配，列表内按前缀长度降序排列，
# 避免 "gpt-4.1" 被 "gpt-4" 先命中）
_BUILTIN_MODEL_WINDOWS: list[tuple[str, int]] = [
    ("gemini-3.5-flash-lite", 1_048_576),
    ("gemini-3.1-flash-lite", 1_048_576),
    ("gemini-3-flash-preview", 1_048_576),
    ("qwen3.6-max-preview", 262_144),
    ("qwen3.5-397b-a17b", 262_144),
    ("qwen3.5-122b-a10b", 262_144),
    ("deepseek-v4-flash", 1_000_000),
    ("qwen3-max-preview", 262_144),
    ("deepseek-v4-pro", 1_000_000),
    ("gemini-3.7-flash", 1_048_576),
    ("gemini-3.6-flash", 1_048_576),
    ("gemini-3.5-flash", 1_048_576),
    ("gemini-2.5-flash", 1_048_576),
    ("qwen3.6-35b-a3b", 262_144),
    ("qwen3-235b-a22b", 131_072),
    ("claude-sonnet-5", 1_000_000),
    ("claude-fable-5", 1_000_000),
    ("claude-opus-5", 1_000_000),
    ("gemini-3.1-pro", 1_048_576),
    ("gemini-2.5-pro", 1_048_576),
    ("qwen3.7-flash", 1_000_000),
    ("qwen3.6-flash", 1_000_000),
    ("qwen3.5-flash", 1_000_000),
    ("qwen3.8-max", 1_000_000),
    ("qwen3.7-max", 1_000_000),
    ("qwen3.7-plus", 1_000_000),
    ("qwen3.6-plus", 1_000_000),
    ("qwen3.6-max", 262_144),
    ("qwen3.6-27b", 262_144),
    ("qwen3.5-plus", 1_000_000),
    ("qwen3.5-27b", 262_144),
    ("qwen3-max", 262_144),
    ("qwen3-32b", 131_072),
    ("kimi-k3", 1_048_576),
    ("glm-5.3", 1_000_000),
    ("glm-5.2", 1_000_000),
    ("gpt-5.6", 1_050_000),
    ("gpt-5.5", 1_050_000),
    ("gpt-5.4", 1_050_000),
]

# 超限错误 → 窗口值 的解析正则（vLLM / OpenAI 兼容错误信息）
_WINDOW_ERROR_PATTERNS = [
    re.compile(r"maximum context length is\s+(\d+)", re.IGNORECASE),
    re.compile(r"maximum context length[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"context length[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"context_window[^\d]{0,40}(\d{4,})", re.IGNORECASE),
    re.compile(r"too many tokens[^\d]{0,40}(\d{4,})", re.IGNORECASE),
]


def _parse_context_window_value(value: Any) -> int | None:
    """宽松读取 /models 响应中的窗口数值字段。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


@dataclass
class StreamChunk:
    """
    chat_stream_with_usage / chat_stream_with_tools 流式片段。

    content / usage / tool_calls / reasoning 互斥：
        - 普通流片段：content 为字符串、其余为 None
        - 末尾用量片段：content=None、usage=UsageInfo（若 API 未返回则为 None）
        - 工具调用片段：content=None、usage=None、tool_calls=[ChatCompletionMessageToolCall, ...]
        - 思维链片段：content=None、usage=None、reasoning 为字符串
          （智谱 / DeepSeek 等兼容端点在 delta.reasoning_content 中流式吐出
          思维链；OpenAI 官方 chat completions 不含该字段，自然为 None）
    """

    content: str | None
    usage: UsageInfo | None
    tool_calls: list | None = None
    reasoning: str | None = None


# ── 档案化配置解析与推理模型兼容辅助（multi-model §5.1/§5.2）─────────


@dataclass
class ResolvedLLMCfg:
    """「llm 顶层字段 + 档案覆盖项」的合并视图（每次请求实时解析，不缓存）。

    档案覆盖项语义（multi-model §4）：None / 0 / {}（空值哨兵）表示
    继承顶层默认值。已接受限制：extra_body 以 {} 表示继承，因此「档案显式
    清空顶层 extra_body」不可表达（P1 评审记录）。
    """

    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    request_interval: float
    max_concurrency: int
    context_window_tokens: int
    models_probe_url: str
    extra_body: dict[str, Any]


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _strip_think_blocks(text: str) -> str:
    """剥离 content 中的 <think>...</think> 思维链片段（§5.2 响应侧清洗）。

    部分推理模型会把思维链以 <think> 标签混入正文，干扰下游 JSON 解析；
    在封装层统一剥离，service 无感知。未闭合的 <think> 不处理（无法确定
    边界）；reasoning_content 字段天然不进正文，无需处理。

    用 str.find 线性扫描而非正则：清洗在事件循环内同步执行，退化模型可能
    重复吐出海量未闭合 <think> 标签，正则 `<think>.*?</think>` 在该病态
    输入下为 O(标签数 × 文本长度)（实测 100KB / 数千标签达秒级，会阻塞
    整个事件循环）；find 版本严格 O(n)，语义与正则版逐例一致。
    """
    if not text or _THINK_OPEN not in text:
        return text
    parts: list[str] = []
    rest = text
    while True:
        start = rest.find(_THINK_OPEN)
        if start == -1:
            return "".join(parts) + rest
        end = rest.find(_THINK_CLOSE, start + len(_THINK_OPEN))
        if end == -1:
            # 剩余部分含未闭合标签，无法确定边界，保持原样
            return "".join(parts) + rest
        parts.append(rest[:start])
        rest = rest[end + len(_THINK_CLOSE) :]


def _adapt_kwargs_for_param_error(
    kwargs: dict[str, Any], exc: Exception
) -> dict[str, Any] | None:
    """按参数类 4xx 错误调整请求参数；无法识别或无可调整项时返回 None。

    识别方式（保守，避免掩盖真实错误）：openai APIStatusError 且状态码为
    400 / 422，且错误文本命中已知参数名。适配动作：
        - 文本拒绝 temperature / top_p → 从请求中剔除对应参数；
        - 文本要求 max_completion_tokens → 将 max_tokens 更名透传
          （OpenAI 推理系列兼容语义）。
    厂商私有参数不做猜测，继续走档案级 extra_body 显式透传。
    """
    if not isinstance(exc, APIStatusError) or exc.status_code not in (400, 422):
        return None
    text = str(exc).lower()
    adapted = dict(kwargs)
    changed = False
    for param in ("temperature", "top_p"):
        if param in text and param in adapted:
            adapted.pop(param)
            changed = True
    if "max_completion_tokens" in text and "max_tokens" in adapted:
        adapted["max_completion_tokens"] = adapted.pop("max_tokens")
        changed = True
    return adapted if changed else None


# fire-and-forget 关闭任务的强引用集：事件循环仅持弱引用，不持引用的任务
# 可能在 await 期间被 GC 回收，导致 close 静默丢失（CPython asyncio 已知
# 陷阱）；完成后经 done callback 自动出集，集合常态为空。
_pending_close_tasks: set[asyncio.Task] = set()


def _close_async_client_quietly(aclient: AsyncOpenAI) -> None:
    """尽力释放 AsyncOpenAI 底层 HTTP 连接（reset / 指纹重建路径的资源收尾）。

    reset() 可能从无事件循环的同步上下文调用（如配置保存），此时无法
    await close()，静默放弃、交由 GC 兜底；在事件循环内则以 fire-and-forget
    任务关闭，不阻塞调用方（与既有 reset 的非阻塞语义一致）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _close() -> None:
        try:
            await aclient.close()
        except Exception:  # noqa: BLE001 - 释放路径绝不抛出
            pass

    try:
        task = loop.create_task(_close())
    except RuntimeError:
        return
    _pending_close_tasks.add(task)
    task.add_done_callback(_pending_close_tasks.discard)


class LLMClient:
    """
    LLM API 客户端。
    封装 OpenAI AsyncClient，提供统一的重试和限流逻辑。
    所有 LLM 调用（文本校对、表达优化、公式 OCR 等）都通过此模块进行。
    """

    def __init__(self, profile_id: str | None = None):
        """档案化客户端（multi-model §5.1）。

        profile_id：绑定的模型档案 id；None 表示无档案——直接使用 llm 顶层
        字段，旧路径行为完全不变。实例在 __init__ 不缓存任何配置（AGENTS.md
        热重载规范），档案对象与覆盖项在每次请求时经 _resolve_cfg() 实时解析。
        """
        self._profile_id = profile_id
        self._client: AsyncOpenAI | None = None
        # 创建 AsyncOpenAI 时记录的档案指纹（base_url / api_key / model），
        # 供 client 属性惰性校验；None 表示"外部直接注入的实例"（测试路径），
        # 不参与指纹校验
        self._client_fingerprint: tuple[str, str, str] | None = None
        self._client_lock = threading.Lock()
        self._last_request_time: float = 0
        self._rate_limit_lock = asyncio.Lock()
        # 并发上限：本实例（即本档案）的全部 LLM 调用共享同一信号量，容量来自
        # 合并配置的 max_concurrency，热重载时随 reset() 重建。
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_capacity: int = 0
        self._logger = get_logger("llm_client")
        # 上下文窗口缓存：key=(档案 id, base_url, model)，按档案隔离避免跨档案
        # 串味（multi-model §8 风险与已裁决），reset() 时随配置一起失效
        self._context_window_cache: dict[tuple[str, str, str], int] = {}
        self._window_cache_lock = threading.Lock()

    def reset(self) -> None:
        """重置缓存的 AsyncOpenAI 实例与上下文窗口探测结果，使下次访问重新读取配置。"""
        with self._client_lock:
            stale, self._client = self._client, None
            self._client_fingerprint = None
        with self._window_cache_lock:
            self._context_window_cache.clear()
        self._semaphore = None
        self._semaphore_capacity = 0
        if stale is not None:
            _close_async_client_quietly(stale)

    # ── 档案化配置解析（每次请求实时，严禁在 __init__ 缓存）────────────

    def _resolve_cfg(self) -> "LLMConfig | ResolvedLLMCfg":
        """解析本客户端生效的 LLM 配置视图（每次请求实时调用）。

        - profile_id 为空 / 内置档案 id：直接返回 llm 顶层配置——内置档案即
          顶层字段的镜像（config.builtin_default_profile），零合并开销，旧
          路径行为完全不变；
        - 自定义档案：按 id 从当前 config 实时重新解析档案对象（不缓存，防
          止热重载后拿到旧档案），None / 0 / {} 覆盖项继承顶层默认值；
        - 档案不存在（防御路径：正常应先经 reset_client_pool 清池）：回退
          顶层配置，功能不中断。
        """
        cfg = get_config().llm
        if not self._profile_id or self._profile_id == BUILTIN_PROFILE_ID:
            return cfg
        profile = next((p for p in cfg.profiles if p.id == self._profile_id), None)
        if profile is None:
            self._logger.warning(
                "Profile %s not found in config; falling back to top-level llm config",
                self._profile_id,
            )
            return cfg
        return ResolvedLLMCfg(
            api_key=profile.api_key,
            base_url=profile.base_url,
            model=profile.model,
            temperature=(
                cfg.temperature if profile.temperature is None else profile.temperature
            ),
            max_tokens=(
                cfg.max_tokens if profile.max_tokens is None else profile.max_tokens
            ),
            request_interval=(
                profile.request_interval or cfg.request_interval
            ),  # 0 / null = 继承顶层
            max_concurrency=(
                cfg.max_concurrency
                if profile.max_concurrency is None
                else profile.max_concurrency
            ),
            # 0 / null = 继承顶层（顶层 0 = 自动探测）
            context_window_tokens=(
                profile.context_window_tokens or cfg.context_window_tokens
            ),
            models_probe_url=profile.models_probe_url or cfg.models_probe_url,
            # {} = 继承顶层（"显式清空顶层 extra_body"不可表达，已接受限制）
            extra_body=(
                dict(profile.extra_body) if profile.extra_body else dict(cfg.extra_body)
            ),
        )

    def _window_cache_key(
        self, cfg: "LLMConfig | ResolvedLLMCfg"
    ) -> tuple[str, str, str]:
        """上下文窗口缓存 key：按档案 id 隔离（multi-model §8 风险与已裁决）。"""
        return (self._profile_id or BUILTIN_PROFILE_ID, cfg.base_url, cfg.model)

    def _get_semaphore(self) -> asyncio.Semaphore:
        """按当前合并配置的 max_concurrency 获取并发信号量（容量变化时重建）。"""
        cap = self._resolve_cfg().max_concurrency
        if self._semaphore is None or self._semaphore_capacity != cap:
            self._semaphore = asyncio.Semaphore(cap)
            self._semaphore_capacity = cap
        return self._semaphore

    def resolved_max_concurrency(self) -> int:
        """当前合并配置（顶层字段 + 档案覆盖项）的并发上限。

        供批次编排层的外部信号量使用（与 _get_semaphore 容量同口径），
        避免外部访问 _resolve_cfg 私有方法。
        """
        return self._resolve_cfg().max_concurrency

    @property
    def resolved_model(self) -> str:
        """当前合并配置的模型名（每次实时解析，热重载语义）。

        供 llm_cache 构造缓存键使用（模型变化 → key 变化），避免外部
        访问 _resolve_cfg 私有方法。
        """
        return self._resolve_cfg().model

    # ── 上下文窗口探测 ─────────────────────────────────────────────────

    @staticmethod
    def _same_origin(base_url: str, probe_url: str) -> bool:
        """判断探测地址与主服务是否同源（scheme + host + 端口一致）。"""

        def _origin(url: str) -> tuple[str, str]:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            try:
                port = parsed.port
            except ValueError:
                port = None
            if port is None:
                port = 443 if parsed.scheme == "https" else 80
            return parsed.scheme.lower(), f"{host}:{port}"

        return _origin(base_url) == _origin(probe_url)

    @staticmethod
    def _probe_context_window_sync(
        base_url: str,
        model: str,
        api_key: str,
        probe_url: str,
        timeout: float = 5.0,
    ) -> int | None:
        """
        同步请求 OpenAI 兼容 /models 端点并解析模型上下文窗口。

        优先匹配 id == model（支持 :latest 等后缀），宽松读取：
        context_length / inputTokenLimit / outputTokenLimit / max_model_len /
        context_window / max_context_length。

        仅当探测地址与 base_url 同源时才携带 API key；跨域探测不发送密钥，
        避免主服务密钥泄漏给直连地址（vLLM 通常无需鉴权即可访问 /models）。

        任何失败（网络、鉴权、HTML 页面、字段缺失）都返回 None，绝不抛出。
        """
        base = (probe_url or base_url).rstrip("/")
        url = f"{base}/models"
        try:
            req = urllib.request.Request(url, method="GET")
            if api_key and LLMClient._same_origin(base_url, probe_url or base_url):
                req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", ""))
            if item_id != model and not item_id.startswith(f"{model}:"):
                continue
            for key in (
                "context_length",
                "inputTokenLimit",
                "outputTokenLimit",
                "max_model_len",
                "context_window",
                "max_context_length",
            ):
                window = _parse_context_window_value(item.get(key))
                if window is not None:
                    return window
        return None

    @staticmethod
    def _builtin_context_window(model: str) -> int | None:
        """按内置模型表前缀匹配上下文窗口；未命中返回 None。"""
        for prefix, window in _BUILTIN_MODEL_WINDOWS:
            if model == prefix or model.startswith(prefix + "-"):
                return window
        return None

    def learn_context_window_from_error(self, error_text: str) -> int | None:
        """
        从上下文超限错误信息中解析窗口值并缓存。

        供 vLLM / OpenAI 兼容网关（"This model's maximum context length is
        N tokens"）在探测不可用时学习真实窗口。解析成功返回窗口值，否则 None。
        """
        if not error_text:
            return None
        window = None
        for pattern in _WINDOW_ERROR_PATTERNS:
            m = pattern.search(error_text)
            if m:
                window = int(m.group(1))
                break
        if window is None:
            return None
        cfg = self._resolve_cfg()
        key = self._window_cache_key(cfg)
        with self._window_cache_lock:
            self._context_window_cache[key] = window
        self._logger.info(
            "Learned context window %s from error (model=%s)", window, cfg.model
        )
        return window

    def _learn_window_from_exception(self, exc: Exception) -> None:
        """异常路径统一尝试从错误文本学习窗口；失败静默。"""
        try:
            self.learn_context_window_from_error(str(exc))
        except Exception:
            pass

    async def get_context_window(self) -> int:
        """
        获取当前模型上下文窗口（token）。

        优先级链：
            1. 合并配置的 context_window_tokens（显式配置非 0；档案覆盖项
               0 / null 继承顶层）
            2. 缓存（含错误信息学习结果，按 (档案 id, base_url, model)）
            3. GET {probe_base}/models 探测（默认 base_url，可用
               models_probe_url 覆盖为直连地址）
            4. 内置模型表
            5. 默认值 200000

        探测失败只降级，绝不抛出；reset()（配置热重载）时缓存失效。
        """
        cfg = self._resolve_cfg()
        explicit = cfg.context_window_tokens
        if explicit > 0:
            return explicit

        key = self._window_cache_key(cfg)
        with self._window_cache_lock:
            cached = self._context_window_cache.get(key)
        if cached is not None:
            return cached

        window = await asyncio.to_thread(
            self._probe_context_window_sync,
            cfg.base_url,
            cfg.model,
            cfg.api_key,
            cfg.models_probe_url,
        )
        if window is None:
            window = self._builtin_context_window(cfg.model)
        if window is None:
            window = _DEFAULT_CONTEXT_WINDOW

        with self._window_cache_lock:
            self._context_window_cache[key] = window
        self._logger.info(
            "Context window resolved to %s for model=%s (probe_base=%s)",
            window,
            cfg.model,
            cfg.models_probe_url or cfg.base_url,
        )
        return window

    @property
    def client(self) -> AsyncOpenAI:
        """
        获取或创建 AsyncOpenAI 实例（懒加载 + 双检锁 + 档案指纹惰性校验）。

        - 首次访问时实时读取配置创建，支持运行时热重载 api_key / base_url；
        - 池化客户端（创建时记录过指纹）每次访问按档案指纹（base_url /
          api_key / model）惰性校验，指纹变化即重建内部实例，并关闭被替换
          的旧实例（该双保险路径是绕过 reset 钩子的磁盘直改热重载的主失效
          路径，不关闭会每次泄漏一个连接池；multi-model §5.1）；
        - 指纹为 None 表示外部直接注入的 _client（测试路径），跳过校验。
        """
        if self._client is not None and self._client_fingerprint is None:
            return self._client
        cfg = self._resolve_cfg()
        fingerprint = (cfg.base_url, cfg.api_key, cfg.model)
        stale: AsyncOpenAI | None = None
        if self._client is None or self._client_fingerprint != fingerprint:
            with self._client_lock:
                if self._client is None or self._client_fingerprint != fingerprint:
                    stale = self._client
                    self._client = AsyncOpenAI(
                        api_key=cfg.api_key,
                        base_url=cfg.base_url,
                    )
                    self._client_fingerprint = fingerprint
        if stale is not None:
            _close_async_client_quietly(stale)
        return self._client

    async def _rate_limit(self) -> None:
        """
        请求频率控制（按本实例档案独立节流）。
        确保两次请求之间的时间间隔不小于合并配置的 request_interval。
        如果上次请求距今不足间隔，则等待到足够时间后再放行。
        """
        async with self._rate_limit_lock:
            interval = self._resolve_cfg().request_interval
            elapsed = time.time() - self._last_request_time
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_request_time = time.time()

    async def _create_raw(
        self, kwargs: dict[str, Any], stream_options: dict[str, Any] | None
    ) -> Any:
        """直连 chat.completions.create（stream_options 可选）。"""
        if stream_options is None:
            return await self.client.chat.completions.create(**kwargs)
        return await self.client.chat.completions.create(
            **kwargs, stream_options=stream_options
        )

    async def _create_completion(
        self, kwargs: dict[str, Any], *, stream_options: dict[str, Any] | None = None
    ) -> Any:
        """发起 chat.completions.create，统一请求侧推理模型兼容（§5.2）。

        降级顺序：
            1. 参数类 4xx（如推理模型拒绝 temperature、要求
               max_completion_tokens）→ 按错误信息剔除/替换不支持参数，
               立即重试一次并记 warning 日志；
            2. （仅流式）stream_options 不被网关支持 → 去掉该参数重试
               （现状降级行为，保持不变）。
        """
        current = dict(kwargs)
        try:
            return await self._create_raw(current, stream_options)
        except Exception as first_error:
            adapted = _adapt_kwargs_for_param_error(current, first_error)
            if adapted is not None:
                self._logger.warning(
                    "LLM endpoint rejected request parameters "
                    "(%s); retrying once with adapted kwargs",
                    first_error,
                )
                current = adapted
                try:
                    return await self._create_raw(current, stream_options)
                except Exception as adapted_error:
                    first_error = adapted_error
            if stream_options is None:
                raise
            self._logger.warning(f"stream_options unsupported, fallback: {first_error}")
            return await self._create_raw(current, None)

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> str:
        """
        非流式 LLM 对话调用，返回原始文本。

        参数：
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            response_format: OpenAI response_format 参数，如 {"type": "json_object"}
            max_retries: 最大重试次数（默认 3）
            model: 覆盖使用的模型名（默认跟随 config.llm.model）

        返回：
            LLM 返回的原始文本（str），由调用方负责 JSON 解析和校验

        异常：
            重试耗尽后抛出最后一次异常的原始类型
        """
        content, _ = await self.chat_with_usage(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            max_retries=max_retries,
            model=model,
        )
        return content

    async def chat_with_usage(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> tuple[str, UsageInfo | None]:
        """非流式 LLM 对话调用，返回 (文本, usage)。

        受全局并发上限约束；多用户模式下另受每用户 LLM 治理约束
        （滑动窗口限流 + 并发租约，超限抛 429 语义 RuntimeError）。
        """
        async with llm_governance.admission_gate():
            async with self._get_semaphore():
                return await self._chat_with_usage_impl(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    max_retries=max_retries,
                    model=model,
                )

    async def _chat_with_usage_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        max_retries: int = 3,
        model: str | None = None,
    ) -> tuple[str, UsageInfo | None]:
        """
        非流式 LLM 对话调用，返回 (文本, usage)。

        usage 为 None 表示 API 未返回用量信息（调用方降级估算）；
        其余行为与 chat() 一致。
        """
        cfg = self._resolve_cfg()
        model_name = model or cfg.model
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        for attempt in range(max_retries):
            try:
                await self._rate_limit()

                self._logger.info(
                    f"LLM request | model={model_name} | tokens={max_tok} "
                    f"| attempt={attempt + 1}"
                )

                kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                }
                if response_format:
                    kwargs["response_format"] = response_format
                if cfg.extra_body:
                    kwargs["extra_body"] = cfg.extra_body

                response = await self._create_completion(kwargs)

                content = response.choices[0].message.content
                usage = response.usage
                usage_info: UsageInfo | None = None
                if usage:
                    usage_info = UsageInfo(
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens,
                    )
                    self._logger.info(
                        f"LLM response | prompt_tokens={usage.prompt_tokens} "
                        f"completion_tokens={usage.completion_tokens} "
                        f"total_tokens={usage.total_tokens}"
                    )

                # 响应侧清洗：剥离推理模型混入正文的 <think> 思维链片段（§5.2）
                return _strip_think_blocks(content) if content else content, usage_info

            except Exception as e:
                self._learn_window_from_exception(e)
                # 其他异常（网络错误、API 错误、认证失败等）
                self._logger.error(
                    f"LLM request failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """流式 LLM 对话调用（Server-Sent Events）。

        受全局并发上限约束；多用户模式下另受每用户 LLM 治理约束。
        """
        async with llm_governance.admission_gate():
            async with self._get_semaphore():
                async for chunk in self._chat_stream_impl(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk

    async def _chat_stream_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """
        流式 LLM 对话调用（Server-Sent Events）。

        用于前端实时显示 LLM 生成进度，逐 token 返回字符串。
        每个 yield 返回一个 token 片段，由调用方自行拼接。

        参数：
            messages: OpenAI 格式的消息列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个 token 片段（string），逐 token 流式返回
        """
        cfg = self._resolve_cfg()
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(f"LLM stream request | model={cfg.model} | tokens={max_tok}")

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
        }
        if cfg.extra_body:
            kwargs["extra_body"] = cfg.extra_body
        stream = await self._create_completion(kwargs)

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
    ) -> ChatCompletion:
        """带 Function Calling 的非流式 LLM 调用。

        受全局并发上限约束；多用户模式下另受每用户 LLM 治理约束。
        """
        async with llm_governance.admission_gate():
            async with self._get_semaphore():
                return await self._chat_with_tools_impl(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                )

    async def _chat_with_tools_impl(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
    ) -> ChatCompletion:
        """
        带 Function Calling 的非流式 LLM 调用。

        返回完整的 ChatCompletion 对象（含 tool_calls），
        由调用方决定如何处理 tool_call 循环。

        参数：
            messages: OpenAI 格式的消息列表
            tools: Function Calling 工具定义列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）
            max_retries: 最大重试次数（默认 3）

        返回：
            openai.ChatCompletion 对象

        异常：
            重试耗尽后抛出最后一次异常的原始类型
        """
        cfg = self._resolve_cfg()
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        for attempt in range(max_retries):
            try:
                await self._rate_limit()

                self._logger.info(
                    f"LLM request (with tools) | model={cfg.model} | "
                    f"tools={len(tools) if tools else 0} | attempt={attempt + 1}"
                )

                kwargs: dict[str, Any] = {
                    "model": cfg.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                }
                if tools:
                    kwargs["tools"] = tools
                if cfg.extra_body:
                    kwargs["extra_body"] = cfg.extra_body

                response = await self._create_completion(kwargs)

                usage = response.usage
                if usage:
                    self._logger.info(
                        f"LLM response (with tools) | prompt_tokens={usage.prompt_tokens} "
                        f"completion_tokens={usage.completion_tokens} "
                        f"total_tokens={usage.total_tokens}"
                    )

                return response

            except Exception as e:
                self._learn_window_from_exception(e)
                self._logger.error(
                    f"LLM request with tools failed "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Max retries exceeded")

    async def chat_stream_with_usage(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """流式 LLM 对话调用，并在流末尾附带 token 用量。

        受全局并发上限约束；多用户模式下另受每用户 LLM 治理约束。
        """
        async with llm_governance.admission_gate():
            async with self._get_semaphore():
                async for chunk in self._chat_stream_with_usage_impl(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk

    async def _chat_stream_with_usage_impl(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """
        流式 LLM 对话调用，并在流的末尾附带 token 用量信息。

        与 chat_stream 的区别：
            - 每个 yield 是 StreamChunk（content 或 usage 二者之一）
            - 末尾会单独 yield 一个 StreamChunk(content=None, usage=Usage)
              用于上层累计 token 计数。

        使用 stream_options={"include_usage": True} 让 OpenAI 兼容 API
        在流末尾推一个仅含 usage 的 chunk。若第三方代理不支持该参数，
        会捕获异常后降级为 usage=None。

        参数：
            messages: OpenAI 格式的消息列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个 StreamChunk 实例。
        """
        cfg = self._resolve_cfg()
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(
            f"LLM stream-with-usage request | model={cfg.model} | tokens={max_tok}"
        )

        kwargs = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
        }
        if cfg.extra_body:
            kwargs["extra_body"] = cfg.extra_body
        stream = await self._create_completion(
            kwargs, stream_options={"include_usage": True}
        )

        async for chunk in stream:
            content = None
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
            reasoning = (
                getattr(chunk.choices[0].delta, "reasoning_content", None)
                if chunk.choices
                else None
            )
            if reasoning:
                yield StreamChunk(content=None, usage=None, reasoning=reasoning)
            usage = getattr(chunk, "usage", None)
            # 仅在末次 chunk 会有 usage，且此时通常 content 为 None
            if content is not None:
                yield StreamChunk(content=content, usage=None)
            if usage is not None:
                yield StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                )

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """流式 LLM 对话调用，支持 Function Calling 工具检测。

        受全局并发上限约束；多用户模式下另受每用户 LLM 治理约束。
        """
        async with llm_governance.admission_gate():
            async with self._get_semaphore():
                async for chunk in self._chat_stream_with_tools_impl(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk

    async def _chat_stream_with_tools_impl(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator["StreamChunk"]:
        """
        流式 LLM 对话调用，支持 Function Calling 工具检测。

        与 chat_stream_with_usage 的区别：
            - 传入 tools 参数用于 LLM 工具调用
            - 流中同时产出 content（文本）和 tool_calls（工具调用）
            - 文本 token 一到就 yield，前端可立即显示
            - 工具调用在流末尾累积完整后，以一条 tool_calls 片段 yield

        参数：
            messages: OpenAI 格式的消息列表
            tools: OpenAI 格式的 Function Calling 工具定义列表
            temperature: 生成温度（覆盖 config 中的默认值）
            max_tokens: 最大生成 token 数（覆盖 config 中的默认值）

        生成：
            每次 yield 一个 StreamChunk 实例。
        """
        cfg = self._resolve_cfg()
        temp = temperature if temperature is not None else cfg.temperature
        max_tok = max_tokens if max_tokens is not None else cfg.max_tokens

        await self._rate_limit()

        self._logger.info(
            f"LLM stream-with-tools request | model={cfg.model} | tokens={max_tok} | tools={len(tools)}"
        )

        kwargs = {
            "model": cfg.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
            "tools": tools,
        }
        if cfg.extra_body:
            kwargs["extra_body"] = cfg.extra_body
        stream = await self._create_completion(
            kwargs, stream_options={"include_usage": True}
        )

        tool_calls_acc: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            content = None
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content

            reasoning = (
                getattr(chunk.choices[0].delta, "reasoning_content", None)
                if chunk.choices
                else None
            )
            if reasoning:
                yield StreamChunk(content=None, usage=None, reasoning=reasoning)

            if chunk.choices and chunk.choices[0].delta.tool_calls:
                for tc in chunk.choices[0].delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"][
                                "arguments"
                            ] += tc.function.arguments

            usage = getattr(chunk, "usage", None)
            if content is not None:
                yield StreamChunk(content=content, usage=None)
            if usage is not None:
                yield StreamChunk(
                    content=None,
                    usage=UsageInfo(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                )

        if tool_calls_acc:
            from openai.types.chat.chat_completion_message_tool_call import (
                ChatCompletionMessageToolCall,
                Function,
            )

            collected: list[ChatCompletionMessageToolCall] = []
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                collected.append(
                    ChatCompletionMessageToolCall(
                        id=tc["id"],
                        function=Function(
                            name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"],
                        ),
                        type="function",
                    )
                )
            yield StreamChunk(content=None, usage=None, tool_calls=collected)


# ── 流式消费共享辅助（三个 Agent 共用） ──────────────────────────────────


def assistant_message_from_stream(
    content: str, tool_calls: list[Any] | None
) -> dict[str, Any]:
    """把流式累计结果构造为 assistant 消息 dict（等价于 msg.model_dump()）。

    流式路径拿不到完整的 ChatCompletionMessage，只能由调用方累计 content
    并收集流末尾的 tool_calls 后重组；思维链（reasoning）只读展示，
    禁止写入对话历史。
    """
    message: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return message


def _is_transient_stream_error(e: Exception) -> bool:
    """流式调用失败是否属于可重试的瞬时抖动（连接/超时/限流/5xx）。

    永久性错误（400 鉴权、上下文超限等）重试无意义，直接抛出，避免
    无谓等待与误导性日志。
    """
    if isinstance(e, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code >= 500
    return isinstance(e, (ConnectionError, TimeoutError))


async def chat_stream_with_tools_retry(
    llm: LLMClient,
    *,
    messages: list[dict],
    tools: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    logger: logging.Logger | None = None,
) -> AsyncIterator[StreamChunk]:
    """带"首 chunk 前瞬时失败重试"的流式工具调用。

    流式调用无内置重试。LLM 尚未吐出任何 chunk 时即失败（连接/限流抖动）
    无法区分"已产生增量"，此时补一次重试是安全的；仅对瞬时异常重试，
    永久性错误直接抛出。已有增量输出后的失败无法安全重放，按原样抛出。
    """
    stream_attempt = 0
    while True:
        stream_attempt += 1
        emitted_any = False
        try:
            async for chunk in llm.chat_stream_with_tools(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                emitted_any = True
                yield chunk
            return
        except Exception as e:
            if emitted_any or stream_attempt >= 2 or not _is_transient_stream_error(e):
                raise
            if logger is not None:
                logger.warning(
                    "chat_stream_with_tools failed before first chunk "
                    "(transient), retrying once: %s",
                    e,
                )
            await asyncio.sleep(1)


def get_llm_client() -> LLMClient:
    """获取 default 槽位的 LLMClient（模型路由器薄别名，multi-model §5.1）。

    存量导入路径（from backend.core.llm_client import get_llm_client）保持
    不变；P3 调用点迁移后本别名仅服务尚未迁移的调用方与存量测试。函数级
    导入以避免 llm_client ↔ model_router 的模块环。
    """
    from backend.core.model_router import get_client_for

    return get_client_for("default")

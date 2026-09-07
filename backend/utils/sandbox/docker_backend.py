"""Docker 执行后端：容器创建 / 等待 / 日志 / 资源限制（v2 方案 §5.4）。

容器安全基线（runner 强制注入，镜像无需配合）：
- ``--network none``：无网络；
- ``--read-only`` + tmpfs ``/tmp``（容量 tmpfs_mb）：根文件系统只读，
  产物只能落在挂载目录；
- ``--pids-limit`` / ``--memory`` / ``--cpus``：资源上限；
- ``--security-opt no-new-privileges`` + ``cap_drop=ALL`` + 非 root
  （与宿主后端 uid:gid 一致，避免产物属主错乱）。

挂载使用同绝对路径（host_path 即容器内路径）。docker SDK 的阻塞调用
一律经 asyncio.to_thread 调度；SDK 缺失或 daemon 不可达统一翻译为
sandbox_unavailable 错误。

支持矩阵：Linux 原生 amd64/arm64。Windows/macOS 的 Docker Desktop
存在路径翻译，破坏同路径挂载前提，本后端直接视为不可用（回落或报错）。

warm pool（容器预创建池，部署收口阶段三）：
- 按 image_kind 维护"已创建未启动"容器队列，池深 =
  ``sandbox.max_concurrent_global``（每副本本地）；
- Docker 容器的 command / 挂载 / 环境变量在 create 时固化、无法在
  start 时改写，而 ExecSpec 逐请求不同——因此池条目携带创建时的完整
  kwargs（"形状"），仅当与本次 ExecSpec 推导的形状**逐字段一致**时才
  复用（执行语义不变：每执行一个 start 一次的全新容器）；形状来自
  最近一次执行的 spec（补充即按其克隆），启动期占位容器（无形状）
  只用于镜像预热，永不参与执行；
- 池容器统一打 label ``latexcellence.warm_pool=1`` 并携带副本标识；
  孤儿清理是**每副本职责**（启动期 + 周期对账，不进维护循环选主）。
"""

import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import datetime

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.sandbox.errors import sandbox_unavailable
from backend.utils.sandbox.models import ExecResult, ExecSpec, SandboxLimits

logger = get_logger("sandbox.docker")

# 容器状态轮询间隔（秒）
_POLL_INTERVAL_SECONDS = 0.2

# ── warm pool（部署收口阶段三）────────────────────────────────
# 池容器标签（孤儿对账按 label + 副本标识过滤）
WARM_POOL_LABEL = "latexcellence.warm_pool"
WARM_POOL_REPLICA_LABEL = "latexcellence.warm_pool.replica"
WARM_POOL_CREATED_AT_LABEL = "latexcellence.warm_pool.created_at"
# 补充任务触发延迟（秒）：给"形状是否稳定"一个观察窗口——稳态工作负载
# （同项目反复编译）照常补满，逐请求换临时目录的负载在唤醒时发现形状
# 已漂移而放弃，避免为死形状制造容器垃圾
REFILL_DELAY_SECONDS = 0.25
# 补充任务并发上限（全后端，含全部 image_kind）
MAX_CONCURRENT_REFILLS = 2
# 外来副本池容器视为孤儿的最小年龄（秒）：存活副本的空闲池容器不因
# 其他副本的对账被立刻回收；本副本崩溃残留至多滞后一个周期被清理
ORPHAN_GRACE_SECONDS = 900.0


def _resolve_replica_id() -> str:
    """副本标识：优先环境变量（部署方显式指定，重启稳定），否则按
    maintenance_lease.make_holder() 既有约定生成 hostname:hex8（进程
    唯一）。本机多进程副本必须各自唯一，故默认不使用纯 hostname。"""
    env_id = os.environ.get("LATEXCELLENCE_SANDBOX_REPLICA_ID", "").strip()
    if env_id:
        return env_id
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


class _PoolEntry:
    """池条目：已创建未启动的容器 + 创建时的完整 kwargs 形状。

    shape=None 表示启动期占位容器（仅预热镜像层缓存），永不匹配任何
    真实 ExecSpec 形状。"""

    __slots__ = ("container", "shape")

    def __init__(self, container, shape: dict | None) -> None:
        self.container = container
        self.shape = shape


def docker_sdk_available() -> bool:
    """docker SDK 是否已安装（不触发任何 I/O）。"""
    import importlib.util

    return importlib.util.find_spec("docker") is not None


def _decode_pair(pair: tuple[bytes, bytes]) -> tuple[str, str]:
    out_b, err_b = pair
    return (
        (out_b or b"").decode("utf-8", errors="replace"),
        (err_b or b"").decode("utf-8", errors="replace"),
    )


def _demux_docker_stream(raw: bytes) -> tuple[bytes, bytes]:
    """解复用 Docker 日志流（非 TTY 模式的帧协议）。

    帧格式（Docker Engine API 稳定约定）：
        [1B stream type][3B flags][4B big-endian payload length][payload]
    type: 0=stdin 1=stdout 2=stderr（3/4 为 resize 等控制帧，丢弃）。
    手动解复用而非依赖 SDK 的 demux 参数：docker-py 7.x 的 logs() 已移除
    该参数，而帧协议跨版本稳定。首帧校验失败时按 TTY 输出整体归入 stdout。
    """
    if not raw:
        return b"", b""
    out, err = bytearray(), bytearray()
    i, n = 0, len(raw)

    def _frame_len_valid(pos: int) -> bool:
        return pos + 8 <= n

    # 首帧头校验：type 必须在 0..4 且长度不超过剩余字节，否则视为 TTY 直写流
    if _frame_len_valid(0) and raw[0] <= 4 and int.from_bytes(raw[4:8], "big") <= n - 8:
        while i + 8 <= n:
            ftype = raw[i]
            length = int.from_bytes(raw[i + 4 : i + 8], "big")
            payload = raw[i + 8 : i + 8 + length]
            if ftype == 1:
                out += payload
            elif ftype == 2:
                err += payload
            i += 8 + length
        return bytes(out), bytes(err)

    return raw, b""


class DockerBackend:
    """基于 docker SDK 的执行后端（客户端惰性创建、线程安全）。

    warm pool 状态（池队列 / 在飞集合 / 补充任务计数）由 ``_pool_guard``
    保护——execute 既可能来自服务层主循环，也可能来自 rar 解压 worker
    线程的独立事件循环，与并发闸门同理必须线程锁而非 asyncio 原语。
    """

    def __init__(self) -> None:
        self._client = None
        self._client_lock = threading.Lock()
        # ── warm pool 状态 ──
        self._replica_id = _resolve_replica_id()
        self._pool: dict[str, deque[_PoolEntry]] = {}
        self._pool_guard = threading.Lock()
        # 已弹出、正在执行但尚未 remove 的池容器 id（对账保护，防误杀）
        self._inflight: set[str] = set()
        # 各 image_kind 最近一次执行的形状（补充任务按其克隆）
        self._refill_shape: dict[str, dict | None] = {}
        self._refill_active = 0
        # 池命中观测：hit / miss（池开但未命中）/ off（开关关闭）
        self._pool_stats = {"hit": 0, "miss": 0, "off": 0}
        # 最近一次 execute 的 (准备耗时 ms, 池状态)，供基准脚本/测试读取
        self._last_prep: tuple[float, str] | None = None
        self._closed = threading.Event()

    # ── 客户端管理 ────────────────────────────────────────────
    def _get_client(self):
        if os.name != "posix":
            raise RuntimeError("Docker sandbox requires a POSIX host")
        with self._client_lock:
            if self._client is None:
                import docker

                self._client = docker.from_env(timeout=10)
            return self._client

    async def ping(self) -> bool:
        """探测 daemon 可达性（结果由 runner 做 TTL 缓存）。"""
        try:
            return await asyncio.to_thread(self._ping_sync)
        except Exception as e:  # noqa: BLE001 — 探测失败即不可达，原因仅记录
            logger.debug("Docker daemon ping failed: %s", e)
            return False

    def _ping_sync(self) -> bool:
        client = self._get_client()
        client.ping()
        return True

    # ── 挂载预检（v2 P7 收口：容器化后端部署的静默失效防护）──────
    async def preflight_mount_check(self, image: str) -> bool:
        """实测宿主 daemon 能否绑定本进程可见的绝对路径（一次性预检）。

        同绝对路径挂载契约（models.py：host_path 即容器内路径）要求后端
        进程与 Docker daemon 共享同一文件系统视图。后端自身容器化部署
        （如 compose 把 data 卷挂在 /app/data）时该前提不成立——spec.cwd
        的宿主路径不存在，容器在 create 阶段才报 "bind source path does
        not exist"，且 auto 模式不会回落（probe 只 ping daemon）。此处以
        最小容器对临时目录做一次真实 bind mount，失败由 runner 视同不可达。
        """
        return await asyncio.to_thread(self._preflight_mount_sync, image)

    def _preflight_mount_sync(self, image: str) -> bool:
        client = self._get_client()
        probe_dir = tempfile.mkdtemp(prefix="lx-sandbox-preflight-")
        container = None
        try:
            from docker.types import Mount

            mounts = [
                Mount(
                    target=probe_dir,
                    source=probe_dir,
                    type="bind",
                    read_only=True,
                )
            ]
            kwargs: dict = {
                "image": image,
                "command": ["true"],
                "detach": True,
                "mounts": mounts,
                "read_only": True,
                "security_opt": ["no-new-privileges"],
                "cap_drop": ["ALL"],
                "mem_limit": "64M",
                "pids_limit": 16,
            }
            kwargs[self._network_kwarg_name(client)] = "none"
            container = client.containers.create(**kwargs)
            container.start()
            container.wait(timeout=20)
            logger.info(
                "sandbox bind-mount preflight passed | image=%s | dir=%s",
                image,
                probe_dir,
            )
            return True
        except Exception as e:  # noqa: BLE001 — 预检失败即视同不可达，原因仅记录
            message = str(e)
            if "bind source path" in message or (
                "no such file or directory" in message.lower()
            ):
                logger.warning(
                    "sandbox bind-mount preflight failed: the host daemon "
                    "cannot see this process's filesystem view (%s). A "
                    "containerized backend must share the mounted data path "
                    "with the host daemon; falling back / failing fast.",
                    e,
                )
            else:
                logger.warning(
                    "sandbox bind-mount preflight failed (image=%s): %s", image, e
                )
            return False
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as e:  # noqa: BLE001 — 清理失败仅记录
                    logger.debug("preflight container cleanup failed: %s", e)
            shutil.rmtree(probe_dir, ignore_errors=True)

    @staticmethod
    def _network_kwarg_name(client) -> str:
        """网络隔离参数名随 SDK 版本变化（旧版 network_mode → 新版
        networking_mode）；按已安装 SDK 的签名选择，避免盲试 TypeError。"""
        import inspect

        params = inspect.signature(client.containers.create).parameters
        return "networking_mode" if "networking_mode" in params else "network_mode"

    def close(self) -> None:
        self._closed.set()
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001 — 关闭失败不影响退出
                    pass
                self._client = None

    # ── warm pool（部署收口阶段三）────────────────────────────
    @staticmethod
    def _warm_pool_enabled() -> bool:
        """开关实时读取（配置热重载即时生效）；仅 docker 后端路径调用，
        auto 模式探测命中 docker 才会走到这里。"""
        try:
            return bool(get_config().sandbox.warm_pool_enabled)
        except Exception:  # noqa: BLE001 — 配置未加载/字段缺失按关闭处理
            return False

    @staticmethod
    def _pool_depth() -> int:
        """池深 = 全局并发上限（实时读取，随热重载变化）。"""
        try:
            return max(1, int(get_config().sandbox.max_concurrent_global))
        except Exception:  # noqa: BLE001 — 配置不可用时按最小池深
            return 1

    def _shape_for(self, spec: ExecSpec, image: str, limits: SandboxLimits) -> dict:
        """ExecSpec → 容器创建 kwargs（即"形状"）。

        复用与即时创建完全相同的 kwargs 构造器，保证池容器与现网容器
        的安全基线逐字段一致；比较时不含 label 与网络参数（二者由
        create 阶段补充）。"""
        return self._build_container_kwargs(spec, image, limits)

    def _pool_labels(self) -> dict[str, str]:
        return {
            WARM_POOL_LABEL: "1",
            WARM_POOL_REPLICA_LABEL: self._replica_id,
            WARM_POOL_CREATED_AT_LABEL: f"{time.time():.3f}",
        }

    def _pool_create(self, shape: dict):
        """按给定形状创建池容器（含 label 与网络隔离参数）。"""
        client = self._get_client()
        kwargs = dict(shape)
        kwargs["labels"] = self._pool_labels()
        kwargs[self._network_kwarg_name(client)] = "none"
        return client.containers.create(**kwargs)

    def _pool_take(self, kind: str, shape: dict):
        """弹出该类别池中形状完全一致的已创建容器；无匹配返回 None。

        匹配条目的弹出与在飞集合登记在同一临界区内完成——对账据此识别
        "已弹出待启动"的容器，锁释放瞬间起即受保护、不会被误杀。形状不
        一致的条目（含启动期占位容器）顺手出队并异步移除——它们对当前
        执行已无用，留着只会占满池深。"""
        stale: list[_PoolEntry] = []
        try:
            while True:
                with self._pool_guard:
                    queue = self._pool.get(kind)
                    entry = queue.popleft() if queue else None
                    if entry is not None and entry.shape == shape:
                        cid = getattr(entry.container, "id", None)
                        if cid:
                            self._inflight.add(cid)
                        return entry.container
                if entry is None:
                    return None
                stale.append(entry)
        finally:
            for entry in stale:
                self._discard_async(entry.container)

    def _pool_put(self, kind: str, container, shape: dict | None) -> bool:
        """入池（未达池深时）；满员返回 False（调用方负责移除容器）。"""
        with self._pool_guard:
            queue = self._pool.setdefault(kind, deque())
            if len(queue) >= self._pool_depth():
                return False
            queue.append(_PoolEntry(container, shape))
            return True

    def _pool_unregister(self, cid: str | None) -> None:
        if not cid:
            return
        with self._pool_guard:
            self._inflight.discard(cid)

    def _discard_async(self, container) -> None:
        """后台移除无用池容器（形状漂移 / 满员 / 关闭后的遗留）。"""

        def _remove():
            try:
                container.remove(force=True)
            except Exception as e:  # noqa: BLE001 — 清理失败仅记录
                logger.debug("warm pool discard failed: %s", e)

        threading.Thread(
            target=_remove, name="sandbox-pool-discard", daemon=True
        ).start()

    def _schedule_refill(
        self, kind: str, shape: dict, image: str, limits: SandboxLimits
    ) -> None:
        """调度一次异步补充（延迟 + 形状复核，见 REFILL_DELAY_SECONDS）。

        并发超限时直接放弃调度：下一次执行完成后还会再调度，池深由
        后续执行逐步补齐即可，无需排队。"""
        if self._closed.is_set() or not self._warm_pool_enabled():
            return
        with self._pool_guard:
            if self._refill_active >= MAX_CONCURRENT_REFILLS:
                return
            self._refill_active += 1
        try:
            threading.Thread(
                target=self._refill_worker,
                args=(kind, shape, image, limits, REFILL_DELAY_SECONDS),
                name="sandbox-pool-refill",
                daemon=True,
            ).start()
        except Exception:
            # 线程启动失败（极端资源受限）：回滚计数，避免永久封死补充通道
            with self._pool_guard:
                self._refill_active -= 1
            raise

    def _refill_worker(
        self,
        kind: str,
        shape: dict,
        image: str,
        limits: SandboxLimits,
        delay: float = 0.0,
    ) -> None:
        """补充任务体：延迟后复核形状未漂移，再把该类别补足到池深。

        线程而非事件循环任务：池补充可能由 rar 解压的 worker 线程循环
        触发，该循环在 execute 返回后随即关闭，循环内任务没有机会运行。
        """
        try:
            if delay > 0:
                time.sleep(delay)
            while not (self._closed.is_set() or not self._warm_pool_enabled()):
                with self._pool_guard:
                    current = self._refill_shape.get(kind)
                    size = len(self._pool.get(kind, ()))
                if current is None or current != shape:
                    return  # 形状已漂移：不为死形状制造容器
                if size >= self._pool_depth():
                    return
                try:
                    container = self._pool_create(shape)
                except Exception as e:  # noqa: BLE001 — 补充失败等下次执行再试
                    logger.debug("warm pool refill create failed: %s", e)
                    return
                if not self._pool_put(kind, container, shape):
                    self._discard_async(container)  # 并发补充越界，弃置
                    return
                logger.debug(
                    "warm pool refilled | kind=%s | image=%s | size=%d",
                    kind,
                    image,
                    size + 1,
                )
        finally:
            with self._pool_guard:
                self._refill_active -= 1

    async def warm_up(self, kinds_images: dict[str, str], limits: SandboxLimits) -> int:
        """启动期预热：每个 docker 类别创建一个占位容器（lifespan 挂载点）。

        占位容器无形状（永不参与执行，首次执行该类别时被丢弃），价值在
        于：镜像层/页缓存预热吸收 daemon 重启后的首次冷启成本，并让池
        创建链路（kwargs / label / 对账）在启动期得到实测。"""
        created = 0
        for kind, image in kinds_images.items():
            try:
                container = await asyncio.to_thread(
                    self._create_placeholder, image, limits
                )
            except Exception as e:  # noqa: BLE001 — 镜像缺失等，跳过该类别
                logger.debug("warm pool placeholder skipped for %s: %s", kind, e)
                continue
            if self._pool_put(kind, container, None):
                created += 1
            else:
                self._discard_async(container)
        if created:
            logger.info(
                "warm pool prewarmed | placeholders=%d | kinds=%s | replica=%s",
                created,
                ",".join(sorted(kinds_images)),
                self._replica_id,
            )
        return created

    def _create_placeholder(self, image: str, limits: SandboxLimits):
        """创建启动期占位容器：完整安全基线，但不挂载任何宿主路径。"""
        client = self._get_client()
        # 经既有构造器获得安全基线字段，再中和 ExecSpec 相关字段——
        # 占位容器不承载执行，绝不绑定宿主目录
        spec = ExecSpec(command=["true"], cwd="/tmp")
        kwargs = self._build_container_kwargs(spec, image, limits)
        kwargs["mounts"] = []
        kwargs["working_dir"] = "/tmp"
        kwargs["labels"] = self._pool_labels()
        kwargs[self._network_kwarg_name(client)] = "none"
        return client.containers.create(**kwargs)

    async def reconcile_orphans(
        self, grace_seconds: float = ORPHAN_GRACE_SECONDS
    ) -> int:
        """对账清理本副本遗留的池容器（每副本职责，不进维护循环选主）。

        容器是 daemon 全局资源、池是副本本地状态，无法进"持租约者执行"
        的维护循环选主语义（见 docs/multi-user.md §10 有意偏离补记）。
        规则：本副本标签 → 运行中与在飞（已弹出待启动）容器是活跃执行，
        绝不触碰——开关热关闭也不例外，在跑编译必须自然收尾；开关开启时
        仅清不在存活集合（池队列 + 在飞）的非运行容器（create/put 竞态
        残留），开关关闭时清退池遗留（队列内 + 游离残留，在飞容器保护至
        执行收尾）。外来副本的 created/exited 容器超过 grace → 移除
        （崩溃副本的孤儿至多滞后 grace + 一个对账周期被清理）；外来运行
        中容器是活跃执行，绝不触碰。
        """
        return await asyncio.to_thread(self._reconcile_sync, grace_seconds)

    @staticmethod
    def _parse_created_at(labels: dict, attrs: dict) -> float:
        """容器创建时间（unix 秒）：优先自建 label，回落解析 daemon 的
        RFC3339 字符串（纳秒精度需截断后才能被 fromisoformat 接受）。"""
        raw = labels.get(WARM_POOL_CREATED_AT_LABEL, "")
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        raw = str(attrs.get("Created") or "").strip()
        # 截断小数秒到 6 位（微秒）并归一化 Z 后缀
        head, _, frac_zone = raw.partition(".")
        tail = ""
        if frac_zone:
            digits = "".join(ch for ch in frac_zone if ch.isdigit())
            tail = "." + digits[:6] + frac_zone[len(digits) :]
        try:
            return datetime.fromisoformat(
                head + tail.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            return 0.0

    def _reconcile_sync(self, grace_seconds: float) -> int:
        client = self._get_client()
        with self._pool_guard:
            inflight: set[str] = set(self._inflight)
            queued: set[str] = set()
            for queue in self._pool.values():
                for entry in queue:
                    cid = getattr(entry.container, "id", None)
                    if cid:
                        queued.add(cid)
            pool_on = (not self._closed.is_set()) and self._warm_pool_enabled()
        try:
            candidates = client.containers.list(
                all=True, filters={"label": f"{WARM_POOL_LABEL}=1"}
            )
        except Exception as e:  # noqa: BLE001 — daemon 不可达时静默跳过
            logger.debug("warm pool reconcile list failed: %s", e)
            return 0
        now = time.time()
        removed = 0
        for c in candidates:
            attrs = c.attrs or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            state = (attrs.get("State") or {}).get("Status")
            mine = labels.get(WARM_POOL_REPLICA_LABEL) == self._replica_id
            if mine:
                # 运行中是活跃执行，绝不触碰（开关热关闭也不得中断在跑编译）；
                # 在飞容器（已弹出、可能正要 start）一并保护
                if state == "running":
                    should_remove = False
                elif pool_on:
                    should_remove = c.id not in inflight and c.id not in queued
                else:
                    # 开关关闭 → 池遗留整体清退（队列内 + 游离残留）
                    should_remove = c.id not in inflight
            else:
                age = now - self._parse_created_at(labels, attrs)
                should_remove = state in ("created", "exited", "dead") and (
                    age > grace_seconds
                )
            if not should_remove:
                continue
            try:
                c.remove(force=True)
                removed += 1
            except Exception as e:  # noqa: BLE001 — 单容器失败不影响其余
                logger.debug("warm pool orphan removal failed: %s", e)
        if removed:
            logger.info(
                "warm pool reconcile removed %d orphan container(s) | replica=%s",
                removed,
                self._replica_id,
            )
        return removed

    # ── 执行 ──────────────────────────────────────────────────
    async def execute(
        self,
        spec: ExecSpec,
        *,
        image: str,
        limits: SandboxLimits,
        grace_seconds: float = 0.0,
        cancel_event: asyncio.Event | None = None,
    ) -> ExecResult:
        """在一次性容器中执行命令；超时强杀容器并标记 timed_out。

        cancel_event 置位时同样强杀容器，但不标记 timed_out——取消是
        调用方的 mid-flight 决策（如 rar 解压超配额），错误语义由调用方
        按自身状态判定；oom 启发式（exit 137）在取消场景不适用。

        warm pool 开启时优先从池中取形状一致的已创建容器直接 start
        （省去 create）；池空或形状漂移时按原路径即时 create（无退化
        路径差异）。执行语义不变：每执行一个 start 一次的全新容器。"""
        started = time.monotonic()
        deadline = started + spec.timeout_seconds + grace_seconds
        timed_out = False
        cancelled = False
        cancel_waiter = (
            asyncio.ensure_future(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        pool_enabled = self._warm_pool_enabled()
        pool_shape = self._shape_for(spec, image, limits) if pool_enabled else None
        pool_cid: str | None = None
        prep_state = "off"
        prep_ms = 0.0
        if pool_enabled:
            self._note_shape(spec.image_kind, pool_shape)
        container = None
        try:
            if pool_shape is not None:
                container = await asyncio.to_thread(
                    self._pool_take, spec.image_kind, pool_shape
                )
            if container is not None:
                pool_cid = getattr(container, "id", None)
                try:
                    await asyncio.to_thread(container.start)
                    prep_state = "hit"
                except Exception as e:  # noqa: BLE001 — 池容器失效回落现路径
                    logger.warning(
                        "warm pool container start failed, falling back to "
                        "fresh create (image=%s): %s",
                        image,
                        e,
                    )
                    self._pool_unregister(pool_cid)
                    pool_cid = None
                    self._discard_async(container)  # 失效容器立即弃置
                    container = None
            if container is None:
                container = await asyncio.to_thread(
                    self._create_and_start, spec, image, limits
                )
                prep_state = "miss" if pool_shape is not None else "off"
            prep_ms = (time.monotonic() - started) * 1000
            with self._pool_guard:
                self._pool_stats[prep_state] += 1
            self._last_prep = (prep_ms, prep_state)
            logger.info(
                "sandbox container ready | pool=%s | prep=%.0fms | image=%s "
                "| cwd=%s | cmd=%s",
                prep_state,
                prep_ms,
                image,
                spec.cwd,
                spec.command,
            )
            # 轮询等待退出；超时或取消先 kill 再收尸，避免容器僵死占用并发槽
            while True:
                state = await asyncio.to_thread(self._read_state, container)
                if state.get("Status") not in ("created", "running"):
                    break
                if time.monotonic() > deadline:
                    logger.warning(
                        "sandbox container timed out after %.1fs, killing: image=%s",
                        time.monotonic() - started,
                        image,
                    )
                    await asyncio.to_thread(container.kill)
                    timed_out = True
                    break
                if cancel_waiter is not None:
                    done, _ = await asyncio.wait(
                        {cancel_waiter}, timeout=_POLL_INTERVAL_SECONDS
                    )
                    if cancel_waiter in done:
                        logger.warning(
                            "sandbox container cancelled mid-flight, killing: "
                            "image=%s",
                            image,
                        )
                        await asyncio.to_thread(container.kill)
                        cancelled = True
                        break
                else:
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            state = await asyncio.to_thread(self._read_state, container)
            raw_logs = await asyncio.to_thread(
                lambda: container.logs(stdout=True, stderr=True)
            )
            stdout, stderr = _decode_pair(_demux_docker_stream(raw_logs))
            exit_code = int(state.get("ExitCode") or 0)
            oom_killed = bool(state.get("OOMKilled")) or (
                exit_code == 137 and not timed_out and not cancelled
            )
            return ExecResult(
                returncode=-1 if timed_out else exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                oom_killed=oom_killed,
                backend="docker",
            )
        except Exception as e:
            if getattr(e, "sandbox_code", None) is not None:
                raise
            raise sandbox_unavailable(
                f"container execution failed (image={image}): {e}"
            ) from e
        finally:
            if cancel_waiter is not None:
                cancel_waiter.cancel()
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception as e:  # noqa: BLE001 — 清理失败仅记录
                    logger.warning("Failed to remove sandbox container: %s", e)
            if pool_cid is not None:
                self._pool_unregister(pool_cid)
            # 执行收尾后异步补充（exec → stop + remove → 异步补充）；
            # 调度失败绝不影响执行结果（finally 内吞异常仅记录）
            if pool_shape is not None and container is not None:
                try:
                    self._schedule_refill(spec.image_kind, pool_shape, image, limits)
                except Exception as e:  # noqa: BLE001
                    logger.debug("warm pool refill scheduling failed: %s", e)

    def _note_shape(self, kind: str, shape: dict | None) -> None:
        with self._pool_guard:
            self._refill_shape[kind] = shape

    # ── 同步实现细节（经 to_thread 调用）───────────────────────
    def _create_and_start(self, spec: ExecSpec, image: str, limits: SandboxLimits):
        client = self._get_client()
        kwargs = self._build_container_kwargs(spec, image, limits)
        # 网络隔离参数名随 SDK 版本变化，按已安装 SDK 的签名选择
        # （避免盲试 TypeError 掩盖真正的参数错误）
        kwargs[self._network_kwarg_name(client)] = "none"
        container = client.containers.create(**kwargs)
        container.start()
        logger.info(
            "sandbox container started | image=%s | cwd=%s | cmd=%s",
            image,
            spec.cwd,
            spec.command,
        )
        return container

    @staticmethod
    def _build_container_kwargs(
        spec: ExecSpec, image: str, limits: SandboxLimits
    ) -> dict:
        from docker.types import Mount

        mounts = [
            Mount(
                target=m.host_path,
                source=m.host_path,
                type="bind",
                read_only=m.read_only,
            )
            for m in spec.effective_mounts()
        ]
        env = dict(spec.env)
        # 只读根文件系统下，工具链的家目录/临时目录统一落到 tmpfs /tmp
        env.setdefault("HOME", "/tmp")
        kwargs: dict = {
            "image": image,
            "command": list(spec.command),
            "detach": True,
            "working_dir": spec.cwd,
            "environment": env,
            "mounts": mounts,
            "mem_limit": f"{limits.memory_mb}M",
            "nano_cpus": int(limits.cpus * 1_000_000_000),
            "pids_limit": limits.pids_limit,
            "tmpfs": {"/tmp": f"size={limits.tmpfs_mb}m"},
            "read_only": True,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
        }
        if os.name == "posix" and hasattr(os, "getuid"):
            # 与宿主后端同 uid:gid 运行，产物属主与本地模式一致
            kwargs["user"] = f"{os.getuid()}:{os.getgid()}"
        return kwargs

    @staticmethod
    def _read_state(container) -> dict:
        container.reload()
        return dict(container.attrs.get("State") or {})

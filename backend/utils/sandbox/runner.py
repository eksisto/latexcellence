"""SandboxRunner：统一执行入口（探测、选后端、并发租约、结果翻译）。

模式语义（config.sandbox.mode，实时读取支持热重载）：
- ``local``：始终本地子进程（开发机无 Docker 的确定性路径）；
- ``auto``：探测到 Docker daemon 即用容器，不可达时回落本地并告警；
- ``docker``：daemon 不可达直接抛 sandbox_unavailable（503），不静默回落。

并发治理：
- 全局并发：进程内跨事件循环安全的计数闸门（max_concurrent_global），
  单/多用户模式均生效（rar 解压等同步核心经 worker 线程独立事件循环
  调用时同样计入）；
- 每用户并发：多用户模式下经 sandbox_slots 表租约（P7 起跨副本生效），
  获取失败抛 sandbox_busy（429）；单用户模式无用户身份，跳过。只读
  轻量命令（git 查询类子命令）经 ``lease_exempt=True`` 豁免租约，
  避免毫秒级查询与重任务争抢每用户唯一槽位造成误伤（429）。
"""

import asyncio
import os
import threading
import time
import weakref

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.sandbox.docker_backend import DockerBackend, docker_sdk_available
from backend.utils.sandbox.errors import sandbox_unavailable
from backend.utils.sandbox.local_backend import execute_local
from backend.utils.sandbox.models import ExecResult, ExecSpec, SandboxLimits

logger = get_logger("sandbox.runner")

# Docker daemon 探测结果的 TTL 缓存（秒）
PROBE_TTL_SECONDS = 60.0

# 全局并发闸门轮询间隔（秒）：仅在全局上限已满的争用场景产生额外等待
_GATE_POLL_INTERVAL_SECONDS = 0.05

# warm pool 孤儿对账周期（秒；启动期先做一轮，之后由本循环接管——
# 每副本职责，不进维护循环选主，见 multi-user-v2-plan §12.1 补记）
POOL_RECONCILE_INTERVAL_SECONDS = 600.0


class _GlobalGate:
    """进程级并发闸门：线程锁保护的计数器 + 异步轮询等待。

    asyncio.Semaphore 与首次使用它的事件循环绑定（跨循环复用抛
    RuntimeError），而沙箱执行存在两类循环来源：服务层主循环，以及
    rar 解压等同步核心在 worker 线程内经 asyncio.run 建立的独立循环。
    ``max_concurrent_global`` 的语义是进程内全部沙箱执行的并发上限，
    必须跨循环统一生效，故以线程安全的计数器实现；等待方轮询重试，
    不占用线程池线程。"""

    def __init__(self) -> None:
        self._count = 0
        self._guard = threading.Lock()

    async def acquire(self, limit: int) -> None:
        while True:
            with self._guard:
                if self._count < limit:
                    self._count += 1
                    return
            await asyncio.sleep(_GATE_POLL_INTERVAL_SECONDS)

    def release(self) -> None:
        with self._guard:
            self._count = max(0, self._count - 1)


# 内置默认镜像名：与 backend/core/config.py SandboxConfig 字段默认值一致。
# 管理员在设置页把镜像名清空保存时按内置默认回落（与三语提示
# "留空使用后端内置默认值"语义一致），而不是让全部命令 503。
_BUILTIN_IMAGES = {
    "tex": "latexcellence-tex:latest",
    "convert": "latexcellence-convert:latest",
    "plot": "latexcellence-plot:latest",
}


class SandboxRunner:
    """全部外部命令执行的唯一入口。"""

    def __init__(self) -> None:
        self._docker = DockerBackend()
        # 探测单飞锁按事件循环隔离（asyncio.Lock 与首次使用它的循环绑定），
        # 探测结果 TTL 缓存本身是跨循环共享的普通数据
        self._probe_gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._probe_ok: bool | None = None
        self._probe_at: float = 0.0
        # bind-mount 预检结果（进程内固化，None=未测）：同绝对路径挂载契约
        # 要求后端与 daemon 共享文件系统视图，后端自身容器化部署时该前提
        # 不成立且只在 create 阶段才暴露——启动期实测一次（见
        # DockerBackend.preflight_mount_check），失败视同不可达。
        self._mount_ok: bool | None = None
        self._warned_fallback = False
        # 全局并发闸门（跨事件循环安全，见 _GlobalGate）
        self._gate = _GlobalGate()
        # 最近一次按镜像类别解析的后端（同步可用性门控读取）
        self._last_backend: dict[str, str] = {}
        # warm pool 周期对账任务（lifespan 循环上创建/取消；None=未启动）
        self._pool_reconcile_task: asyncio.Task | None = None

    def _probe_gate(self) -> asyncio.Lock:
        """当前事件循环的探测单飞锁（惰性创建，随循环回收）。"""
        loop = asyncio.get_running_loop()
        lock = self._probe_gates.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._probe_gates[loop] = lock
        return lock

    # ── 配置 ──────────────────────────────────────────────────
    @staticmethod
    def _sandbox_cfg():
        return get_config().sandbox

    @staticmethod
    def _image_for(kind: str) -> str:
        scfg = get_config().sandbox
        if kind not in _BUILTIN_IMAGES:
            raise sandbox_unavailable(f"no image configured for kind '{kind}'")
        image = {
            "tex": scfg.image_tex,
            "convert": scfg.image_convert,
            "plot": scfg.image_plot,
        }.get(kind, "")
        image = (image or "").strip()
        return image if image else _BUILTIN_IMAGES[kind]

    def _limits(self) -> SandboxLimits:
        scfg = self._sandbox_cfg()
        return SandboxLimits(
            cpus=scfg.cpus,
            memory_mb=scfg.memory_mb,
            pids_limit=scfg.pids_limit,
            tmpfs_mb=scfg.tmpfs_mb,
        )

    # ── 探测与后端选择 ────────────────────────────────────────
    async def probe_docker(self) -> bool:
        """daemon 可达性（TTL 缓存；SDK 缺失 / 非 POSIX 视为不可达）。

        首次探测通过 ping 后追加一次 bind-mount 预检（进程内固化，不随
        TTL 重测——部署拓扑不会在运行期变化）；mode=local 跳过预检。
        """
        async with self._probe_gate():
            now = time.monotonic()
            if self._probe_ok is not None and now - self._probe_at < PROBE_TTL_SECONDS:
                return self._probe_ok
            reachable = bool(os.name == "posix" and docker_sdk_available()) and (
                await self._docker.ping()
            )
            if reachable and self._mount_ok is None:
                if self._sandbox_cfg().mode == "local":
                    self._mount_ok = True  # 本地后端不做容器挂载，视为满足前提
                else:
                    self._mount_ok = await self._docker.preflight_mount_check(
                        self._image_for("tex")
                    )
                    if not self._mount_ok:
                        logger.warning(
                            "sandbox bind-mount preflight failed — the Docker "
                            "daemon cannot see this process's filesystem view "
                            "(containerized backend?). Treating Docker as "
                            "unreachable; mode=auto falls back to local, "
                            "mode=docker will fail fast"
                        )
            if self._mount_ok is False:
                reachable = False
            if reachable != self._probe_ok:
                logger.info(
                    "Docker daemon reachability changed: %s -> %s",
                    self._probe_ok,
                    reachable,
                )
            self._probe_ok = reachable
            self._probe_at = now
            if reachable:
                self._warned_fallback = False
            return reachable

    async def resolve_backend(self, kind: str) -> str:
        """解析指定镜像类别的执行后端（"docker" | "local"）。"""
        mode = self._sandbox_cfg().mode
        if mode == "local":
            self._last_backend[kind] = "local"
            return "local"
        if await self.probe_docker():
            self._last_backend[kind] = "docker"
            return "docker"
        if mode == "auto":
            if not self._warned_fallback:
                logger.warning(
                    "Docker daemon unreachable — falling back to local "
                    "execution (sandbox.mode=auto)"
                )
                self._warned_fallback = True
            self._last_backend[kind] = "local"
            return "local"
        raise sandbox_unavailable(
            "sandbox.mode=docker but the Docker daemon is unreachable "
            "(or the bind-mount preflight failed)"
        )

    def cached_backend(self, kind: str) -> str | None:
        """最近一次 resolve 的后端结果（不触发 I/O）。

        供 pandoc/texfot/latexindent 可用性门控使用：返回 "docker" 表示
        工具在容器内可用（宿主无需安装）；None 表示本进程尚未解析过，
        门控应沿用本地检测逻辑。
        """
        return self._last_backend.get(kind)

    # ── 并发治理 ──────────────────────────────────────────────
    async def _acquire_user_slot(
        self, spec: ExecSpec, user_id: str | None, max_per_user: int, max_global: int
    ) -> str | None:
        """多用户模式获取每用户租约；其余场景返回 None（仅全局信号量）。"""
        if not user_id or not get_config().multi_user.enabled:
            return None
        from backend.core.multiuser import db, sandbox_leases

        if not db.is_initialized():
            # 多用户模式下 DB 属核心基础设施，未就绪应快速失败而非裸奔
            raise RuntimeError(
                "Database not initialized — cannot acquire sandbox lease"
            )
        return await asyncio.to_thread(
            sandbox_leases.acquire,
            user_id,
            spec.timeout_seconds,
            max_global=max_global,
            max_per_user=max_per_user,
        )

    # ── 执行 ──────────────────────────────────────────────────
    async def execute(
        self,
        spec: ExecSpec,
        *,
        user_id: str | None = None,
        lease_exempt: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> ExecResult:
        """执行一次外部命令：选后端 → 并发治理 → 结果归一。

        ``lease_exempt=True`` 豁免每用户并发租约（仍受全局闸门约束），
        供 git 查询类等只读轻量命令使用。

        ``cancel_event`` 置位触发 mid-flight 协作式取消（供调用方在配额
        超限等宿主侧判定后中断执行）：docker 后端强杀容器、local 后端
        击杀进程组，结果为非零退出码且不标记 ``timed_out``——错误语义由
        调用方按自身状态（如超配额）判定。不传保持既有纯超时语义。
        """
        scfg = self._sandbox_cfg()
        backend = await self.resolve_backend(spec.image_kind)
        await self._gate.acquire(scfg.max_concurrent_global)
        slot_id: str | None = None
        try:
            if not lease_exempt:
                slot_id = await self._acquire_user_slot(
                    spec,
                    user_id,
                    max_per_user=scfg.max_concurrent_per_user,
                    max_global=scfg.max_concurrent_global,
                )
            if backend == "docker":
                return await self._docker.execute(
                    spec,
                    image=self._image_for(spec.image_kind),
                    limits=self._limits(),
                    grace_seconds=float(scfg.startup_grace_seconds),
                    cancel_event=cancel_event,
                )
            return await execute_local(spec, cancel_event=cancel_event)
        finally:
            self._gate.release()
            if slot_id:
                from backend.core.multiuser import db, sandbox_leases

                await asyncio.to_thread(sandbox_leases.release, slot_id)

    # ── warm pool 启动期挂载（部署收口阶段三）──────────────────
    async def warm_pool_startup(self) -> None:
        """lifespan 启动期：预热占位容器 + 首轮孤儿对账 + 周期对账任务。

        仅在 warm_pool_enabled 开启且存在解析为 docker 的镜像类别时生效；
        auto 探测未命中 docker（回落 local）与 local/docker 关闭开关的
        部署形态完全不走这里（行为与现网一致）。"""
        scfg = self._sandbox_cfg()
        if not scfg.warm_pool_enabled:
            return
        kinds_images: dict[str, str] = {}
        for kind in ("tex", "convert", "plot"):
            if self._last_backend.get(kind) != "docker":
                continue
            try:
                kinds_images[kind] = self._image_for(kind)
            except Exception:  # noqa: BLE001 — 类别无镜像名则跳过
                continue
        if not kinds_images:
            return
        await self._docker.warm_up(kinds_images, self._limits())
        await self._docker.reconcile_orphans()
        if self._pool_reconcile_task is None or self._pool_reconcile_task.done():
            self._pool_reconcile_task = asyncio.create_task(self._pool_reconcile_loop())
            logger.info(
                "warm pool periodic reconcile scheduled (every %.0fs)",
                POOL_RECONCILE_INTERVAL_SECONDS,
            )

    async def _pool_reconcile_loop(self) -> None:
        """周期对账：清理本副本池孤儿与开关关闭后的池遗留。"""
        while True:
            await asyncio.sleep(POOL_RECONCILE_INTERVAL_SECONDS)
            # 开关关闭时同样继续循环：backend 侧对账规则会清退本副本池
            # 遗留（运行中/在飞执行容器保护至自然收尾），实现配置关闭后
            # 的资源回收
            try:
                await self._docker.reconcile_orphans()
            except Exception as e:  # noqa: BLE001 — 单轮失败不终止循环
                logger.debug("warm pool reconcile failed: %s", e)

    async def close(self) -> None:
        if self._pool_reconcile_task is not None:
            task = self._pool_reconcile_task
            self._pool_reconcile_task = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — 关闭路径尽力而为
                pass
        self._docker.close()


# ── 进程级单例与生命周期 ─────────────────────────────────────
_runner: SandboxRunner | None = None


def get_sandbox_runner() -> SandboxRunner:
    global _runner
    if _runner is None:
        _runner = SandboxRunner()
    return _runner


def reset_sandbox_runner() -> None:
    """重置单例（测试用）。"""
    global _runner
    _runner = None


async def initialize_sandbox_runner() -> None:
    """lifespan 启动钩子：预热 daemon 探测并填充后端解析缓存。

    预热让 auto 模式首个请求即有结论；同时把 tex/convert/plot 三类的
    解析结果写入 cached_backend_for() 门控缓存——宿主不装 pandoc/TeX、
    全靠镜像的部署形态下，首个请求的可用性门控即正确（否则首次转换
    会因读到空缓存而错误要求宿主安装工具）。

    docker 后端生效且 warm pool 开启时，随后执行池预热（每类别一个
    占位容器）与首轮孤儿对账，并挂载周期对账任务（部署收口阶段三）。
    """
    runner = get_sandbox_runner()
    reachable = await runner.probe_docker()
    mode = get_config().sandbox.mode
    if reachable:
        logger.info("Sandbox runtime ready (backend=docker, mode=%s)", mode)
    else:
        logger.info("Sandbox runtime ready (backend=local, mode=%s)", mode)
    for kind in ("tex", "convert", "plot"):
        try:
            await runner.resolve_backend(kind)
        except Exception as e:  # noqa: BLE001 — docker 模式不可达等，留空走本地门控
            logger.debug("sandbox backend warmup skipped for %s: %s", kind, e)
    try:
        await runner.warm_pool_startup()
    except Exception as e:  # noqa: BLE001 — 池预热失败不影响可用性
        logger.warning("Sandbox warm pool startup failed: %s", e)


async def close_sandbox_runner() -> None:
    global _runner
    if _runner is not None:
        await _runner.close()
    _runner = None


def cached_backend_for(kind: str) -> str | None:
    """模块级便捷入口：读取 runner 最近解析结果（见 SandboxRunner.cached_backend）。"""
    if _runner is None:
        return None
    return _runner.cached_backend(kind)


def current_sandbox_user_id() -> str | None:
    """读取多用户上下文的当前用户（供每用户并发租约）；缺失返回 None。

    延迟导入：单用户模式 / 单测环境无需加载多用户模块链。
    """
    try:
        from backend.core.multiuser.userspace import get_current_user_id

        return get_current_user_id()
    except Exception:  # noqa: BLE001 — 上下文缺失时按单用户处理
        return None

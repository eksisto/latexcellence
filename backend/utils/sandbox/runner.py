"""SandboxRunner：统一执行入口（探测、选后端、并发租约、结果翻译）。

模式语义（config.sandbox.mode，实时读取支持热重载）：
- ``local``：始终本地子进程（开发机无 Docker 的确定性路径）；
- ``auto``：探测到 Docker daemon 即用容器，不可达时回落本地并告警；
- ``docker``：daemon 不可达直接抛 sandbox_unavailable（503），不静默回落。

并发治理：
- 全局并发：进程内 asyncio.Semaphore(max_concurrent_global)，单/多用户
  模式均生效；
- 每用户并发：多用户模式下经 sandbox_slots 表租约（P7 起跨副本生效），
  获取失败抛 sandbox_busy（429）；单用户模式无用户身份，跳过。只读
  轻量命令（git 查询类子命令）经 ``lease_exempt=True`` 豁免租约，
  避免毫秒级查询与重任务争抢每用户唯一槽位造成误伤（429）。
"""

import asyncio
import os
import time

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.sandbox.docker_backend import DockerBackend, docker_sdk_available
from backend.utils.sandbox.errors import sandbox_unavailable
from backend.utils.sandbox.local_backend import execute_local
from backend.utils.sandbox.models import ExecResult, ExecSpec, SandboxLimits

logger = get_logger("sandbox.runner")

# Docker daemon 探测结果的 TTL 缓存（秒）
PROBE_TTL_SECONDS = 60.0

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
        self._probe_lock = asyncio.Lock()
        self._probe_ok: bool | None = None
        self._probe_at: float = 0.0
        # bind-mount 预检结果（进程内固化，None=未测）：同绝对路径挂载契约
        # 要求后端与 daemon 共享文件系统视图，后端自身容器化部署时该前提
        # 不成立且只在 create 阶段才暴露——启动期实测一次（见
        # DockerBackend.preflight_mount_check），失败视同不可达。
        self._mount_ok: bool | None = None
        self._warned_fallback = False
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_limit = 0
        # 最近一次按镜像类别解析的后端（同步可用性门控读取）
        self._last_backend: dict[str, str] = {}

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
        async with self._probe_lock:
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
    def _global_semaphore(self, limit: int) -> asyncio.Semaphore:
        if self._semaphore is None or self._semaphore_limit != limit:
            # 配置热重载改变上限时重建（旧的排队中的等待者随旧对象回收）
            self._semaphore = asyncio.Semaphore(limit)
            self._semaphore_limit = limit
        return self._semaphore

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
    ) -> ExecResult:
        """执行一次外部命令：选后端 → 并发治理 → 结果归一。

        ``lease_exempt=True`` 豁免每用户并发租约（仍受全局信号量约束），
        供 git 查询类等只读轻量命令使用。
        """
        scfg = self._sandbox_cfg()
        backend = await self.resolve_backend(spec.image_kind)
        async with self._global_semaphore(scfg.max_concurrent_global):
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
                    )
                return await execute_local(spec)
            finally:
                if slot_id:
                    from backend.core.multiuser import db, sandbox_leases

                    await asyncio.to_thread(sandbox_leases.release, slot_id)

    async def close(self) -> None:
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

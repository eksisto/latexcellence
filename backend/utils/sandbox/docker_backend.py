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
"""

import asyncio
import os
import threading
import time

from backend.core.logger import get_logger
from backend.utils.sandbox.errors import sandbox_unavailable
from backend.utils.sandbox.models import ExecSpec, ExecResult, SandboxLimits

logger = get_logger("sandbox.docker")

# 容器状态轮询间隔（秒）
_POLL_INTERVAL_SECONDS = 0.2


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


class DockerBackend:
    """基于 docker SDK 的执行后端（客户端惰性创建、线程安全）。"""

    def __init__(self) -> None:
        self._client = None
        self._client_lock = threading.Lock()

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

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001 — 关闭失败不影响退出
                    pass
                self._client = None

    # ── 执行 ──────────────────────────────────────────────────
    async def execute(
        self,
        spec: ExecSpec,
        *,
        image: str,
        limits: SandboxLimits,
        grace_seconds: float = 0.0,
    ) -> ExecResult:
        """在一次性容器中执行命令；超时强杀容器并标记 timed_out。"""
        container = None
        started = time.monotonic()
        deadline = started + spec.timeout_seconds + grace_seconds
        timed_out = False
        try:
            container = await asyncio.to_thread(
                self._create_and_start, spec, image, limits
            )
            # 轮询等待退出；超时先 kill 再收尸，避免容器僵死占用并发槽
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
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            state = await asyncio.to_thread(self._read_state, container)
            stdout, stderr = _decode_pair(
                await asyncio.to_thread(
                    lambda: container.logs(stdout=True, stderr=True, demux=True)
                )
            )
            exit_code = int(state.get("ExitCode") or 0)
            oom_killed = bool(state.get("OOMKilled")) or (
                exit_code == 137 and not timed_out
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
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception as e:  # noqa: BLE001 — 清理失败仅记录
                    logger.warning("Failed to remove sandbox container: %s", e)

    # ── 同步实现细节（经 to_thread 调用）───────────────────────
    def _create_and_start(self, spec: ExecSpec, image: str, limits: SandboxLimits):
        client = self._get_client()
        kwargs = self._build_container_kwargs(spec, image, limits)
        try:
            container = client.containers.create(**kwargs, networking_mode="none")
        except TypeError:
            # 兼容旧版 docker SDK：networking_mode 尚未更名
            container = client.containers.create(**kwargs, network_mode="none")
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

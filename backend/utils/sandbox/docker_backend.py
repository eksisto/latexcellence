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
import shutil
import tempfile
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
            raw_logs = await asyncio.to_thread(
                lambda: container.logs(stdout=True, stderr=True)
            )
            stdout, stderr = _decode_pair(_demux_docker_stream(raw_logs))
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

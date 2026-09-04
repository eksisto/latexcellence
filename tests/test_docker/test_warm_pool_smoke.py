"""warm pool 真容器冒烟（marker: docker，无 daemon / 缺镜像自动 skip）。

验收口径对应部署收口阶段三 §4.3：
- 预热生效：启动期占位容器真实创建（label + 副本标识可查）；
- 池命中执行：同形状 ExecSpec 第二次执行复用已创建容器（start 一次），
  结果与现网一致，执行后容器被清理（不留运行/退出残留）；
- 对账清理：本副本孤儿池容器可被 reconcile 清退；
- 开关关闭：不触池、无池容器产生。

全部用例自带清理（teardown 移除本副本全部池容器），不在 daemon 留垃圾。
"""

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from backend.core.config import get_config
from backend.utils.sandbox.docker_backend import (
    WARM_POOL_LABEL,
    WARM_POOL_REPLICA_LABEL,
    DockerBackend,
)
from backend.utils.sandbox.models import ExecSpec, SandboxLimits

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module", autouse=True)
def _ensure_config_loaded():
    """模块级 fixture 先于函数级 conftest 加载，需自行保证配置就绪。"""
    from backend.core.config import load_config

    try:
        get_config()
    except RuntimeError:
        load_config("config.json")


def _daemon_available() -> bool:
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env(timeout=10)
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def _image_exists(image: str) -> bool:
    try:
        import docker as docker_sdk

        docker_sdk.from_env(timeout=10).images.get(image)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def docker_available():
    if not _daemon_available():
        pytest.skip("Docker daemon 不可达，跳过 warm pool 冒烟")
    return True


@pytest.fixture
def pool_env():
    """真实 daemon 上的池环境：启用开关 + 指定镜像 + 用例后清场。"""
    cfg = get_config()
    if not _image_exists(cfg.sandbox.image_tex):
        pytest.skip(f"镜像不存在: {cfg.sandbox.image_tex}")
    original_enabled = cfg.sandbox.warm_pool_enabled
    cfg.sandbox.warm_pool_enabled = True
    backend = DockerBackend()
    yield backend
    cfg.sandbox.warm_pool_enabled = original_enabled
    # 清场：先等在途补充任务退出（延迟触发，避免清场后又落新容器），
    # 对账开启时保护存活池条目，先清空进程内池再全量移除（尽力而为）
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with backend._pool_guard:
                if backend._refill_active == 0:
                    break
            time.sleep(0.1)
        with backend._pool_guard:
            backend._pool.clear()
            backend._inflight.clear()
        backend._reconcile_sync(grace_seconds=0)
        for c in _list_pool_containers(backend._replica_id):
            try:
                c.remove(force=True)
            except Exception:  # noqa: BLE001 — 已不存在
                pass
    finally:
        backend.close()


def _limits() -> SandboxLimits:
    scfg = get_config().sandbox
    return SandboxLimits(
        cpus=scfg.cpus,
        memory_mb=scfg.memory_mb,
        pids_limit=scfg.pids_limit,
        tmpfs_mb=scfg.tmpfs_mb,
    )


def _list_pool_containers(replica_id: str | None = None):
    import docker as docker_sdk

    client = docker_sdk.from_env(timeout=10)
    containers = client.containers.list(
        all=True, filters={"label": f"{WARM_POOL_LABEL}=1"}
    )
    if replica_id is None:
        return containers
    return [
        c
        for c in containers
        if (c.attrs.get("Config") or {}).get("Labels", {}).get(WARM_POOL_REPLICA_LABEL)
        == replica_id
    ]


def _wait_until(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


class TestWarmPoolSmoke:
    def test_warmup_creates_labeled_placeholders(self, docker_available, pool_env):
        """预热生效：占位容器真实创建（无形状、带 label 与副本标识）。"""
        backend = pool_env
        created = asyncio.run(
            backend.warm_up({"tex": get_config().sandbox.image_tex}, _limits())
        )
        assert created == 1
        pool = _list_pool_containers(backend._replica_id)
        assert len(pool) == 1
        attrs = pool[0].attrs
        state = (attrs.get("State") or {}).get("Status")
        assert state == "created"  # 已创建未启动
        # 进程内清空池后对账：占位容器由本副本清退（孤儿语义）
        with backend._pool_guard:
            backend._pool.clear()
        removed = asyncio.run(backend.reconcile_orphans(grace_seconds=0))
        assert removed >= 1
        assert _list_pool_containers(backend._replica_id) == []

    def test_pool_hit_execution_and_cleanup(self, docker_available, pool_env):
        """池命中执行端到端：第二次同形状执行复用池容器，执行后无残留。"""
        backend = pool_env
        work = Path(tempfile.mkdtemp(prefix="lx_warm_pool_smoke_"))
        try:
            spec = ExecSpec(
                command=["sh", "-c", "echo warm-pool-hit"],
                cwd=str(work),
                image_kind="tex",
                timeout_seconds=60,
            )
            # 第一跑：池空 → 即时 create（miss），随后异步补充按形状克隆
            res1 = asyncio.run(
                backend.execute(
                    spec, image=get_config().sandbox.image_tex, limits=_limits()
                )
            )
            assert res1.returncode == 0
            assert backend._pool_stats["miss"] == 1
            assert _wait_until(
                lambda: len(backend._pool.get("tex", ())) > 0, timeout=30
            ), "补充任务未按最近形状创建池容器"
            pooled_id = backend._pool["tex"][0].container.id

            # 第二跑：池命中 → 复用已创建容器（start 一次）执行
            res2 = asyncio.run(
                backend.execute(
                    spec, image=get_config().sandbox.image_tex, limits=_limits()
                )
            )
            assert res2.returncode == 0
            assert "warm-pool-hit" in res2.stdout
            assert backend._pool_stats["hit"] == 1
            # 执行后容器被清理：池容器已 remove，不留运行/退出残留
            remaining_ids = {c.id for c in _list_pool_containers(backend._replica_id)}
            assert pooled_id not in remaining_ids
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_disabled_bypasses_pool(self, docker_available, pool_env, monkeypatch):
        """开关关闭：执行走现网路径，不产生任何池容器。"""
        cfg = get_config()
        cfg.sandbox.warm_pool_enabled = False
        backend = pool_env
        work = Path(tempfile.mkdtemp(prefix="lx_warm_pool_off_"))
        try:
            spec = ExecSpec(
                command=["true"],
                cwd=str(work),
                image_kind="tex",
                timeout_seconds=60,
            )
            res = asyncio.run(
                backend.execute(spec, image=cfg.sandbox.image_tex, limits=_limits())
            )
            assert res.returncode == 0
            assert backend._pool_stats["off"] == 1
            assert _list_pool_containers(backend._replica_id) == []
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_reconcile_removes_orphan_pool_container(self, docker_available, pool_env):
        """对账清退本副本孤儿：真实 daemon 上创建后由 reconcile 移除。"""
        backend = pool_env
        limits = _limits()
        shape = backend._shape_for(
            ExecSpec(command=["true"], cwd="/tmp"),
            get_config().sandbox.image_tex,
            limits,
        )
        orphan = backend._pool_create(shape)
        try:
            assert any(
                c.id == orphan.id for c in _list_pool_containers(backend._replica_id)
            )
            removed = asyncio.run(backend.reconcile_orphans(grace_seconds=0))
            assert removed >= 1
            assert not any(
                c.id == orphan.id for c in _list_pool_containers(backend._replica_id)
            )
        finally:
            try:
                orphan.remove(force=True)
            except Exception:  # noqa: BLE001 — 已被对账移除
                pass

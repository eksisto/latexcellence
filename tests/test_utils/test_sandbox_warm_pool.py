"""warm pool（容器预创建池）hermetic 单测（部署收口阶段三）。

覆盖 docker 后端池路径的核心语义（fake docker client，零 daemon I/O）：
- 开关关闭：execute 与现网逐字段一致（不触池、容器无 label、无补充）；
- 池空未命中 → 即时 create（无退化路径差异）+ 形状记账；
- 补充任务按最近形状克隆容器（label / 副本标识 / 安全基线一致），
  池深封顶 max_concurrent_global，形状漂移时放弃（不制造死容器垃圾）；
- 池命中：start 一次的全新容器执行、执行后清理、在飞集合登记/注销；
- 形状不一致（含启动期占位容器）出队即弃、启动失败回落现路径；
- 孤儿对账：本副本存活集合保护 / 本副本遗留清退 / 外来容器按年龄与状态；
- runner 侧 warm_pool_startup 挂载（预热 + 首轮对账 + 周期任务）。
"""

import time
from types import SimpleNamespace

import pytest

from backend.utils.sandbox import docker_backend as db_mod
from backend.utils.sandbox.docker_backend import (
    MAX_CONCURRENT_REFILLS,
    WARM_POOL_CREATED_AT_LABEL,
    WARM_POOL_LABEL,
    WARM_POOL_REPLICA_LABEL,
    DockerBackend,
)
from backend.utils.sandbox.models import ExecSpec, SandboxLimits


def _spec(**kwargs) -> ExecSpec:
    kwargs.setdefault("command", ["echo", "hi"])
    kwargs.setdefault("cwd", "/workspace/proj")
    kwargs.setdefault("timeout_seconds", 30)
    return ExecSpec(**kwargs)


_LIMITS = SandboxLimits(cpus=1.0, memory_mb=256, pids_limit=64, tmpfs_mb=64)
_IMAGE = "tex-img:1"


class FakeContainer:
    """fake 容器：记录创建 kwargs，支持 start/remove/reload/logs/kill。"""

    _seq = 0

    def __init__(self, create_kwargs=None, *, fail_on_start=False):
        FakeContainer._seq += 1
        self.id = f"container-{FakeContainer._seq}"
        self.create_kwargs = create_kwargs or {}
        self.fail_on_start = fail_on_start
        self.started = False
        self.removed = False
        self.killed = False
        # 模拟 daemon inspect 形状：State + Config.Labels（对账读取口径）
        self.attrs = {
            "State": {"Status": "created", "OOMKilled": False},
            "Config": {
                "Labels": dict(self.create_kwargs.get("labels") or {}),
            },
        }

    def start(self):
        if self.fail_on_start:
            raise RuntimeError("simulated start failure")
        self.started = True
        self.attrs["State"]["Status"] = "exited"
        self.attrs["State"]["ExitCode"] = 0

    def reload(self):
        pass

    def kill(self):
        self.killed = True
        self.attrs["State"]["Status"] = "exited"
        self.attrs["State"]["ExitCode"] = 137

    def logs(self, stdout=True, stderr=True):
        return b""

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self):
        self.created: list[FakeContainer] = []

    def create(self, **kwargs):
        c = FakeContainer(kwargs)
        self.created.append(c)
        return c

    def list(self, all=True, filters=None):
        assert filters and filters.get("label") == f"{WARM_POOL_LABEL}=1"
        return [c for c in self.created if not c.removed]


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()


def _install_cfg(monkeypatch, *, enabled=True, depth=4):
    sandbox = SimpleNamespace(
        mode="docker",
        image_tex=_IMAGE,
        image_convert="c",
        image_plot="p",
        cpus=1.0,
        memory_mb=256,
        pids_limit=64,
        tmpfs_mb=64,
        max_concurrent_global=depth,
        max_concurrent_per_user=1,
        startup_grace_seconds=1,
        warm_pool_enabled=enabled,
    )
    monkeypatch.setattr(db_mod, "get_config", lambda: SimpleNamespace(sandbox=sandbox))
    return sandbox


@pytest.fixture
def env(monkeypatch):
    """构造带 fake client 的后端；补充任务延迟放大到测试生命周期之外，
    需要补充结果的用例直接同步驱动 _refill_worker(delay=0) 保证确定性。
    池深默认 1：单次补充恰好创建一个容器，便于逐条断言。"""
    _install_cfg(monkeypatch, enabled=True, depth=1)
    # 调度出去的补充线程在测试内绝不执行（延迟 3600s）
    monkeypatch.setattr(db_mod, "REFILL_DELAY_SECONDS", 3600.0)
    backend = DockerBackend()
    client = FakeClient()
    monkeypatch.setattr(backend, "_get_client", lambda: client)
    return backend, client


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class TestDisabledPassthrough:
    @pytest.mark.asyncio
    async def test_disabled_executes_without_pool(self, env, monkeypatch):
        backend, client = env
        _install_cfg(monkeypatch, enabled=False)
        res = await backend.execute(_spec(), image=_IMAGE, limits=_LIMITS)
        assert res.returncode == 0
        assert res.backend == "docker"
        # 执行容器走即时 create：无 label、不入池、无补充任务
        assert len(client.containers.created) == 1
        assert "labels" not in client.containers.created[0].create_kwargs
        assert backend._pool_stats["off"] == 1
        assert not backend._pool
        assert backend._refill_active == 0

    @pytest.mark.asyncio
    async def test_disabled_pool_put_rejected_by_reconcile(self, env, monkeypatch):
        """开关关闭后对账把本副本池容器整体清退。"""
        backend, client = env
        _install_cfg(monkeypatch, enabled=True)
        await backend.warm_up({"tex": _IMAGE}, _LIMITS)
        assert len(backend._pool.get("tex", ())) == 1
        _install_cfg(monkeypatch, enabled=False)
        removed = await backend.reconcile_orphans(grace_seconds=0)
        assert removed == 1
        assert all(c.removed for c in client.containers.created)


class TestPoolMissThenHit:
    @pytest.mark.asyncio
    async def test_empty_pool_creates_immediately(self, env):
        backend, client = env
        res = await backend.execute(_spec(), image=_IMAGE, limits=_LIMITS)
        assert res.returncode == 0
        assert backend._pool_stats["miss"] == 1
        # 即时 create 路径不带池 label（执行容器不是池容器）
        assert "labels" not in client.containers.created[0].create_kwargs
        assert backend._last_prep is not None and backend._last_prep[1] == "miss"

    @pytest.mark.asyncio
    async def test_refill_clones_last_shape_with_labels(self, env):
        backend, client = env
        spec = _spec()
        shape = backend._shape_for(spec, _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        assert len(backend._pool["tex"]) == 1
        pool_kwargs = client.containers.created[0].create_kwargs
        # 安全基线与即时 create 逐字段一致
        for key in (
            "read_only",
            "cap_drop",
            "security_opt",
            "mem_limit",
            "nano_cpus",
            "pids_limit",
            "tmpfs",
            "working_dir",
            "environment",
            "command",
        ):
            assert pool_kwargs[key] == shape[key], key
        assert pool_kwargs["labels"][WARM_POOL_LABEL] == "1"
        assert pool_kwargs["labels"][WARM_POOL_REPLICA_LABEL] == backend._replica_id
        assert WARM_POOL_CREATED_AT_LABEL in pool_kwargs["labels"]

    @pytest.mark.asyncio
    async def test_second_identical_execution_hits_pool(self, env):
        backend, client = env
        spec = _spec()
        shape = backend._shape_for(spec, _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        pooled = client.containers.created[0]

        res = await backend.execute(spec, image=_IMAGE, limits=_LIMITS)
        assert res.returncode == 0
        assert backend._pool_stats["hit"] == 1
        assert pooled.started  # 池容器 start 一次即执行
        assert pooled.removed  # 执行后清理
        assert not backend._inflight  # 在飞集合注销
        assert len(client.containers.created) == 1  # 未再即时 create
        assert backend._last_prep[1] == "hit"

    @pytest.mark.asyncio
    async def test_inflight_registered_while_running(self, env):
        """执行期间池容器登记在飞集合（对账保护窗口）。"""
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        seen_inflight = []

        class SlowContainer(FakeContainer):
            def start(self):
                seen_inflight.append(set(backend._inflight))
                super().start()

        client.containers.created[0] = SlowContainer(
            client.containers.created[0].create_kwargs
        )
        # 池内条目引用需同步替换为新容器对象
        backend._pool["tex"].clear()
        backend._pool_put("tex", client.containers.created[0], shape)
        await backend.execute(_spec(), image=_IMAGE, limits=_LIMITS)
        assert seen_inflight and list(seen_inflight[0])[0].startswith("container-")
        assert not backend._inflight


class TestRefillPolicy:
    @pytest.mark.asyncio
    async def test_refill_caps_at_depth(self, env, monkeypatch):
        backend, client = env
        _install_cfg(monkeypatch, enabled=True, depth=4)
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        for _ in range(5):
            backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        assert len(backend._pool["tex"]) == 4  # 池深 = max_concurrent_global

    @pytest.mark.asyncio
    async def test_refill_aborts_on_shape_drift(self, env):
        backend, client = env
        shape_a = backend._shape_for(_spec(command=["echo", "a"]), _IMAGE, _LIMITS)
        shape_b = backend._shape_for(_spec(command=["echo", "b"]), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape_a)
        backend._refill_worker("tex", shape_b, _IMAGE, _LIMITS, delay=0)
        assert not client.containers.created  # 形状已漂移：不制造容器

    @pytest.mark.asyncio
    async def test_limits_change_breaks_shape(self, env, monkeypatch):
        """资源限额热重载后形状失配：池容器不复用（保守正确）。"""
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        hot_limits = SandboxLimits(cpus=2.0, memory_mb=512, pids_limit=64, tmpfs_mb=64)
        res = await backend.execute(_spec(), image=_IMAGE, limits=hot_limits)
        assert res.returncode == 0
        assert backend._pool_stats["hit"] == 0  # 失配不命中
        assert backend._pool_stats["miss"] == 1
        assert _wait_until(lambda: client.containers.created[0].removed)

    def test_refill_concurrency_capped(self, env, monkeypatch):
        backend, _client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_active = MAX_CONCURRENT_REFILLS
        backend._schedule_refill("tex", shape, _IMAGE, _LIMITS)
        # 达到并发上限：放弃调度（不排队、不递增计数）
        assert backend._refill_active == MAX_CONCURRENT_REFILLS


class TestShapeMismatchAndFallback:
    @pytest.mark.asyncio
    async def test_placeholder_discarded_on_first_execute(self, env):
        """启动期占位容器（shape=None）不参与执行：首次执行弃置并即时 create。"""
        backend, client = env
        await backend.warm_up({"tex": _IMAGE}, _LIMITS)
        placeholder = client.containers.created[0]
        res = await backend.execute(_spec(), image=_IMAGE, limits=_LIMITS)
        assert res.returncode == 0
        assert backend._pool_stats["miss"] == 1
        assert _wait_until(lambda: placeholder.removed)  # 占位容器出队即弃
        assert not placeholder.started  # 从未启动

    @pytest.mark.asyncio
    async def test_pool_start_failure_falls_back_to_fresh_create(self, env):
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        client.containers.created[0].fail_on_start = True

        res = await backend.execute(_spec(), image=_IMAGE, limits=_LIMITS)
        assert res.returncode == 0  # 回落后照常执行
        assert len(client.containers.created) == 2  # 失效容器 + 新建容器
        assert backend._pool_stats["hit"] == 0
        assert backend._pool_stats["miss"] == 1
        assert not backend._inflight


class TestReconcile:
    def _make_orphan(self, backend, client, *, replica, status, age_seconds):
        labels = {
            WARM_POOL_LABEL: "1",
            WARM_POOL_REPLICA_LABEL: replica,
            WARM_POOL_CREATED_AT_LABEL: f"{time.time() - age_seconds:.3f}",
        }
        kwargs = {
            "image": _IMAGE,
            "labels": labels,
            "command": ["true"],
            "network_mode": "none",
        }
        c = FakeContainer(kwargs)
        c.attrs["State"]["Status"] = status
        client.containers.created.append(c)
        return c

    @pytest.mark.asyncio
    async def test_reconcile_rules(self, env):
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        live_pooled = client.containers.created[0]

        mine_stray = self._make_orphan(
            backend,
            client,
            replica=backend._replica_id,
            status="created",
            age_seconds=0,
        )
        foreign_old_created = self._make_orphan(
            backend, client, replica="other:aa", status="created", age_seconds=3600
        )
        foreign_fresh_created = self._make_orphan(
            backend, client, replica="other:bb", status="created", age_seconds=10
        )
        foreign_running = self._make_orphan(
            backend, client, replica="other:cc", status="running", age_seconds=3600
        )
        foreign_exited = self._make_orphan(
            backend, client, replica="other:dd", status="exited", age_seconds=3600
        )

        removed = await backend.reconcile_orphans(
            grace_seconds=db_mod.ORPHAN_GRACE_SECONDS
        )
        # 本副本存活集合内的池容器保留；本副本遗留清退；
        # 外来 created/exited 超龄清退、未超龄与运行中保留
        assert not live_pooled.removed
        assert mine_stray.removed
        assert foreign_old_created.removed
        assert not foreign_fresh_created.removed
        assert not foreign_running.removed
        assert foreign_exited.removed
        assert removed == 3

    @pytest.mark.asyncio
    async def test_reconcile_never_touches_running_own_container(self, env):
        """执行中的本副本池容器（在飞）绝不被对账误杀。"""
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        executing = client.containers.created[0]
        executing.attrs["State"]["Status"] = "running"
        with backend._pool_guard:
            backend._inflight.add(executing.id)
        await backend.reconcile_orphans(grace_seconds=0)
        assert not executing.removed

    @pytest.mark.asyncio
    async def test_reconcile_pool_off_spares_running_and_inflight(
        self, env, monkeypatch
    ):
        """开关热关闭：对账清退池遗留，但运行中/在飞执行容器绝不误杀。

        回归：关闭分支曾无条件清退本副本全部池容器（不看状态与在飞
        集合），配置关闭叠加周期对账时可强删正在执行的容器、中断在跑
        编译。"""
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        queued = client.containers.created[0]  # 池内遗留 → 清退

        running = self._make_orphan(
            backend,
            client,
            replica=backend._replica_id,
            status="running",
            age_seconds=0,
        )
        inflight_created = self._make_orphan(
            backend,
            client,
            replica=backend._replica_id,
            status="created",
            age_seconds=0,
        )
        exited = self._make_orphan(
            backend,
            client,
            replica=backend._replica_id,
            status="exited",
            age_seconds=0,
        )
        with backend._pool_guard:
            backend._inflight.add(inflight_created.id)

        _install_cfg(monkeypatch, enabled=False)
        removed = await backend.reconcile_orphans(grace_seconds=0)
        assert queued.removed  # 池内遗留照常清退
        assert exited.removed  # 游离退出残留照常清退
        assert not running.removed  # 运行中执行绝不触碰
        assert not inflight_created.removed  # 在飞容器（可能正要 start）保护
        assert removed == 2

    @pytest.mark.asyncio
    async def test_pool_take_registers_inflight_atomically(self, env):
        """弹出与在飞登记在同一临界区内完成：锁释放瞬间容器必已受对账
        保护（回归：pop 与登记曾分两次加锁，窗口内并发对账可误杀刚弹出
        的容器，致执行退化走即时 create）。"""
        backend, client = env
        shape = backend._shape_for(_spec(), _IMAGE, _LIMITS)
        backend._note_shape("tex", shape)
        backend._refill_worker("tex", shape, _IMAGE, _LIMITS, delay=0)
        target = client.containers.created[0]

        violations: list[str] = []
        real_guard = backend._pool_guard

        class CheckingLock:
            """临界区退出哨兵：池已空且目标容器未移除时，必须已在飞。"""

            def __enter__(self):
                real_guard.acquire()

            def __exit__(self, *exc):
                if (
                    not backend._pool.get("tex")
                    and not target.removed
                    and target.id not in backend._inflight
                ):
                    violations.append(target.id)
                real_guard.release()

        backend._pool_guard = CheckingLock()
        try:
            taken = backend._pool_take("tex", shape)
        finally:
            backend._pool_guard = real_guard
        assert taken is target
        assert not violations
        assert target.id in backend._inflight


class TestWarmUp:
    @pytest.mark.asyncio
    async def test_placeholder_binds_no_host_path(self, env):
        backend, client = env
        created = await backend.warm_up({"tex": _IMAGE, "plot": "p"}, _LIMITS)
        assert created == 2
        for c in client.containers.created:
            assert c.create_kwargs["labels"][WARM_POOL_LABEL] == "1"
            assert c.create_kwargs["mounts"] == []  # 不绑定任何宿主路径
            assert c.create_kwargs["read_only"] is True
            assert c.create_kwargs["working_dir"] == "/tmp"
        assert all(entry.shape is None for entry in backend._pool["tex"])

    @pytest.mark.asyncio
    async def test_placeholder_create_failure_skips_kind(self, env, monkeypatch):
        backend, client = env
        monkeypatch.setattr(
            backend,
            "_create_placeholder",
            lambda image, limits: (_ for _ in ()).throw(RuntimeError("no image")),
        )
        created = await backend.warm_up({"tex": _IMAGE}, _LIMITS)
        assert created == 0
        assert not backend._pool.get("tex")


class TestWarmPoolStartup:
    @staticmethod
    def _runner(monkeypatch, *, enabled, mode="docker"):
        from backend.utils.sandbox.runner import SandboxRunner

        sandbox = SimpleNamespace(
            mode=mode,
            image_tex=_IMAGE,
            image_convert="c",
            image_plot="p",
            cpus=1.0,
            memory_mb=256,
            pids_limit=64,
            tmpfs_mb=64,
            max_concurrent_global=2,
            max_concurrent_per_user=1,
            startup_grace_seconds=1,
            warm_pool_enabled=enabled,
        )
        monkeypatch.setattr(
            "backend.utils.sandbox.runner.get_config",
            lambda: SimpleNamespace(sandbox=sandbox),
        )
        runner = SandboxRunner()

        class FakeDocker:
            def __init__(self):
                self.warm_calls = []
                self.reconcile_calls = []

            async def ping(self):
                return True

            async def preflight_mount_check(self, image):
                return True

            async def warm_up(self, kinds_images, limits):
                self.warm_calls.append(dict(kinds_images))

            async def reconcile_orphans(self, grace_seconds=900.0):
                self.reconcile_calls.append(grace_seconds)

            def close(self):
                pass

        runner._docker = FakeDocker()
        return runner, runner._docker

    @pytest.mark.asyncio
    async def test_startup_warms_and_schedules_reconcile(self, monkeypatch):
        runner, fake = self._runner(monkeypatch, enabled=True)
        for kind in ("tex", "convert", "plot"):
            await runner.resolve_backend(kind)
        try:
            await runner.warm_pool_startup()
            assert fake.warm_calls == [{"tex": _IMAGE, "convert": "c", "plot": "p"}]
            assert len(fake.reconcile_calls) == 1
            assert runner._pool_reconcile_task is not None
            assert not runner._pool_reconcile_task.done()
        finally:
            await runner.close()
        assert runner._pool_reconcile_task is None

    @pytest.mark.asyncio
    async def test_startup_skipped_when_disabled(self, monkeypatch):
        runner, fake = self._runner(monkeypatch, enabled=False)
        for kind in ("tex", "convert", "plot"):
            await runner.resolve_backend(kind)
        await runner.warm_pool_startup()
        assert fake.warm_calls == []
        assert fake.reconcile_calls == []
        assert runner._pool_reconcile_task is None

    @pytest.mark.asyncio
    async def test_startup_skipped_when_local_backend(self, monkeypatch):
        runner, fake = self._runner(monkeypatch, enabled=True, mode="local")
        # mode=local 时 resolve_backend 不触 docker，last_backend 全 local
        for kind in ("tex", "convert", "plot"):
            await runner.resolve_backend(kind)
        await runner.warm_pool_startup()
        assert fake.warm_calls == []
        assert runner._pool_reconcile_task is None

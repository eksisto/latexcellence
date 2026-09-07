"""SandboxRunner 契约测试：模式选择、探测回落、错误翻译、并发治理。

全部使用 fake 后端（不发起真实 docker I/O）；真容器冒烟见 test_docker/。
"""

import asyncio

import pytest

from backend.core.config import get_config
from backend.utils.sandbox import ExecSpec, make_sandbox_error, sandbox_code_of
from backend.utils.sandbox.models import ExecResult
from backend.utils.sandbox.runner import (
    PROBE_TTL_SECONDS,
    SandboxRunner,
    current_sandbox_user_id,
)


def _spec(**kwargs) -> ExecSpec:
    kwargs.setdefault("command", ["echo", "hi"])
    kwargs.setdefault("cwd", "/tmp")
    return ExecSpec(**kwargs)


def _result(**kwargs) -> ExecResult:
    kwargs.setdefault("returncode", 0)
    kwargs.setdefault("stdout", "")
    kwargs.setdefault("stderr", "")
    kwargs.setdefault("backend", "local")
    return ExecResult(**kwargs)


@pytest.fixture
def cfg():
    config = get_config()
    yield config
    # 还原默认，避免污染其他测试
    config.sandbox.mode = "auto"
    config.multi_user.enabled = False


def _fake_docker(runner: SandboxRunner, *, reachable: bool, result=None):
    """替换 runner 的 docker 后端为 fake（记录调用参数）。"""
    calls: dict = {}

    async def fake_ping():
        return reachable

    async def fake_preflight_mount_check(image):
        # 挂载预检默认通过（预检失败分支在 test_sandbox_docker_backend.py）
        return True

    async def fake_execute(
        spec, *, image, limits, grace_seconds=0.0, cancel_event=None
    ):
        calls["image"] = image
        calls["limits"] = limits
        calls["grace"] = grace_seconds
        calls["spec"] = spec
        calls["cancel_event"] = cancel_event
        return result or _result(backend="docker")

    runner._docker = type(
        "FakeDocker",
        (),
        {
            "ping": staticmethod(fake_ping),
            "preflight_mount_check": staticmethod(fake_preflight_mount_check),
            "execute": staticmethod(fake_execute),
        },
    )()
    return calls


class TestModeSelection:
    @pytest.mark.asyncio
    async def test_local_mode_always_local(self, cfg):
        cfg.sandbox.mode = "local"
        runner = SandboxRunner()
        assert await runner.resolve_backend("tex") == "local"

    @pytest.mark.asyncio
    async def test_auto_without_sdk_falls_back_to_local(self, cfg, monkeypatch):
        # 无 docker SDK / 非 POSIX：ping 短路为不可达 → auto 回落 local
        monkeypatch.setattr(
            "backend.utils.sandbox.runner.docker_sdk_available", lambda: False
        )
        cfg.sandbox.mode = "auto"  # 显式声明：conftest 基线强制 local
        runner = SandboxRunner()
        assert await runner.resolve_backend("tex") == "local"

    @pytest.mark.asyncio
    async def test_docker_mode_unreachable_raises(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "backend.utils.sandbox.runner.docker_sdk_available", lambda: False
        )
        cfg.sandbox.mode = "docker"
        runner = SandboxRunner()
        with pytest.raises(RuntimeError) as exc_info:
            await runner.resolve_backend("tex")
        assert sandbox_code_of(exc_info.value) == "sandbox_unavailable"

    @pytest.mark.asyncio
    async def test_docker_reachable_resolves_docker(self, cfg):
        cfg.sandbox.mode = "auto"  # 显式声明：conftest 基线强制 local
        runner = SandboxRunner()
        _fake_docker(runner, reachable=True)
        assert await runner.resolve_backend("convert") == "docker"
        assert runner.cached_backend("convert") == "docker"


class TestExecuteRouting:
    @pytest.mark.asyncio
    async def test_local_execution_passthrough(self, cfg, monkeypatch):
        cfg.sandbox.mode = "local"
        expected = _result(returncode=3, stdout="out", stderr="err")
        seen: dict = {}

        async def fake_execute_local(spec, *, cancel_event=None):
            seen["spec"] = spec
            seen["cancel_event"] = cancel_event
            return expected

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.execute_local", fake_execute_local
        )
        runner = SandboxRunner()
        res = await runner.execute(_spec(timeout_seconds=7))
        assert res is expected
        assert seen["spec"].timeout_seconds == 7
        assert seen["cancel_event"] is None

    @pytest.mark.asyncio
    async def test_cancel_event_passthrough(self, cfg, monkeypatch):
        """cancel_event 契约：原样透传给所选后端（Q4 方案 A）。"""
        cfg.sandbox.mode = "local"
        seen: dict = {}

        async def fake_execute_local(spec, *, cancel_event=None):
            seen["cancel_event"] = cancel_event
            return _result()

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.execute_local", fake_execute_local
        )
        runner = SandboxRunner()
        event = asyncio.Event()
        await runner.execute(_spec(), cancel_event=event)
        assert seen["cancel_event"] is event

    @pytest.mark.asyncio
    async def test_global_gate_bounds_cross_loop_concurrency(self, cfg, monkeypatch):
        """全局并发闸门跨事件循环生效（rar 同步核心经独立循环调用 execute）。

        asyncio.Semaphore 与循环绑定，闸门必须是进程级：worker 循环内的
        执行同样计入 max_concurrent_global。"""
        cfg.sandbox.mode = "local"
        cfg.sandbox.max_concurrent_global = 1

        async def fake_execute_local(spec, *, cancel_event=None):
            await asyncio.sleep(0.05)
            return _result()

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.execute_local", fake_execute_local
        )
        runner = SandboxRunner()

        async def run_in_worker_loop():
            loop = asyncio.new_event_loop()
            try:
                return await asyncio.to_thread(
                    loop.run_until_complete, runner.execute(_spec())
                )
            finally:
                loop.close()

        # 同一时刻两个循环各跑一个执行：上限=1 时必须串行（都成功但不并发）
        import time as _time

        start = _time.monotonic()
        await asyncio.gather(run_in_worker_loop(), runner.execute(_spec()))
        elapsed = _time.monotonic() - start
        assert elapsed >= 0.09  # 串行化：总耗时 ≥ 2 × 单次执行时长

    @pytest.mark.asyncio
    async def test_docker_uses_configured_image_and_limits(self, cfg):
        cfg.sandbox.mode = "docker"
        cfg.sandbox.cpus = 2.0
        cfg.sandbox.memory_mb = 2048
        oom = _result(returncode=137, oom_killed=True, backend="docker")
        runner = SandboxRunner()
        calls = _fake_docker(runner, reachable=True, result=oom)

        res = await runner.execute(
            _spec(image_kind="plot", timeout_seconds=10), user_id="u1"
        )
        assert res.oom_killed is True  # OOM 标志原样透传给上层
        assert calls["image"] == cfg.sandbox.image_plot
        assert calls["limits"].cpus == 2.0
        assert calls["limits"].memory_mb == 2048
        assert calls["grace"] == float(cfg.sandbox.startup_grace_seconds)

    @pytest.mark.asyncio
    async def test_docker_mode_unavailable_propagates_tagged_error(self, cfg):
        cfg.sandbox.mode = "docker"
        runner = SandboxRunner()

        class FailingDocker:
            async def ping(self):
                return False

        runner._docker = FailingDocker()
        with pytest.raises(RuntimeError) as exc_info:
            await runner.execute(_spec())
        assert sandbox_code_of(exc_info.value) == "sandbox_unavailable"


class TestProbeCache:
    @pytest.mark.asyncio
    async def test_probe_result_cached_within_ttl(self, cfg):
        runner = SandboxRunner()
        flipper = _fake_docker(runner, reachable=True)

        first = await runner.probe_docker()
        assert first is True
        # 翻转可达性后仍应命中 TTL 缓存
        flipper2 = _fake_docker(runner, reachable=False)
        assert await runner.probe_docker() is True
        assert flipper is not flipper2

    @pytest.mark.asyncio
    async def test_probe_cache_expires_after_ttl(self, cfg, monkeypatch):
        runner = SandboxRunner()
        _fake_docker(runner, reachable=True)
        assert await runner.probe_docker() is True

        # 时间快进越过 TTL
        real_monotonic = __import__("time").monotonic
        monkeypatch.setattr(
            "backend.utils.sandbox.runner.time.monotonic",
            lambda: real_monotonic() + PROBE_TTL_SECONDS + 1,
        )

        _fake_docker(runner, reachable=False)
        assert await runner.probe_docker() is False


class TestUserSlot:
    @pytest.mark.asyncio
    async def test_single_user_skips_lease(self, cfg, monkeypatch):
        cfg.multi_user.enabled = False
        cfg.sandbox.mode = "local"

        acquired: list = []

        def fake_acquire(*a, **k):  # pragma: nocover - 不应被调用
            acquired.append(a)

        monkeypatch.setattr(
            "backend.core.multiuser.sandbox_leases.acquire", fake_acquire
        )
        runner = SandboxRunner()
        await runner.execute(_spec(), user_id="someone")
        assert acquired == []

    @pytest.mark.asyncio
    async def test_multiuser_requires_initialized_db(self, cfg):
        cfg.multi_user.enabled = True
        cfg.sandbox.mode = "local"
        runner = SandboxRunner()
        # 未 init_db 时快速失败而非裸奔执行
        with pytest.raises(RuntimeError, match="Database not initialized"):
            await runner.execute(_spec(), user_id="u1")

    @pytest.mark.asyncio
    async def test_lease_exempt_skips_user_lease(self, cfg, monkeypatch):
        """lease_exempt=True 豁免每用户租约（git 查询类只读命令）。

        常规多用户执行在 DB 未初始化时快速失败；豁免路径完全不触租约，
        因此 DB 未就绪也能正常执行，以此证明租约环节被跳过。
        """
        cfg.multi_user.enabled = True
        cfg.sandbox.mode = "local"

        async def fake_execute_local(spec, *, cancel_event=None):
            assert cancel_event is None
            return _result()

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.execute_local", fake_execute_local
        )
        runner = SandboxRunner()
        res = await runner.execute(_spec(), user_id="u1", lease_exempt=True)
        assert res.returncode == 0


class TestLocalBackendCancel:
    """local 后端协作式取消（Q4 方案 A）：真实子进程（sleep）验证。"""

    @pytest.mark.asyncio
    async def test_cancel_event_kills_process(self, cfg):
        """cancel_event 置位 → 进程组被击杀、非零退出、不标记 timed_out。"""
        import os

        if os.name != "posix":
            pytest.skip("进程组取消语义仅 POSIX")
        cfg.sandbox.mode = "local"
        runner = SandboxRunner()
        cancel_event = asyncio.Event()
        spec = _spec(command=["sleep", "30"], timeout_seconds=60)
        task = asyncio.ensure_future(runner.execute(spec, cancel_event=cancel_event))
        await asyncio.sleep(0.4)  # 等 sleep 进程启动并进入等待
        cancel_event.set()
        res = await asyncio.wait_for(task, timeout=10)
        assert res.returncode != 0
        assert res.timed_out is False

    @pytest.mark.asyncio
    async def test_no_cancel_keeps_timeout_semantics(self, cfg):
        """不传 cancel_event：既有纯超时语义回归（timed_out=True）。"""
        cfg.sandbox.mode = "local"
        runner = SandboxRunner()
        res = await runner.execute(_spec(command=["sleep", "30"], timeout_seconds=0.3))
        assert res.timed_out is True
        assert res.returncode != 0

    @pytest.mark.asyncio
    async def test_no_cancel_path_keeps_inline_wait_for(self, cfg, monkeypatch):
        """回归（阶段一评审修复）：无 cancel_event 的既有路径不派生独立
        communicate 任务——asyncio.wait 三方竞争仅供取消路径使用。

        背景：宿主事件循环 teardown（如 TestClient 每请求 portal 关闭）
        的整体取消扫描会把孤立 communicate 任务与外层任务分别取消，取消
        解卷交错后外层任务可能悬挂在永不结算的 future 上（全量测试曾
        稳定复现卡死）。git 等既有调用方不传 cancel_event，必须保持与
        沙箱化之前 wait_for 内联语义逐项一致。"""
        cfg.sandbox.mode = "local"

        async def _forbidden_wait(*args, **kwargs):
            raise AssertionError(
                "no-cancel_event path must keep inline wait_for semantics"
            )

        monkeypatch.setattr(
            "backend.utils.sandbox.local_backend.asyncio.wait", _forbidden_wait
        )
        runner = SandboxRunner()
        res = await runner.execute(_spec(command=["echo", "hi"], timeout_seconds=10))
        assert res.returncode == 0
        assert res.timed_out is False

    @pytest.mark.asyncio
    async def test_outer_task_cancelled_midflight_settles(self, cfg):
        """回归（阶段一评审修复）：模拟请求循环 teardown 的整体取消扫描——
        外层任务与子进程派生任务同时被取消时，execute 必须在有限时间内
        全部结算，不得悬挂。"""
        cfg.sandbox.mode = "local"
        runner = SandboxRunner()
        spec = _spec(command=["sleep", "30"], timeout_seconds=60)
        task = asyncio.ensure_future(runner.execute(spec))
        await asyncio.sleep(0.3)  # 等 sleep 子进程启动并进入等待
        current = asyncio.current_task()
        others = [t for t in asyncio.all_tasks() if t is not current]
        for t in others:
            t.cancel()
        task.cancel()
        done, pending = await asyncio.wait({task, *others}, timeout=10)
        assert pending == set()  # 全部结算：teardown 不再可能卡死


class TestErrorFactories:
    def test_sandbox_busy_tagged(self):
        err = make_sandbox_error("sandbox_busy", "pool full")
        assert isinstance(err, RuntimeError)  # 遵守 AGENTS.md：复用标准异常
        assert sandbox_code_of(err) == "sandbox_busy"

    def test_plain_runtime_error_has_no_code(self):
        assert sandbox_code_of(RuntimeError("boom")) is None


class TestCurrentUserId:
    def test_returns_none_outside_request_context(self):
        # 单测环境无路由依赖注入，应安全返回 None 而非抛错
        assert current_sandbox_user_id() is None


class TestSingleton:
    def test_get_and_reset(self):
        from backend.utils.sandbox import (
            get_sandbox_runner,
            reset_sandbox_runner,
        )

        reset_sandbox_runner()
        a = get_sandbox_runner()
        b = get_sandbox_runner()
        assert a is b
        reset_sandbox_runner()
        assert get_sandbox_runner() is not a
        reset_sandbox_runner()


class TestInitializeWarmup:
    @pytest.mark.asyncio
    async def test_initialize_fills_gating_cache(self, cfg, monkeypatch):
        """启动预热应填充三类镜像的后端解析缓存（宿主无工具部署形态首请求即正确）。"""
        from backend.utils.sandbox import (
            cached_backend_for,
            initialize_sandbox_runner,
            reset_sandbox_runner,
        )

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.docker_sdk_available", lambda: False
        )
        cfg.sandbox.mode = "auto"
        reset_sandbox_runner()
        try:
            await initialize_sandbox_runner()
            # daemon 不可达 + auto → 三类均解析为 local，门控缓存立即可用
            for kind in ("tex", "convert", "plot"):
                assert cached_backend_for(kind) == "local"
        finally:
            reset_sandbox_runner()

    @pytest.mark.asyncio
    async def test_initialize_survives_unreachable_docker_mode(self, cfg, monkeypatch):
        """docker 模式 + daemon 不可达：预热不抛错，门控留空回落本地检测。"""
        from backend.utils.sandbox import (
            cached_backend_for,
            initialize_sandbox_runner,
            reset_sandbox_runner,
        )

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.docker_sdk_available", lambda: False
        )
        cfg.sandbox.mode = "docker"
        reset_sandbox_runner()
        try:
            await initialize_sandbox_runner()  # 不应抛 sandbox_unavailable
            assert cached_backend_for("convert") is None
        finally:
            reset_sandbox_runner()

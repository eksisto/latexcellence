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

    async def fake_execute(spec, *, image, limits, grace_seconds=0.0):
        calls["image"] = image
        calls["limits"] = limits
        calls["grace"] = grace_seconds
        calls["spec"] = spec
        return result or _result(backend="docker")

    runner._docker = type(
        "FakeDocker",
        (),
        {"ping": staticmethod(fake_ping), "execute": staticmethod(fake_execute)},
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

        async def fake_execute_local(spec):
            seen["spec"] = spec
            return expected

        monkeypatch.setattr(
            "backend.utils.sandbox.runner.execute_local", fake_execute_local
        )
        runner = SandboxRunner()
        res = await runner.execute(_spec(timeout_seconds=7))
        assert res is expected
        assert seen["spec"].timeout_seconds == 7

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
        import backend.utils.sandbox.runner as runner_mod

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
            initialize_sandbox_runner,
            cached_backend_for,
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
            initialize_sandbox_runner,
            cached_backend_for,
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

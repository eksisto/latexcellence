"""docker 后端纯逻辑单元测试（无需 Docker daemon）。

覆盖 fd3e145 修复引入但此前只有 daemon-gated 冒烟才触达的逻辑：
- _demux_docker_stream：帧协议解析 / 控制帧丢弃 / TTY 回退判定
- _network_kwarg_name：docker-py 新旧签名下的网络参数名选择
- _build_container_kwargs：容器安全基线断言（无网络 / 只读 / 降权 / 限额）
- _image_for：空镜像名按内置默认回落（与设置页提示一致）
- execute 的 cancel_event 协作式取消（部署收口阶段一，fake 容器 hermetic）
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from backend.utils.sandbox.docker_backend import (
    DockerBackend,
    _demux_docker_stream,
)
from backend.utils.sandbox.errors import sandbox_code_of
from backend.utils.sandbox.models import ExecSpec, SandboxLimits, SandboxMount


def _frame(ftype: int, payload: bytes) -> bytes:
    """构造 Docker 非 TTY 日志流帧：[1B type][3B flags][4B len][payload]。"""
    return bytes([ftype, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


class TestDemuxDockerStream:
    def test_empty_input(self):
        assert _demux_docker_stream(b"") == (b"", b"")

    def test_stdout_stderr_split(self):
        raw = _frame(1, b"out1") + _frame(2, b"err1") + _frame(1, b"out2")
        out, err = _demux_docker_stream(raw)
        assert out == b"out1out2"
        assert err == b"err1"

    def test_control_frames_dropped(self):
        # 3/4 为 resize 等控制帧，必须丢弃且不影响后续帧解析
        raw = _frame(3, b"ctl") + _frame(1, b"data") + _frame(4, b"ctl2")
        out, err = _demux_docker_stream(raw)
        assert out == b"data"
        assert err == b""

    def test_tty_fallback_when_header_invalid(self):
        # 首字节 > 4 不可能是帧类型：整体按 TTY 直写归入 stdout
        out, err = _demux_docker_stream(b"plain latex output\n")
        assert out == b"plain latex output\n"
        assert err == b""

    def test_tty_fallback_when_length_overruns(self):
        # 首字节形似帧类型，但声明长度超出剩余字节：同样回退 TTY 整体归档
        bogus = bytes([1, 0, 0, 0]) + (10**6).to_bytes(4, "big") + b"short"
        out, err = _demux_docker_stream(bogus)
        assert out == bogus
        assert err == b""


class TestNetworkKwargName:
    @staticmethod
    def _client_with_create(func):
        return SimpleNamespace(containers=SimpleNamespace(create=func))

    def test_new_sdk_uses_networking_mode(self):
        captured: dict = {}

        def create(self=None, image=None, networking_mode=None, **kw):
            captured.update(kw)
            captured["networking_mode"] = networking_mode
            return SimpleNamespace(start=lambda: None)

        client = self._client_with_create(create)
        assert DockerBackend._network_kwarg_name(client) == "networking_mode"

    def test_old_sdk_uses_network_mode(self):
        def create(self=None, image=None, network_mode=None, **kw):
            return SimpleNamespace(start=lambda: None)

        client = self._client_with_create(create)
        assert DockerBackend._network_kwarg_name(client) == "network_mode"


class TestBuildContainerKwargsBaseline:
    """容器安全基线：任何参数重构都不应悄悄削弱隔离。"""

    @pytest.fixture
    def kwargs(self):
        pytest.importorskip("docker")
        spec = ExecSpec(
            command=["latexmk", "-pdf", "main.tex"],
            cwd="/workspace/proj",
            mounts=[SandboxMount(host_path="/workspace/input", read_only=True)],
            env={"OPENAI_API_KEY": "test"},
        )
        limits = SandboxLimits(cpus=2.0, memory_mb=256, pids_limit=128, tmpfs_mb=64)
        return DockerBackend._build_container_kwargs(spec, "tex-img:1", limits)

    def test_read_only_rootfs_and_caps(self, kwargs):
        assert kwargs["read_only"] is True
        assert kwargs["cap_drop"] == ["ALL"]
        assert "no-new-privileges" in kwargs["security_opt"]

    def test_resource_limits(self, kwargs):
        assert kwargs["mem_limit"] == "256M"
        assert kwargs["nano_cpus"] == 2_000_000_000
        assert kwargs["pids_limit"] == 128
        assert kwargs["tmpfs"] == {"/tmp": "size=64m"}

    def test_mounts_same_path_and_flags(self, kwargs):
        mounts = {m["Target"]: m for m in kwargs["mounts"]}
        assert set(mounts) == {"/workspace/proj", "/workspace/input"}
        # cwd 可写、显式挂载只读；source 与 target 同路径（契约前提）
        assert mounts["/workspace/proj"]["ReadOnly"] is False
        ro = mounts["/workspace/input"]
        assert ro["ReadOnly"] is True
        assert ro["Source"] == ro["Target"] == "/workspace/input"

    def test_env_home_defaults_to_tmpfs(self, kwargs):
        assert kwargs["environment"]["HOME"] == "/tmp"
        assert kwargs["environment"]["OPENAI_API_KEY"] == "test"

    def test_non_root_user_on_posix(self, kwargs):
        if hasattr(os, "getuid"):
            assert kwargs["user"] == f"{os.getuid()}:{os.getgid()}"


class TestImageForFallback:
    """空镜像名按内置默认回落（与设置页三语提示语义一致）。"""

    def _patch_cfg(self, monkeypatch, tex="", convert=" ", plot="img-x"):
        from backend.utils.sandbox import runner as runner_mod

        sandbox = SimpleNamespace(
            image_tex=tex,
            image_convert=convert,
            image_plot=plot,
            mode="auto",
            cpus=1.0,
            memory_mb=64,
            pids_limit=16,
            tmpfs_mb=32,
            max_concurrent_global=1,
            max_concurrent_per_user=1,
            startup_grace_seconds=1,
        )
        monkeypatch.setattr(
            runner_mod, "get_config", lambda: SimpleNamespace(sandbox=sandbox)
        )
        return runner_mod.SandboxRunner()

    def test_empty_values_fall_back_to_builtin(self, monkeypatch):
        r = self._patch_cfg(monkeypatch)
        assert r._image_for("tex") == "latexcellence-tex:latest"
        assert r._image_for("convert") == "latexcellence-convert:latest"

    def test_explicit_value_passthrough_after_strip(self, monkeypatch):
        r = self._patch_cfg(monkeypatch)
        assert r._image_for("plot") == "img-x"

    def test_unknown_kind_raises(self, monkeypatch):
        r = self._patch_cfg(monkeypatch)
        with pytest.raises(RuntimeError) as ei:
            r._image_for("nope")
        assert sandbox_code_of(ei.value) == "sandbox_unavailable"


class TestProbeMountPreflightIntegration:
    """ping 通过但挂载预检失败 → auto 回落 local；结果进程内固化。"""

    @pytest.mark.asyncio
    async def test_preflight_failure_falls_back_to_local(self, monkeypatch):
        from backend.utils.sandbox import runner as runner_mod

        class FakeDocker:
            preflight_calls = 0

            async def ping(self):
                return True

            async def preflight_mount_check(self, image):
                FakeDocker.preflight_calls += 1
                return False

        sandbox = SimpleNamespace(
            mode="auto",
            image_tex="t",
            image_convert="c",
            image_plot="p",
            max_concurrent_global=1,
            max_concurrent_per_user=1,
            startup_grace_seconds=1,
        )
        monkeypatch.setattr(
            runner_mod, "get_config", lambda: SimpleNamespace(sandbox=sandbox)
        )
        r = runner_mod.SandboxRunner()
        r._docker = FakeDocker()
        assert await r.probe_docker() is False
        assert await r.resolve_backend("tex") == "local"
        # 进程内固化：TTL 过期后也不重复预检
        r._probe_at -= runner_mod.PROBE_TTL_SECONDS * 2
        assert await r.probe_docker() is False
        assert FakeDocker.preflight_calls == 1

    @pytest.mark.asyncio
    async def test_local_mode_skips_preflight(self, monkeypatch):
        from backend.utils.sandbox import runner as runner_mod

        class FakeDocker:
            def __init__(self):
                self.preflight_called = False

            async def ping(self):
                return True

            async def preflight_mount_check(self, image):
                self.preflight_called = True
                return True

        sandbox = SimpleNamespace(
            mode="local",
            image_tex="t",
            image_convert="c",
            image_plot="p",
            max_concurrent_global=1,
            max_concurrent_per_user=1,
            startup_grace_seconds=1,
        )
        monkeypatch.setattr(
            runner_mod, "get_config", lambda: SimpleNamespace(sandbox=sandbox)
        )
        r = runner_mod.SandboxRunner()
        fake = FakeDocker()
        r._docker = fake
        await r.probe_docker()
        assert fake.preflight_called is False


class TestExecuteCancelEvent:
    """cancel_event 协作式取消（Q4 方案 A）：fake 容器验证杀容器语义。

    hermetic：fake client/container 替身不触达 daemon；覆盖执行循环中
    cancel_event 置位 → kill → 非 timed_out 结果 → 容器强制移除 的链路，
    以及取消场景下 exit 137 不误判为 oom_killed 的启发式豁免。"""

    @staticmethod
    def _fake_backend(monkeypatch, *, oom_on_kill=False):
        from backend.utils.sandbox.docker_backend import DockerBackend

        calls: dict = {"kill": 0, "removed": 0}

        class FakeContainer:
            def __init__(self):
                self.attrs = {"State": {"Status": "running", "OOMKilled": False}}
                self.killed = False

            def start(self):
                self.attrs["State"]["Status"] = "running"

            def reload(self):
                if self.killed:
                    self.attrs["State"] = {
                        "Status": "exited",
                        "ExitCode": 137,
                        "OOMKilled": oom_on_kill,
                    }

            def kill(self):
                self.killed = True
                calls["kill"] += 1

            def logs(self, stdout=True, stderr=True):
                return b""

            def remove(self, force=False):
                calls["removed"] += 1

        container = FakeContainer()

        class FakeContainers:
            def create(self, **kwargs):
                return container

        class FakeClient:
            containers = FakeContainers()

        backend = DockerBackend()
        monkeypatch.setattr(backend, "_get_client", lambda: FakeClient())
        return backend, container, calls

    @staticmethod
    def _spec() -> ExecSpec:
        return ExecSpec(
            command=["sleep", "60"], cwd="/workspace/proj", timeout_seconds=30
        )

    @pytest.mark.asyncio
    async def test_cancel_kills_container_without_timed_out(self, monkeypatch):
        backend, container, calls = self._fake_backend(monkeypatch)

        async def canceller():
            await asyncio.sleep(0.05)
            event.set()

        event = asyncio.Event()
        task = asyncio.ensure_future(
            backend.execute(
                self._spec(),
                image="img:1",
                limits=SandboxLimits(),
                cancel_event=event,
            )
        )
        asyncio.ensure_future(canceller())
        res = await asyncio.wait_for(task, timeout=10)
        assert calls["kill"] == 1
        assert res.timed_out is False
        assert res.returncode == 137
        assert res.oom_killed is False  # 取消场景豁免 exit-137 oom 启发式
        assert calls["removed"] == 1  # 容器最终强制清理

    @pytest.mark.asyncio
    async def test_cancel_before_start_still_kills(self, monkeypatch):
        """cancel_event 已置位（如监视任务先于执行越限）：不漏杀。"""
        backend, _container, calls = self._fake_backend(monkeypatch)
        event = asyncio.Event()
        event.set()
        res = await asyncio.wait_for(
            backend.execute(
                self._spec(),
                image="img:1",
                limits=SandboxLimits(),
                cancel_event=event,
            ),
            timeout=10,
        )
        assert calls["kill"] == 1
        assert res.timed_out is False
        assert res.returncode == 137

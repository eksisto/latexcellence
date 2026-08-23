"""docker 后端纯逻辑单元测试（无需 Docker daemon）。

覆盖 fd3e145 修复引入但此前只有 daemon-gated 冒烟才触达的逻辑：
- _demux_docker_stream：帧协议解析 / 控制帧丢弃 / TTY 回退判定
- _network_kwarg_name：docker-py 新旧签名下的网络参数名选择
- _build_container_kwargs：容器安全基线断言（无网络 / 只读 / 降权 / 限额）
- _image_for：空镜像名按内置默认回落（与设置页提示一致）
"""

import os
from types import SimpleNamespace

import pytest

from backend.utils.sandbox.docker_backend import (
    DockerBackend,
    _demux_docker_stream,
)
from backend.utils.sandbox.errors import sandbox_code_of
from backend.utils.sandbox.models import ExecSpec, SandboxMount, SandboxLimits


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

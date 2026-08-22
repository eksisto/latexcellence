"""sandbox 包契约测试：ExecSpec / ExecResult / 挂载语义。"""

import pytest
from pydantic import ValidationError

from backend.utils.sandbox.models import (
    ExecResult,
    ExecSpec,
    SandboxLimits,
    SandboxMount,
)


class TestExecSpec:
    def test_minimal_spec(self):
        spec = ExecSpec(command=["echo", "hi"], cwd="/tmp")
        assert spec.command == ["echo", "hi"]
        assert spec.image_kind == "tex"
        assert spec.timeout_seconds == 120

    def test_empty_command_rejected(self):
        with pytest.raises(ValidationError):
            ExecSpec(command=[], cwd="/tmp")

    def test_relative_cwd_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            ExecSpec(command=["x"], cwd="relative/path")

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            ExecSpec(command=["x"], cwd="/tmp", bogus=1)

    def test_effective_mounts_includes_cwd_rw(self):
        spec = ExecSpec(command=["x"], cwd="/work")
        mounts = spec.effective_mounts()
        assert len(mounts) == 1
        assert mounts[0].host_path == "/work"
        assert mounts[0].read_only is False

    def test_effective_mounts_dedupes_cwd_priority(self):
        """cwd 与显式挂载同路径时 cwd 的 rw 语义优先，且不重复挂载。"""
        spec = ExecSpec(
            command=["x"],
            cwd="/work",
            mounts=[SandboxMount(host_path="/work", read_only=True)],
        )
        mounts = spec.effective_mounts()
        assert len(mounts) == 1
        assert mounts[0].read_only is False

    def test_effective_mounts_merges_extra_paths(self):
        spec = ExecSpec(
            command=["x"],
            cwd="/work",
            mounts=[
                SandboxMount(host_path="/data", read_only=True),
                SandboxMount(host_path="/data"),  # 重复路径去重
            ],
        )
        hosts = sorted(m.host_path for m in spec.effective_mounts())
        assert hosts == ["/data", "/work"]


class TestSandboxMount:
    def test_relative_path_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            SandboxMount(host_path="rel/path")

    def test_read_only_flag(self):
        m = SandboxMount(host_path="/etc/ro", read_only=True)
        assert m.read_only is True


class TestSandboxLimits:
    def test_defaults_match_plan(self):
        limits = SandboxLimits()
        assert limits.cpus == 1.0
        assert limits.memory_mb == 1024
        assert limits.pids_limit == 256
        assert limits.tmpfs_mb == 512

    def test_non_positive_cpus_rejected(self):
        with pytest.raises(ValidationError):
            SandboxLimits(cpus=0)


class TestExecResult:
    def test_local_result_defaults(self):
        r = ExecResult(returncode=0, stdout="out", stderr="", backend="local")
        assert r.timed_out is False
        assert r.oom_killed is False
        assert r.backend == "local"

    def test_docker_oom_result(self):
        r = ExecResult(
            returncode=137,
            stdout="",
            stderr="Killed",
            oom_killed=True,
            backend="docker",
        )
        assert r.oom_killed is True
        assert r.backend == "docker"

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValidationError):
            ExecResult(returncode=0, stdout="", stderr="", backend="ssh")

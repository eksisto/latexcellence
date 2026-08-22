"""沙箱执行契约：ExecSpec / ExecResult / 挂载与资源限制模型。

调用方把"要执行的命令 + 工作目录 + 超时"描述成 ExecSpec，由
SandboxRunner 选择后端（local/docker）执行并归一为 ExecResult。

挂载语义（v2 方案 §3 关键决策）：bind mount 使用同绝对路径
（host_path 即容器内路径），保证 template_gen 等解析 .log 中文件路径的
逻辑在容器内外零改动。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 镜像用途分类（与 config.sandbox.image_* 一一对应）
ImageKind = Literal["tex", "convert", "plot"]
# 执行后端标识
BackendKind = Literal["local", "docker"]


class SandboxMount(BaseModel):
    """单个 bind mount：host_path 同时是容器内目标路径（同绝对路径）。"""

    model_config = ConfigDict(extra="forbid")

    host_path: str
    read_only: bool = False

    @model_validator(mode="after")
    def _require_absolute(self) -> "SandboxMount":
        from pathlib import Path

        if not Path(self.host_path).is_absolute():
            raise ValueError(f"mount host_path must be absolute, got: {self.host_path}")
        return self


class SandboxLimits(BaseModel):
    """单容器资源限制（从 sandbox 配置节实时派生）。"""

    model_config = ConfigDict(extra="forbid")

    cpus: float = Field(1.0, gt=0)
    memory_mb: int = Field(1024, ge=64)
    pids_limit: int = Field(256, ge=16)
    tmpfs_mb: int = Field(512, ge=32)


class ExecSpec(BaseModel):
    """一次外部命令执行的完整描述。

    设计约定：不支持 stdin 协议——需要输入数据的调用方一律把内容写进
    工作目录内的临时文件后以路径参数传入（docker 后端不注入 stdin，
    文件化输入让 local/docker 两条后端天然行为一致，避免"仅本地可用"
    的半支持协议）。
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)  # argv 形式，禁止 shell 字符串
    cwd: str  # 工作目录（绝对路径；默认作为 rw 挂载进容器）
    image_kind: ImageKind = "tex"
    mounts: list[SandboxMount] = Field(default_factory=list)  # 额外挂载
    env: dict[str, str] = Field(default_factory=dict)  # 环境变量白名单覆盖
    timeout_seconds: float = Field(120, gt=0)

    @model_validator(mode="after")
    def _validate_paths(self) -> "ExecSpec":
        from pathlib import Path

        if not Path(self.cwd).is_absolute():
            raise ValueError(f"cwd must be absolute, got: {self.cwd}")
        for arg in self.command:
            if not isinstance(arg, str) or not arg:
                raise ValueError("command entries must be non-empty strings")
        return self

    def effective_mounts(self) -> list[SandboxMount]:
        """cwd 的 rw 挂载 + 显式 mounts（按 host_path 去重，cwd 优先）。"""
        merged: dict[str, SandboxMount] = {self.cwd: SandboxMount(host_path=self.cwd)}
        for m in self.mounts:
            merged.setdefault(m.host_path, m)
        return list(merged.values())


class ExecResult(BaseModel):
    """归一化的执行结果契约。

    runner 负责把容器退出码 / OOM 标志 / 超时翻译成本结构，上层
    （timed_out / returncode / stdout/stderr）错误管线不受后端影响。
    """

    model_config = ConfigDict(extra="forbid")

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    oom_killed: bool = False
    backend: BackendKind

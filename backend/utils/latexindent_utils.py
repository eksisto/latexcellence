"""
latexindent.pl 调用封装。

负责：检测环境、调用 latexindent 对 LaTeX 源码片段进行格式化。
执行经 SandboxRunner：sandbox docker 模式下进 tex 镜像容器
（latexindent 内置；配置文件随临时目录挂载进容器），local 回落
本地子进程（行为与历史一致）。
"""

import shutil
import tempfile
import uuid
from pathlib import Path

from backend.core.logger import get_logger
from backend.utils.sandbox import (
    ExecSpec,
    cached_backend_for,
    current_sandbox_user_id,
    get_sandbox_runner,
)

_logger = get_logger("latexindent_utils")

_LATEXINDENT_TIMEOUT = 30

# 项目统一缩进配置：覆盖 latexindent 默认的制表符缩进，统一为每层 4 个空格
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "latexindent.yaml"


def is_latexindent_available() -> bool:
    """检测 latexindent 是否可用。

    sandbox docker 后端下由 tex 镜像内置（宿主无需安装）；本进程尚未
    解析过后端时沿用本地 ``latexindent --version`` 探测。
    """
    if cached_backend_for("tex") == "docker":
        return True
    return _probe_local_version()


def _probe_local_version() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["latexindent", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _resolve_config_for_run(work_dir: Path) -> str | None:
    """定位本次运行使用的 latexindent 配置。

    返回传给 -l 的路径字符串。docker 路径下把配置复制进工作目录
    （容器内只可见挂载目录，宿主源码路径不进容器）；本地路径直接引用。
    """
    if cached_backend_for("tex") == "docker":
        if DEFAULT_CONFIG_PATH.is_file():
            target = work_dir / DEFAULT_CONFIG_PATH.name
            shutil.copy2(DEFAULT_CONFIG_PATH, target)
            return target.name
        _logger.warning(
            f"latexindent config not found: {DEFAULT_CONFIG_PATH}, using defaults"
        )
        return None
    if DEFAULT_CONFIG_PATH.is_file():
        # -l 只合并指定配置文件（其余沿用 latexindent 默认设置）
        return DEFAULT_CONFIG_PATH.as_posix()
    _logger.warning(
        f"latexindent config not found: {DEFAULT_CONFIG_PATH}, using defaults"
    )
    return None


async def format_with_latexindent(
    source: str,
    file_path: str | None = None,
    config_path: str | None = None,
) -> str:
    """
    对 LaTeX 源码片段执行 latexindent 格式化。

    参数：
        source: 要格式化的 LaTeX 源码
        file_path: 原始文件路径（用于检测文件类型，可选）
        config_path: latexindent 配置文件路径（可选）；默认加载项目内置
            latexindent.yaml（每层 4 个空格缩进），可传入自定义配置覆盖

    返回：
        格式化后的 LaTeX 源码；异常或空结果时返回原始源码。
    """
    work_dir = Path(tempfile.mkdtemp(prefix="latexindent_"))
    try:
        tmp_name = f"latexindent_{uuid.uuid4().hex[:8]}.tex"
        tmp_path = work_dir / tmp_name
        tmp_path.write_text(source, encoding="utf-8")

        cmd = [
            "latexindent",
            "-s",
            "-w",
            "-c",
            str(tmp_path.parent),
            f"-g={tmp_path.with_suffix('.log').name}",
        ]
        # -c: 日志/备份统一写入临时目录便于清理；-l 只合并指定配置文件
        custom_cfg = Path(config_path) if config_path else None
        if custom_cfg is not None:
            if custom_cfg.is_file():
                if cached_backend_for("tex") == "docker":
                    merged = work_dir / custom_cfg.name
                    shutil.copy2(custom_cfg, merged)
                    cmd.append(f"-l={merged.name}")
                else:
                    cmd.append(f"-l={custom_cfg.as_posix()}")
            else:
                _logger.warning(
                    f"latexindent config not found: {custom_cfg}, using defaults"
                )
        else:
            resolved = _resolve_config_for_run(work_dir)
            if resolved:
                cmd.append(f"-l={resolved}")
        cmd.append(str(tmp_path))

        spec = ExecSpec(
            command=cmd,
            cwd=str(work_dir),
            image_kind="tex",
            timeout_seconds=float(_LATEXINDENT_TIMEOUT),
        )
        result = await get_sandbox_runner().execute(
            spec, user_id=current_sandbox_user_id()
        )

        if result.timed_out:
            _logger.warning(
                f"latexindent timed out after {_LATEXINDENT_TIMEOUT}s, "
                "falling back to original source"
            )
            return source
        if result.returncode != 0:
            _logger.warning(
                f"latexindent failed with rc={result.returncode}: "
                f"{result.stderr[:200]}"
            )
            return source
        formatted = tmp_path.read_text(encoding="utf-8")
        if not formatted.strip():
            _logger.warning("latexindent returned empty output, falling back")
            return source
        return formatted

    except Exception as e:  # noqa: BLE001 — 任何失败都回退原始源码
        _logger.warning(
            f"latexindent call failed: {e}, falling back to original source"
        )
        return source
    finally:
        # 整目录清理：备份（*.bak*）、日志随工作目录一并删除。
        # 同步 rmtree + 重试等待挪到线程池，避免阻塞事件循环；
        # Windows 上文件句柄释放有延迟，短暂重试后放弃（残留仅在系统临时区）
        import asyncio

        await asyncio.to_thread(_cleanup_work_dir, work_dir)


def _cleanup_work_dir(work_dir: Path) -> None:
    for attempt in range(3):
        try:
            shutil.rmtree(work_dir)
            return
        except OSError:
            if attempt == 2:
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                import time

                time.sleep(0.05)

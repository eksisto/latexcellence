"""pytest 根配置：为每次会话分配独立的 basetemp。

Windows 上 Codex 沙箱以专用用户（如 CodexSandboxOffline）执行命令，
pytest 若长期复用同一个 basetemp，跨会话、跨主体清理旧目录时可能因
ACL 权限不足触发 PermissionError（WinError 5）。本文件让每次 pytest
会话使用工作区内 `.pytest_tmp/session-<时间戳>-<随机串>/` 作为独立
basetemp：会话之间互不清理对方目录；下次会话开始时尽力清理较旧的
历史会话目录（保留数量沿用 `tmp_path_retention_count`，默认 3）。

权限受限而无法清理的残留目录由 `clean-cache.ps1`（自动提权）兜底，
完整排查说明见 `docs/windows-temp-permissions.md`。
"""

from __future__ import annotations

import shutil
import uuid
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

_SESSION_PREFIX = "session-"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_old_sessions(base: Path, keep: int) -> None:
    """尽力保留最近 keep 个会话目录，删除更早的；失败静默跳过。"""
    sessions: list[Path] = []
    try:
        candidates = list(base.glob(f"{_SESSION_PREFIX}*"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.is_dir():
                sessions.append(candidate)
        except OSError:
            # ACL 受限的历史目录无法 stat，交给清理脚本处理。
            continue
    # 相同 mtime 时按名称降序，保证清理顺序确定（名称含时间戳，近似创建顺序）。
    sessions.sort(key=lambda p: (_mtime(p), p.name), reverse=True)
    for old in sessions[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def _make_session_dir(config: Any, *, cleanup: bool = True) -> Path | None:
    try:
        root = Path(getattr(config, "rootpath", config.rootdir))
        base = root / ".pytest_tmp"
        base.mkdir(mode=0o700, exist_ok=True)
        if cleanup:
            try:
                keep = max(1, int(config.getini("tmp_path_retention_count")))
            except Exception:
                keep = 3
            _cleanup_old_sessions(base, keep)
        name = (
            f"{_SESSION_PREFIX}{datetime.now():%Y%m%d-%H%M%S}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        session_dir = base / name
        session_dir.mkdir(mode=0o700)
        return session_dir
    except OSError as exc:
        warnings.warn(
            f"无法创建工作区会话级 basetemp（{exc}），已回退到 pytest "
            "默认临时目录；排查说明见 docs/windows-temp-permissions.md",
            RuntimeWarning,
        )
        return None


def pytest_configure(config: Any) -> None:
    """把内置 tmpdir 工厂指向本会话的独立 basetemp。"""
    if config.option.basetemp is not None:
        # 显式 --basetemp 优先，尊重用户选择。
        return
    # xdist worker 不做历史清理：多 worker 并发启动时 keep（默认 3）小于
    # worker 数，后到的清理会把先到的 worker 刚创建的会话目录误判为旧目录
    # 删除。历史目录清理由主进程（无 workerinput）统一负责。
    cleanup = not hasattr(config, "workerinput")
    session_dir = _make_session_dir(config, cleanup=cleanup)
    if session_dir is None:
        return
    config.option.basetemp = str(session_dir)
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None and hasattr(factory, "_given_basetemp"):
        # 内置 tmpdir 插件若已在本配置阶段创建工厂（basetemp 惰性取用），
        # 在首次取用前替换它的目标目录；若尚未创建，后续 from_config 会
        # 读取上面写入的 config.option.basetemp。两种顺序都覆盖。
        factory._given_basetemp = session_dir
        factory._basetemp = None

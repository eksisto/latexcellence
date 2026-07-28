"""
项目路径安全校验模块。

职责：
    提供唯一的项目路径解析入口 resolve_project_path()，
    所有需要根据项目根目录拼接 file_path 的调用方必须经过此函数。

校验规则：
    1. 不为空 / 不含 null 字节
    2. 不是绝对路径
    3. 不含 '..' 路径段（快速拦截）
    4. resolve() 后 is_relative_to(root.resolve())（精确拦截符号链接逃逸）
    5. 可选：拒绝符号链接
    6. 可选：扩展名白名单
"""

import os
from pathlib import Path

from backend.core.logger import get_logger

_logger = get_logger("path_guard")


def resolve_project_path(
    root: Path,
    file_path: str,
    *,
    allowed_suffixes: tuple[str, ...] | None = None,
    allow_symlink: bool = False,
) -> Path:
    """
    唯一的项目路径解析入口。

    将 file_path 相对于 root 解析为绝对路径，同时执行安全校验，
    确保结果不超出项目根目录范围。

    参数：
        root: 项目根目录（已 resolve 的 Path）
        file_path: 相对路径（如 "chapters/intro.tex"）
        allowed_suffixes: 允许的文件扩展名白名单，None 表示不限制
        allow_symlink: 是否允许符号链接，False 则拒绝所有符号链接

    返回：
        规范化后的绝对路径

    异常：
        ValueError: 路径不合法或超出项目根目录
    """
    if not file_path or not file_path.strip():
        raise ValueError("file_path must not be empty")

    if "\x00" in file_path:
        raise ValueError("file_path contains illegal characters")

    if os.path.isabs(file_path):
        raise ValueError("file_path must not be an absolute path")

    if ".." in Path(file_path).parts:
        raise ValueError("file_path must not contain '..' segments")

    resolved = (root / file_path).resolve()

    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("file_path resolves outside the project root")

    if not allow_symlink:
        check = Path(root / file_path)
        while check != root and check != check.parent:
            try:
                if os.path.islink(check):
                    raise ValueError("file_path contains a symbolic link")
            except ValueError:
                raise
            except OSError:
                pass
            check = check.parent

    if allowed_suffixes is not None and resolved.suffix.lower() not in allowed_suffixes:
        allowed = "/".join(allowed_suffixes)
        raise ValueError(f"file_path must end with one of: {allowed}")

    return resolved

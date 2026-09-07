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

另提供 resolve_path_case_insensitive()：按大小写不敏感方式解析任意磁盘
路径（供项目打开 / 目录浏览使用），不参与项目根目录的安全拼接。
"""

import os
from pathlib import Path, PureWindowsPath

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

    # 跨平台绝对路径判断：os.path.isabs 只识别当前平台的绝对路径（Linux 上是
    # POSIX /...，Windows 上是盘符/UNC），需叠加 PureWindowsPath 覆盖 Windows
    # 风格绝对路径（C:/...、C:\...、\\server\share\...），防止跨平台走私绕过。
    if (
        os.path.isabs(file_path) or PureWindowsPath(file_path).is_absolute()
    ):  # noqa: PTH117 — bytes 路径与跨平台语义，见上注释
        raise ValueError("file_path must not be an absolute path")

    if ".." in Path(file_path).parts:
        raise ValueError("file_path must not contain '..' segments")

    raw = Path(root / file_path)
    resolved = raw.resolve()
    root_resolved = root.resolve()

    # 符号链接检测必须先于 resolve() 后的越界检查：指向项目外的链接在
    # resolved 路径上已经越界，若先做 is_relative_to 会得到 “resolves
    # outside” 而非精确的 “symbolic link” 错误。
    if not allow_symlink:
        check = raw
        while check != root and check != check.parent:
            try:
                if os.path.islink(check):
                    raise ValueError("file_path contains a symbolic link")
            except ValueError:
                raise
            except OSError:
                pass
            check = check.parent

    if not resolved.is_relative_to(root_resolved):
        raise ValueError("file_path resolves outside the project root")

    if allowed_suffixes is not None and resolved.suffix.lower() not in allowed_suffixes:
        allowed = "/".join(allowed_suffixes)
        raise ValueError(f"file_path must end with one of: {allowed}")

    # allow_symlink=True 时返回未 resolve 的链接路径本身（契约见测试）；
    # 越界链接仍会被上面的 is_relative_to 拦截，安全性质不变。
    return raw if allow_symlink else resolved


def resolve_path_case_insensitive(path: str) -> Path | None:
    """
    大小写不敏感地解析路径，返回磁盘上的真实路径（真实大小写）。

    Windows/NTFS 本身大小写不敏感，`Path.exists()` 已能命中错误大小写的路径；
    此函数主要让 Linux 等大小写敏感文件系统获得一致的体验（例如输入 /latex
    定位 /LaTeX）。路径不存在（含找不到 casefold 匹配）时返回 None。
    """
    p = Path(path)
    if p.exists():
        return p
    if not p.is_absolute():
        p = Path.cwd() / p
        if p.exists():
            return p

    def _match(anchor: Path, remaining: list[str]) -> Path | None:
        """逐段匹配：每层优先精确匹配，否则扫描父目录条目做 casefold 匹配。"""
        if not remaining:
            return anchor
        head, tail = remaining[0], remaining[1:]
        exact = anchor / head
        if exact.exists():
            return _match(exact, tail)
        # 扫描候选（去重后排序，保持确定性）；找不到/无法扫描返回 None 回溯
        try:
            candidates = sorted(
                {
                    e.name
                    for e in anchor.iterdir()
                    if e.name.casefold() == head.casefold() and e.name != head
                }
            )
        except OSError:
            return None
        for name in candidates:
            resolved = _match(anchor / name, tail)
            if resolved is not None:
                return resolved
        return None

    return _match(Path(p.anchor), list(p.parts[1:]))


def resolve_user_path(
    user_id: str,
    project_id: str,
    file_path: str,
    *,
    allowed_suffixes: tuple[str, ...] | None = None,
    allow_symlink: bool = False,
) -> Path:
    """
    多用户模式统一路径解析入口（基于 resolve_project_path 的封装）。

    将 file_path 解析为 用户项目根目录（data/users/<user_id>/projects/<project_id>/）
    内的绝对路径，并执行与 resolve_project_path 相同的安全校验（拒绝绝对路径、
    '..'、符号链接与越界逃逸）。

    用途：
        - 多用户模式下的项目文件读写，禁止直接使用 `root / file_path` 拼接；
        - 单用户模式仍使用 resolve_project_path()（以用户选择的本地目录为根）；
        - project_id 一律来自服务端生成的 UUID，绝不由用户直接拼接。

    参数：
        user_id: 当前用户 ID（服务端 UUID）
        project_id: 项目 ID（服务端 UUID，同时是目录名）
        file_path: 项目内相对路径（如 "chapters/intro.tex"）
        allowed_suffixes: 允许的文件扩展名白名单
        allow_symlink: 是否允许符号链接（默认 False，拒绝）

    返回：
        校验通过的项目内绝对路径

    异常：
        ValueError: 路径不合法或超出用户项目根目录
    """
    # 延迟导入避免模块加载期循环依赖（userspace 依赖 config，config 不依赖 path_guard）
    from backend.core.multiuser import userspace

    root = userspace.project_dir(user_id, project_id)
    return resolve_project_path(
        root,
        file_path,
        allowed_suffixes=allowed_suffixes,
        allow_symlink=allow_symlink,
    )

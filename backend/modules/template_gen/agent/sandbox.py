"""模板生成 Agent 沙箱路径解析与文件读取。"""

from pathlib import Path

from backend.utils.path_guard import resolve_project_path

from .constants import _ALLOWED_READ_SUFFIXES, _ALLOWED_SUFFIXES


def resolve_sandbox_path(
    sandbox: Path, file_path: str, *, for_read: bool = False
) -> Path:
    """校验并解析沙箱内相对路径，拒绝绝对路径 / '..' / 越界。

    for_read=True 时允许读取编译生成文件（.log/.aux/.toc 等），
    写入仍严格限制在 _ALLOWED_SUFFIXES 白名单内。
    """
    fp = file_path.replace("\\", "/")
    resolved = resolve_project_path(sandbox, fp, allow_symlink=True)
    allowed = _ALLOWED_READ_SUFFIXES if for_read else _ALLOWED_SUFFIXES
    if resolved.suffix.lower() not in allowed:
        raise ValueError(
            f"file_path has disallowed extension: {resolved.suffix} "
            f"(allowed: {allowed})"
        )
    return resolved


def load_sandbox(output_dir: str, session_id: str) -> Path:
    """根据 session_id 解析沙箱目录（供 router 的文件读取端点使用）。

    安全约束：session_id 必须是纯十六进制/字母数字（生成时为 uuid hex），
    解析后必须严格位于 `output_dir/` 子目录内，禁止 `..` 逃逸到
    其他模板或兄弟会话目录。
    """
    if not session_id or not session_id.strip():
        raise ValueError("session_id cannot be empty")
    if "\x00" in session_id:
        raise ValueError("session_id contains invalid characters")
    # 仅允许字母数字与下划线/短横线，杜绝 '..' / '/' / '\\' 注入
    if not all(c.isalnum() or c in "_-" for c in session_id):
        raise ValueError("session_id contains invalid characters")

    base = Path(output_dir)
    if not base.is_absolute():
        base = Path.cwd() / base
    base_root = base.resolve()
    sandbox = (base_root / session_id).resolve()
    if not sandbox.is_relative_to(base_root):
        raise ValueError("session_id resolves outside the sandbox root")
    return sandbox


def safe_read_sandbox_file(
    sandbox: Path, rel_path: str
) -> tuple[str | None, str | None]:
    """安全读取沙箱内文件，返回 (content, error)。"""
    try:
        resolved = resolve_sandbox_path(sandbox, rel_path, for_read=True)
    except ValueError as e:
        return None, str(e)
    if not resolved.exists() or not resolved.is_file():
        return None, f"File not found: {rel_path}"
    return resolved.read_text(encoding="utf-8"), None

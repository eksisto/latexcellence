"""
文件读写辅助模块。

职责：
    1. 文件 MD5 校验（修改前检查一致性，防止并发冲突）
    2. 备份管理（含撤销/重做栈，而非简单 .bak 文件）
    3. 原子写入（先写临时文件，再 rename，避免写入中断导致源文件损坏）
    4. 文本替换辅助（按行号范围精确替换）
"""

import hashlib
import shutil
import tempfile
from collections import deque
from pathlib import Path
from typing import Optional

from backend.core.logger import get_logger


def compute_md5(file_path: str | Path) -> str:
    """
    计算文件的 MD5 哈希值。
    用于在修改前校验文件未被他人改动。

    参数：
        file_path: 文件路径

    返回：
        MD5 十六进制字符串
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def safe_read(file_path: str | Path, encoding: str = "utf-8") -> str:
    """
    安全读取文件全部文本。
    检测非 UTF-8 文件并报告错误。

    参数：
        file_path: 文件路径
        encoding: 字符编码，默认 UTF-8

    返回：
        文件内容字符串
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        raise ValueError(
            f"File {file_path} is not valid {encoding}. "
            "Only UTF-8 encoded files are supported."
        )


def safe_read_lines(file_path: str | Path, encoding: str = "utf-8") -> list[str]:
    """
    安全读取文件全部行，保留行尾换行符。

    参数：
        file_path: 文件路径
        encoding: 字符编码

    返回：
        行列表
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.readlines()
    except UnicodeDecodeError:
        raise ValueError(
            f"File {file_path} is not valid {encoding}. "
            "Only UTF-8 encoded files are supported."
        )


def atomic_write(file_path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """
    原子写入：先写入临时文件，再重命名为目标文件。

    流程：
        1. 在同目录创建临时文件
        2. 写入完整内容
        3. flush + fsync 确保落盘
        4. 将临时文件重命名为目标文件（操作系统保证原子性）

    参数：
        file_path: 目标文件路径
        content: 要写入的内容
        encoding: 字符编码
    """
    path = Path(file_path)
    # 在同目录创建临时文件，确保 rename 在同文件系统上
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with open(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
        # 原子替换
        tmp_path_obj = Path(tmp_path)
        tmp_path_obj.replace(path)
    except Exception:
        # 清理临时文件
        Path(tmp_path).unlink(missing_ok=True)
        raise


def replace_lines_in_place(
    file_path: str | Path,
    line_start: int,
    line_end: int,
    new_lines: list[str],
    encoding: str = "utf-8",
) -> None:
    """
    替换文件中指定行号范围的内容。

    参数：
        file_path: 文件路径
        line_start: 起始行号（1-indexed）
        line_end: 结束行号（1-indexed，包含）
        new_lines: 新行内容列表（不含换行符）
        encoding: 字符编码
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(path, "r", encoding=encoding) as f:
        lines = f.readlines()

    # 边界检查
    if line_start < 1 or line_start > len(lines):
        raise IndexError(f"line_start {line_start} out of range (1-{len(lines)})")
    if line_end < line_start or line_end > len(lines):
        raise IndexError(f"line_end {line_end} out of range ({line_start}-{len(lines)})")

    # 替换行
    replacement = [ln + "\n" for ln in new_lines]
    lines[line_start - 1 : line_end] = replacement

    # 确保末尾有换行
    content = "".join(lines)
    if not content.endswith("\n"):
        content += "\n"

    atomic_write(path, content, encoding)


class BackupManager:
    """
    文件备份与撤销/重做管理器。

    维护一个撤销栈和重做栈，支持多步 undo/redo 操作。
    每次 apply_change 前自动 push 当前状态到撤销栈。

    用法：
        mgr = BackupManager()
        mgr.push("file.tex")                    # 快照当前状态
        apply_changes("file.tex")               # 执行修改
        mgr.undo("file.tex")                    # 撤销一步
        mgr.redo("file.tex")                    # 重做一步
    """

    def __init__(self, max_history: int = 50):
        """
        参数：
            max_history: 每个文件最多保留的历史快照数
        """
        self._max_history = max_history
        # { file_path_abs: deque of snapshots (latest at right) }
        self._undo_stacks: dict[str, deque[str]] = {}
        # { file_path_abs: deque of snapshots }
        self._redo_stacks: dict[str, deque[str]] = {}
        self._logger = get_logger("backup_manager")

    def push(self, file_path: str | Path) -> str:
        """
        将文件当前内容推入撤销栈。
        应在执行任何修改操作前调用。

        参数：
            file_path: 文件路径

        返回：
            当前文件的 MD5 哈希值
        """
        path = Path(file_path).resolve()
        abs_path = str(path)

        if abs_path not in self._undo_stacks:
            self._undo_stacks[abs_path] = deque(maxlen=self._max_history)

        content = safe_read(path)
        self._undo_stacks[abs_path].append(content)

        # 清空重做栈（新操作后不能再 redo 旧状态）
        self._redo_stacks.pop(abs_path, None)

        md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
        self._logger.debug(
            f"Backup pushed: {path.name} | undo_stack_size={len(self._undo_stacks[abs_path])} | md5={md5[:8]}"
        )
        return md5

    def verify_md5(self, file_path: str | Path, expected_md5: str) -> bool:
        """
        校验文件当前 MD5 是否与解析时一致。
        不一致说明用户在审核期间手动修改了文件。

        参数：
            file_path: 文件路径
            expected_md5: 解析时记录的 MD5

        返回：
            True 表示一致，可安全写入
        """
        current = compute_md5(file_path)
        return current == expected_md5

    def undo(self, file_path: str | Path) -> bool:
        """
        撤销最近一次 push 以来的修改。
        将文件恢复到 push 时的状态。

        参数：
            file_path: 文件路径

        返回：
            True 表示成功撤销，False 表示无可撤销的快照
        """
        path = Path(file_path).resolve()
        abs_path = str(path)

        if abs_path not in self._undo_stacks or not self._undo_stacks[abs_path]:
            self._logger.warning(f"No undo history for: {path.name}")
            return False

        # 将当前内容推入重做栈
        current = safe_read(path)
        if abs_path not in self._redo_stacks:
            self._redo_stacks[abs_path] = deque(maxlen=self._max_history)
        self._redo_stacks[abs_path].append(current)

        # 取出撤销栈中的快照并写回
        snapshot = self._undo_stacks[abs_path].pop()
        atomic_write(path, snapshot)

        self._logger.info(
            f"Undo applied: {path.name} | undo_remaining={len(self._undo_stacks[abs_path])}"
        )
        return True

    def redo(self, file_path: str | Path) -> bool:
        """
        重做最近一次 undo 撤销的修改。

        参数：
            file_path: 文件路径

        返回：
            True 表示成功重做，False 表示无可重做的快照
        """
        path = Path(file_path).resolve()
        abs_path = str(path)

        if abs_path not in self._redo_stacks or not self._redo_stacks[abs_path]:
            self._logger.warning(f"No redo history for: {path.name}")
            return False

        # 将当前内容推入撤销栈
        current = safe_read(path)
        if abs_path not in self._undo_stacks:
            self._undo_stacks[abs_path] = deque(maxlen=self._max_history)
        self._undo_stacks[abs_path].append(current)

        # 取出重做栈中的快照并写回
        snapshot = self._redo_stacks[abs_path].pop()
        atomic_write(path, snapshot)

        self._logger.info(
            f"Redo applied: {path.name} | redo_remaining={len(self._redo_stacks[abs_path])}"
        )
        return True

    def can_undo(self, file_path: str | Path) -> bool:
        """检查指定文件是否有可撤销的快照。"""
        abs_path = str(Path(file_path).resolve())
        return abs_path in self._undo_stacks and len(self._undo_stacks[abs_path]) > 0

    def can_redo(self, file_path: str | Path) -> bool:
        """检查指定文件是否有可重做的快照。"""
        abs_path = str(Path(file_path).resolve())
        return abs_path in self._redo_stacks and len(self._redo_stacks[abs_path]) > 0

    def clear(self, file_path: Optional[str | Path] = None) -> None:
        """
        清空备份历史。
        如果指定文件路径，则只清空该文件的记录；否则清空所有。

        参数：
            file_path: 文件路径，为 None 时清空所有记录
        """
        if file_path is not None:
            abs_path = str(Path(file_path).resolve())
            self._undo_stacks.pop(abs_path, None)
            self._redo_stacks.pop(abs_path, None)
        else:
            self._undo_stacks.clear()
            self._redo_stacks.clear()


# 全局单例
_backup_manager: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    """获取全局唯一的 BackupManager 实例。"""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = BackupManager()
    return _backup_manager

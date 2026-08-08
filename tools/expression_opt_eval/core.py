"""表达优化人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

通用逻辑（语料加载、工作区、marker 还原、命中判定、CLI 骨架）收敛在
tools/eval_common.py；本文件保留工具专属常量，并向后兼容导出共享函数。
"""

from eval_common import (
    PROJECT_ROOT,
    analyze_hits,
    load_manifest as _load_manifest,
    normalize_fragment,
    restore_markers,
)
from eval_common import iter_items as _iter_items
from eval_common import open_eval_workspace as _open_eval_workspace

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "expression_opt"


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return _load_manifest(FIXTURES_DIR)


def iter_items(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 段落 LaTeX 文本)。"""
    yield from _iter_items(FIXTURES_DIR, manifest, limit)


def open_eval_workspace(workspace_id: str) -> None:
    """在语料目录上打开评估工作区（幂等：已打开则复用）。"""
    _open_eval_workspace(FIXTURES_DIR, workspace_id)

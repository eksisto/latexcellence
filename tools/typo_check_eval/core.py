"""错字查找人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

职责：
    1. 加载 tests/fixtures/typo_check 语料（manifest.json + 每条一个 .tex 段落片段）
    2. 在语料目录上打开工作区（typo_check 直接作用于 .tex 文本，无需像 OCR 那样渲染图片）
    3. 还原 marker：把 LLM 修正文本（含 __CMD_0000__ / __MATH_0000__ 等 marker）还原为
       可读 LaTeX，供人工报告展示
    4. 期望错字命中判定：把 manifest 中标注的期望错字与检查出的 changes 做匹配，
       给出命中/漏检/误报辅助标记（仅辅助人工观察，不是自动评分）
"""

import json
import re
from pathlib import Path

from backend.core.workspace_manager import get_workspace_manager
from backend.utils.latex_parser import LatexParser

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "typo_check"


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def iter_items(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 段落 LaTeX 文本)。"""
    entries = manifest["items"]
    if limit is not None:
        entries = entries[:limit]
    for entry in entries:
        tex = (FIXTURES_DIR / entry["file"]).read_text(encoding="utf-8").strip()
        yield entry, tex


def open_eval_workspace(workspace_id: str) -> None:
    """在语料目录上打开评估工作区（幂等：已打开则复用）。"""
    project = get_workspace_manager().get_or_create(workspace_id)
    if not project.is_open:
        project.open(str(FIXTURES_DIR))


def restore_markers(original_tex: str, corrected_with_markers: str) -> str:
    """把 LLM 修正文本中的 marker 还原为原始 LaTeX（供报告展示）。"""
    parser = LatexParser()
    _, marker_map = parser.strip_to_plain_text(original_tex)
    return parser.restore_from_markers(corrected_with_markers, marker_map)


def normalize_fragment(text: str) -> str:
    """归一化片段：折叠空白并转小写，用于期望错字与 changes 的宽松匹配。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def analyze_hits(
    expected_issues: list[dict], changes: list[dict]
) -> dict[str, list[int]]:
    """统计期望错字命中 / 漏检 / 误报修改条目（辅助人工观察）。

    匹配规则（宽松、仅用于报告标记）：
        - 命中：某条 change.original 与期望错字原文归一化后互相包含（子串匹配），
          或二者共享至少一个显著词元（英文单词长度 ≥ 4）——覆盖 LLM 引用更宽
          上下文片段（如期望 "affect on"、change 引用 "has little affect"）的情况
        - 误报：change.original 不包含任何期望错字原文
        - 干净语料（无期望错字）的任意非空 changes 全部计入误报
    """
    expected_norm = [normalize_fragment(e.get("original", "")) for e in expected_issues]
    matched: set[int] = set()
    false_positives: list[int] = []
    for idx, change in enumerate(changes):
        orig_norm = normalize_fragment(change.get("original", ""))
        if not orig_norm:
            continue
        hit = [
            i
            for i, exp in enumerate(expected_norm)
            if exp
            and (
                exp in orig_norm
                or orig_norm in exp
                or _share_significant_token(exp, orig_norm)
            )
        ]
        if hit:
            matched.update(hit)
        else:
            false_positives.append(idx)
    return {
        "matched": sorted(matched),
        "missed": [i for i in range(len(expected_issues)) if i not in matched],
        "false_positive_changes": false_positives,
    }


def _share_significant_token(expected_norm: str, change_norm: str) -> bool:
    """期望片段与修改片段是否共享至少一个显著词元（长度 ≥ 4）。

    中文片段整体作为单个词元处理（无空格分词），子串规则已覆盖；此规则
    主要解决英文多词期望片段与更宽的 change.original 之间的匹配。
    """
    expected_tokens = set(expected_norm.split())
    change_tokens = set(change_norm.split())
    return any(len(tok) >= 4 and tok in change_tokens for tok in expected_tokens)

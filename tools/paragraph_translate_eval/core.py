"""逐段翻译人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

通用逻辑（语料加载、工作区、marker 还原、归一化、CLI 骨架）收敛在
tools/eval_common.py；本文件保留翻译专属的期望短语命中判定与语料校验，
并向后兼容导出共享函数。

职责：
    1. 加载 tests/fixtures/paragraph_translate 语料（manifest.json + 每条一个
       单段落 .tex 片段）
    2. 在语料目录上打开工作区（paragraph_translate 直接作用于 .tex 文本，
       无需像 OCR 那样渲染图片）
    3. 还原 marker：服务成功路径的 translated 已是还原后的干净 LaTeX（幂等），
       回退路径为 marker 空间文本，统一还原后供人工报告展示
    4. 期望短语命中判定：把 manifest 中标注的期望短语（original →
       translated）与 changes 及整段译文做匹配，给出命中/漏检辅助标记
       （仅辅助人工观察，不是自动评分）
    5. 语料校验：保证每条单段落、期望短语原文在剥离 marker 后的纯文本中
       可见、短语译文含于参考译文、语言配置合法
"""

import re

from eval_common import (
    PROJECT_ROOT,
    load_manifest as _load_manifest,
    normalize_fragment,
    restore_markers,
    share_significant_token,
)
from eval_common import iter_items as _iter_items
from eval_common import open_eval_workspace as _open_eval_workspace

from backend.utils.latex_parser import LatexParser

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "paragraph_translate"


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return _load_manifest(FIXTURES_DIR)


def iter_items(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 段落 LaTeX 文本)。"""
    yield from _iter_items(FIXTURES_DIR, manifest, limit)


def open_eval_workspace(workspace_id: str) -> None:
    """在语料目录上打开评估工作区（幂等：已打开则复用）。"""
    _open_eval_workspace(FIXTURES_DIR, workspace_id)


def analyze_hits(
    expected_phrases: list[dict],
    changes: list[dict],
    translated_text: str = "",
) -> dict[str, list[int]]:
    """统计期望短语命中 / 漏检与未被短语覆盖的 change（辅助人工观察）。

    匹配规则（宽松、仅用于报告标记）：
        - 命中：某条 change.original 与期望原文归一化后互相包含或共享显著词元，
          且 change.translated 与期望译文归一化后互相包含或共享显著词元；
          未被任何 change 覆盖的期望短语，若其译文整体出现在译文段落中，也算命中
        - 漏检：期望短语既未被任何 change 覆盖，译文段落整体也不含其译文
        - unmatched_changes：与任何期望短语都不匹配的 change 索引。
          翻译是整段行为，这类 change 不是误报，仅作参考信息
    """
    expected = [
        {
            "original_norm": normalize_fragment(phrase.get("original", "")),
            "translated_norm": normalize_fragment(phrase.get("translated", "")),
        }
        for phrase in expected_phrases or []
    ]
    matched: set[int] = set()
    unmatched_changes: list[int] = []
    for idx, change in enumerate(changes or []):
        orig_norm = normalize_fragment(change.get("original", ""))
        trans_norm = normalize_fragment(change.get("translated", ""))
        if not orig_norm or not trans_norm:
            continue
        hit = [
            i
            for i, exp in enumerate(expected)
            if exp["original_norm"]
            and exp["translated_norm"]
            and _phrase_matches(exp, orig_norm, trans_norm)
        ]
        if hit:
            matched.update(hit)
        else:
            unmatched_changes.append(idx)

    # 段落级兜底：LLM 未必逐条输出 changes，译文整体包含期望译文也算命中
    translated_norm = normalize_fragment(translated_text) if translated_text else ""
    for i, exp in enumerate(expected):
        if (
            i not in matched
            and exp["translated_norm"]
            and exp["translated_norm"] in translated_norm
        ):
            matched.add(i)

    return {
        "matched": sorted(matched),
        "missed": [i for i in range(len(expected)) if i not in matched],
        "unmatched_changes": unmatched_changes,
    }


def _phrase_matches(exp: dict, orig_norm: str, trans_norm: str) -> bool:
    """单条 change 是否命中某个期望（原文, 译文）短语对。"""
    original_ok = (
        exp["original_norm"] == orig_norm
        or exp["original_norm"] in orig_norm
        or orig_norm in exp["original_norm"]
        or share_significant_token(exp["original_norm"], orig_norm)
    )
    translated_ok = (
        exp["translated_norm"] == trans_norm
        or exp["translated_norm"] in trans_norm
        or trans_norm in exp["translated_norm"]
        or share_significant_token(exp["translated_norm"], trans_norm)
    )
    return original_ok and translated_ok


def validate_corpus(manifest: dict | None = None) -> list[str]:
    """校验语料不变量，返回问题列表（空列表 = 通过）。

    不变量：
        - 每条片段为单段落（逐段翻译按段落独立处理）
        - 源/目标语言使用合法名称（English / Chinese），含翻译时二者不同
        - has_translation=false 的条目不得声明 expected_phrases
        - 期望短语原文必须出现在剥离 marker 后的纯文本中——若藏在命令参数
          或公式内部会被 marker 保护吞掉，LLM 根本看不到，不能作为期望
        - 期望短语译文必须含于参考译文（保证参考译文确实覆盖了短语）
    """
    manifest = manifest or load_manifest()
    parser = LatexParser()
    valid_languages = {"English", "Chinese"}
    issues: list[str] = []
    for entry in manifest["items"]:
        item_id = entry["id"]
        path = FIXTURES_DIR / entry["file"]
        if not path.exists():
            issues.append(f"{item_id}: 文件不存在 {entry['file']}")
            continue
        tex = path.read_text(encoding="utf-8").strip()
        if "\n\n" in tex:
            issues.append(f"{item_id}: 含空行，不是单段落（逐段翻译按段落独立处理）")
        source = entry.get("source_language", "")
        target = entry.get("target_language", "")
        if source not in valid_languages or target not in valid_languages:
            issues.append(
                f"{item_id}: 语言名称不合法（应为 English / Chinese）: "
                f"{source} → {target}"
            )
        if entry.get("has_translation") and source == target:
            issues.append(f"{item_id}: has_translation=true 但源/目标语言相同")
        phrases = entry.get("expected_phrases", []) or []
        if not entry.get("has_translation") and phrases:
            issues.append(f"{item_id}: has_translation=false 但声明了 expected_phrases")
        try:
            stripped, _ = parser.strip_to_plain_text(tex)
        except Exception as e:
            issues.append(f"{item_id}: 无法剥离 marker（{e}）")
            continue
        plain = re.sub(r"\s+", " ", stripped).lower()
        reference = re.sub(r"\s+", " ", entry.get("expected_translation", "")).lower()
        for phrase in phrases:
            original = phrase.get("original", "")
            translated = phrase.get("translated", "")
            original_norm = normalize_fragment(original)
            translated_norm = normalize_fragment(translated)
            if original_norm and original_norm.isascii():
                pattern = r"(?<![A-Za-z])" + re.escape(original_norm) + r"(?![A-Za-z])"
                found_original = re.search(pattern, plain) is not None
            else:
                found_original = original_norm in plain
            if original_norm and not found_original:
                issues.append(
                    f"{item_id}: 期望短语原文不可见（可能被 marker 保护吞掉）: "
                    f"{original!r}"
                )
            if translated_norm and translated_norm not in reference:
                issues.append(f"{item_id}: 期望短语译文不在参考译文中: {translated!r}")
    return issues

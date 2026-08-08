"""术语一致性人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

通用逻辑（语料加载、工作区、marker 还原、归一化、CLI 骨架）收敛在
tools/eval_common.py；本文件保留术语检查专属的簇级命中判定与语料校验，
并向后兼容导出共享函数。

职责：
    1. 加载 tests/fixtures/terminology_check 语料（manifest.json + 每条一个
       多段落 .tex 文档）
    2. 在语料目录上打开工作区（terminology_check 直接作用于 .tex 文本，
       无需像 OCR 那样渲染图片）
    3. 还原 marker：把每段修正文本（含 __CMD_0000__ / __MATH_0000__ 等
       marker）还原为可读 LaTeX，供人工报告展示
    4. 期望术语簇命中判定：把 manifest 中标注的术语簇（recommended +
       variants）与检查出的 changes 做匹配，给出命中/漏检/误报辅助标记
       （仅辅助人工观察，不是自动评分）
    5. 语料校验：保证每条多段落、期望变体在剥离 marker 后的纯文本中可见、
       recommended 是 variants 之一（防把术语写进命令参数/公式导致 LLM 看不到）
"""

from eval_common import (
    PROJECT_ROOT,
    load_manifest as _load_manifest,
    normalize_fragment,
    restore_markers,
    share_significant_token,
)
from eval_common import iter_items as _iter_items
from eval_common import open_eval_workspace as _open_eval_workspace

import re

from backend.utils.latex_parser import LatexParser

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "terminology_check"


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return _load_manifest(FIXTURES_DIR)


def iter_items(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 文档 LaTeX 全文)。"""
    yield from _iter_items(FIXTURES_DIR, manifest, limit)


def open_eval_workspace(workspace_id: str) -> None:
    """在语料目录上打开评估工作区（幂等：已打开则复用）。"""
    _open_eval_workspace(FIXTURES_DIR, workspace_id)


def analyze_hits(
    expected_clusters: list[dict], changes: list[dict]
) -> dict[str, object]:
    """统计期望术语簇命中 / 漏检 / 误报修改条目（辅助人工观察）。

    期望的"变体替换"由簇展开得到：簇中每个非推荐变体对应一条
    {original: 变体, corrected: 推荐项}。

    匹配规则（宽松、仅用于报告标记）：
        - 命中：某条 change.original 与期望变体归一化后互相包含或共享显著词元，
          且 change.corrected 与簇推荐项归一化后互相包含或共享显著词元
        - 误报：不满足上述条件的 change（干净语料的所有非空 changes 全部计入误报）

    返回 dict 含：
        matched / missed          —— 展开后期望变体替换的命中/漏检索引
        false_positive_changes    —— 误报 change 索引
        cluster_stats             —— 每簇的 matched_variants / missed_variants / status
                                    （hit 全部命中 / partial 部分命中 / missed 全漏）
    """
    expected: list[dict] = []
    for ci, cluster in enumerate(expected_clusters or []):
        rec_norm = normalize_fragment(cluster.get("recommended", ""))
        for variant in cluster.get("variants", []) or []:
            variant_norm = normalize_fragment(variant)
            # 按原始字符串排除推荐项自身（不能用归一化比较：大小写变体如
            # "Deep Learning" 与 "deep learning" 归一化后相同，但前者仍应作为
            # 需要统一的变体计入期望）
            if variant_norm and rec_norm and variant != cluster.get("recommended"):
                expected.append(
                    {
                        "cluster": ci,
                        "variant": variant,
                        "variant_norm": variant_norm,
                        "recommended_norm": rec_norm,
                    }
                )

    matched: set[int] = set()
    false_positives: list[int] = []
    for idx, change in enumerate(changes):
        orig_norm = normalize_fragment(change.get("original", ""))
        corr_norm = normalize_fragment(change.get("corrected", ""))
        if not orig_norm or not corr_norm:
            continue
        hit = [
            i
            for i, exp in enumerate(expected)
            if _pair_matches(exp, orig_norm, corr_norm)
        ]
        if hit:
            matched.update(hit)
        else:
            false_positives.append(idx)

    missed = [i for i in range(len(expected)) if i not in matched]
    cluster_stats: list[dict] = []
    for ci, cluster in enumerate(expected_clusters or []):
        indices = [i for i, e in enumerate(expected) if e["cluster"] == ci]
        matched_vars = [expected[i]["variant"] for i in indices if i in matched]
        missed_vars = [expected[i]["variant"] for i in indices if i not in matched]
        if not indices:
            status = "n/a"
        elif not missed_vars:
            status = "hit"
        elif not matched_vars:
            status = "missed"
        else:
            status = "partial"
        cluster_stats.append(
            {
                "recommended": cluster.get("recommended", ""),
                "variants": list(cluster.get("variants", []) or []),
                "matched_variants": matched_vars,
                "missed_variants": missed_vars,
                "status": status,
            }
        )

    return {
        "matched": sorted(matched),
        "missed": missed,
        "false_positive_changes": false_positives,
        "cluster_stats": cluster_stats,
    }


def _pair_matches(exp: dict, orig_norm: str, corr_norm: str) -> bool:
    """单条 change 是否命中某个期望（变体, 推荐项）对。"""
    variant_ok = (
        exp["variant_norm"] == orig_norm
        or exp["variant_norm"] in orig_norm
        or orig_norm in exp["variant_norm"]
        or share_significant_token(exp["variant_norm"], orig_norm)
    )
    rec_ok = (
        exp["recommended_norm"] == corr_norm
        or exp["recommended_norm"] in corr_norm
        or corr_norm in exp["recommended_norm"]
        or share_significant_token(exp["recommended_norm"], corr_norm)
    )
    return variant_ok and rec_ok


def validate_corpus(manifest: dict | None = None) -> list[str]:
    """校验语料不变量，返回问题列表（空列表 = 通过）。

    不变量：
        - 每条文档至少 2 个段落（术语一致性依赖跨段落聚类）
        - has_inconsistency=false 的条目不得声明 expected_clusters
        - 期望簇的 recommended 必须是 variants 之一（服务侧聚类契约）
        - 每个期望变体必须出现在剥离 marker 后的纯文本中——若藏在命令参数
          或公式内部会被 marker 保护吞掉，LLM 根本看不到，不能作为期望
    """
    manifest = manifest or load_manifest()
    parser = LatexParser()
    issues: list[str] = []
    for entry in manifest["items"]:
        item_id = entry["id"]
        path = FIXTURES_DIR / entry["file"]
        if not path.exists():
            issues.append(f"{item_id}: 文件不存在 {entry['file']}")
            continue
        tex = path.read_text(encoding="utf-8").strip()
        if len(tex.split("\n\n")) < 2:
            issues.append(f"{item_id}: 文档段落数 < 2（术语一致性需要跨段落聚类）")
        clusters = entry.get("expected_clusters", []) or []
        if not entry.get("has_inconsistency") and clusters:
            issues.append(
                f"{item_id}: has_inconsistency=false 但声明了 expected_clusters"
            )
        try:
            stripped, _ = parser.strip_to_plain_text(tex)
        except Exception:
            continue
        plain = re.sub(r"\s+", " ", stripped).lower()
        for cluster in clusters:
            recommended = cluster.get("recommended", "")
            variants = cluster.get("variants", []) or []
            if recommended and recommended not in variants:
                issues.append(
                    f"{item_id}: recommended 不在 variants 中: {recommended!r}"
                )
            for variant in variants:
                variant_norm = normalize_fragment(variant)
                if not variant_norm:
                    continue
                if variant_norm.isascii():
                    pattern = (
                        r"(?<![A-Za-z])" + re.escape(variant_norm) + r"(?![A-Za-z])"
                    )
                    found = re.search(pattern, plain) is not None
                else:
                    found = variant_norm in plain
                if not found:
                    issues.append(
                        f"{item_id}: 变体在纯文本中不可见（可能被 marker 保护吞掉）: "
                        f"{variant!r}"
                    )
    return issues

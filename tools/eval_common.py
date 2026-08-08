"""人工评估工具共享基座（维护者工具，不进入运行时与 pytest）。

错字查找（typo_check_eval）、表达优化（expression_opt_eval）、术语一致性
（terminology_check_eval）三套人工评估工具共用同一套基础设施：

    - 语料加载：load_manifest / iter_items
    - 工作区：open_eval_workspace
    - marker 还原：restore_markers（把 LLM 输出中的 __CMD_0000__ 等还原为
      可读 LaTeX，供人工报告展示）
    - 命中判定：normalize_fragment / share_significant_token / analyze_hits
      （期望 original→corrected 条目的宽松匹配，仅辅助人工观察）
    - CLI 骨架：ensure_config / run_eval_cli / write_results / REPORT_CSS

各工具目录只保留自己的服务调用、结果整理与报告内容（如术语检查的
簇级命中判定与语料校验），其余逻辑收敛在本文件。
"""

import argparse
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from backend.core.config import get_config, load_config
from backend.core.workspace_manager import get_workspace_manager
from backend.utils.latex_parser import LatexParser

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# 三套报告共用的 HTML 样式（terminology 额外用到 .terms / .paragraphs b）
REPORT_CSS = """
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; background: #f5f6f8; color: #222; }
  h1 { font-size: 22px; }
  .summary { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
  .grid { display: flex; flex-wrap: wrap; gap: 16px; }
  section { margin-bottom: 28px; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px; width: 700px; }
  .card h3 { margin: 0 0 8px; font-size: 15px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #fff; }
  .badge.ok { background: #2e9e5b; }
  .badge.warn { background: #d9822b; }
  .badge.err { background: #c0392b; }
  .badge.clean { background: #5b7fa6; }
  .meta { font-size: 12px; color: #666; margin: 4px 0; }
  .issues, .changes, .paragraphs { margin: 8px 0; font-size: 13px; }
  .issues ul { margin: 4px 0; padding-left: 18px; }
  .changes table { border-collapse: collapse; width: 100%; margin-top: 4px; }
  .changes th, .changes td { border: 1px solid #e0e0e0; padding: 4px 6px; font-size: 12px; text-align: left; vertical-align: top; }
  .changes th { background: #f4f5f7; }
  del { color: #c0392b; }
  ins { color: #2e9e5b; text-decoration: none; }
  pre { background: #f8f9fa; border: 1px solid #eee; border-radius: 4px; padding: 8px; font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }
  .hint { font-size: 12px; color: #d9822b; margin: 4px 0; }
  .terms { font-size: 12px; color: #5b7fa6; margin: 4px 0; }
  .empty { color: #999; padding: 8px 0; font-size: 13px; }
  .paragraphs b { display: block; margin-top: 6px; }
"""


# ── 语料加载 ───────────────────────────────────────────────────────────────


def load_manifest(fixtures_dir: Path) -> dict:
    """读取语料 manifest.json。"""
    return json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))


def iter_items(fixtures_dir: Path, manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 语料全文 LaTeX)。"""
    entries = manifest["items"]
    if limit is not None:
        entries = entries[:limit]
    for entry in entries:
        tex = (fixtures_dir / entry["file"]).read_text(encoding="utf-8").strip()
        yield entry, tex


def open_eval_workspace(fixtures_dir: Path, workspace_id: str) -> None:
    """在语料目录上打开评估工作区（幂等：已打开则复用）。"""
    project = get_workspace_manager().get_or_create(workspace_id)
    if not project.is_open:
        project.open(str(fixtures_dir))


# ── marker 还原 ────────────────────────────────────────────────────────────


def restore_markers(original_tex: str, text_with_markers: str) -> str:
    """把 LLM 输出文本中的 marker 还原为原始 LaTeX（供报告展示）。"""
    parser = LatexParser()
    _, marker_map = parser.strip_to_plain_text(original_tex)
    return parser.restore_from_markers(text_with_markers, marker_map)


# ── 命中判定（辅助人工观察，不自动评分）───────────────────────────────────


def normalize_fragment(text: str) -> str:
    """归一化片段：折叠空白并转小写，用于期望条目与 changes 的宽松匹配。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def share_significant_token(expected_norm: str, change_norm: str) -> bool:
    """期望片段与修改片段是否共享至少一个显著词元（长度 ≥ 4）。

    中文片段整体作为单个词元处理（无空格分词），子串规则已覆盖；此规则
    主要解决英文多词期望片段与更宽的 change.original 之间的匹配。
    """
    expected_tokens = set(expected_norm.split())
    change_tokens = set(change_norm.split())
    return any(len(tok) >= 4 and tok in change_tokens for tok in expected_tokens)


def analyze_hits(
    expected_items: list[dict], changes: list[dict]
) -> dict[str, list[int]]:
    """统计期望条目命中 / 漏检 / 误报修改条目（错字/表达优化共用）。

    匹配规则（宽松、仅用于报告标记）：
        - 命中：某条 change.original 与期望原文归一化后互相包含（子串匹配），
          或二者共享至少一个显著词元（英文单词长度 ≥ 4）——覆盖 LLM 引用更宽
          上下文片段的情况
        - 误报：change.original 不包含任何期望原文
        - 干净语料（无期望条目）的任意非空 changes 全部计入误报
    """
    expected_norm = [
        normalize_fragment(item.get("original", "")) for item in expected_items
    ]
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
                or share_significant_token(exp, orig_norm)
            )
        ]
        if hit:
            matched.update(hit)
        else:
            false_positives.append(idx)
    return {
        "matched": sorted(matched),
        "missed": [i for i in range(len(expected_items)) if i not in matched],
        "false_positive_changes": false_positives,
    }


# ── CLI 骨架 ───────────────────────────────────────────────────────────────


def ensure_config(project_root: Path) -> None:
    """确保 config 已加载（脚本不在 FastAPI lifespan 内运行）。"""
    try:
        get_config()
    except RuntimeError:
        config_path = project_root / "config.json"
        if not config_path.exists():
            raise RuntimeError("未找到 config.json，请先配置 LLM API Key")
        load_config(str(config_path))


def write_results(output_dir: Path, results: list[dict]) -> None:
    (output_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_eval_cli(
    *,
    fixtures_dir: Path,
    default_eval_dir: Path,
    default_workspace_id: str,
    description: str,
    no_key_warning: str,
    check_label: str,
    service_factory: Callable[[], object],
    check_one: Callable[[object, dict, str], Awaitable[dict]],
    build_item: Callable[..., dict],
    render_report: Callable[[Path, list[dict]], None],
    validate_corpus: Callable[[dict], list[str]] | None = None,
) -> int:
    """三套人工评估工具共用的 CLI 入口与主循环。

    各工具只需提供服务工厂、单条检查、条目整理与报告渲染；语料加载、
    工作区打开、结果落盘与进度打印统一在此完成。terminology 额外提供
    validate_corpus 时自动增加 --validate 参数。
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--output-dir", type=Path, default=default_eval_dir)
    parser.add_argument(
        "--workspace-id", default=default_workspace_id, help="评估工作区 ID"
    )
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条")
    parser.add_argument("--only", default=None, help="只评估指定 id")
    if validate_corpus is not None:
        parser.add_argument(
            "--validate",
            action="store_true",
            help="只校验语料，不调用 LLM",
        )
    args = parser.parse_args()
    try:
        return asyncio.run(
            run_eval_main(
                args=args,
                fixtures_dir=fixtures_dir,
                no_key_warning=no_key_warning,
                check_label=check_label,
                service_factory=service_factory,
                check_one=check_one,
                build_item=build_item,
                render_report=render_report,
                validate_corpus=validate_corpus,
            )
        )
    except RuntimeError as e:
        print(f"错误：{e}")
        return 1


async def run_eval_main(
    *,
    args: argparse.Namespace,
    fixtures_dir: Path,
    no_key_warning: str,
    check_label: str,
    service_factory: Callable[[], object],
    check_one: Callable[[object, dict, str], Awaitable[dict]],
    build_item: Callable[..., dict],
    render_report: Callable[[Path, list[dict]], None],
    validate_corpus: Callable[[dict], list[str]] | None = None,
) -> int:
    """逐条评估并生成报告（供 run_eval_cli 调用，也可被测试直接驱动）。"""
    ensure_config(PROJECT_ROOT)

    manifest = load_manifest(fixtures_dir)
    if validate_corpus is not None:
        corpus_issues = validate_corpus(manifest)
        for issue in corpus_issues:
            print(f"[corpus] {issue}")
        if corpus_issues:
            print(f"语料校验发现 {len(corpus_issues)} 个问题，请先修复后再运行评估。")
            return 1
        if args.validate:
            print("语料校验通过。")
            return 0

    cfg = get_config()
    if not cfg.llm.api_key:
        print(no_key_warning)

    open_eval_workspace(fixtures_dir, args.workspace_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    service = service_factory()
    results: list[dict] = []
    for entry, tex in iter_items(fixtures_dir, manifest, args.limit):
        if args.only and entry["id"] != args.only:
            continue
        print(f"[{check_label}] {entry['id']} ...", flush=True)
        payload: dict = {}
        error: str | None = None
        try:
            payload = await check_one(service, entry, args.workspace_id)
        except Exception as e:
            error = str(e)
        item = build_item(entry, tex, payload, error)
        detail = f": {error}" if error else ""
        print(f"  -> {item['status']}（{len(item['changes'])} 条修改）{detail}")
        results.append(item)

    write_results(args.output_dir, results)
    report_path = args.output_dir / "report.html"
    render_report(report_path, results)
    print(f"\n报告已生成: {report_path}")
    return 0

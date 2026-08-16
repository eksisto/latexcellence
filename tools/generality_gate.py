# -*- coding: utf-8 -*-
"""Agent 闭环优化通用性门禁（Generality Gate）。

用于 template_gen / plot_gen / style_modify 三个 Agent 闭环优化工具的
防过拟合检查，对应 docs/templategen-optimization-lessons.md 的改进方法：

1. 硬编码特征扫描（check）：提示词/Agent 代码 diff 中出现学校模板、评测
   场景数值、OS 路径等"部署事实"时拒绝；具体字体/宏包名作为弱特征提示
   人工确认，避免把"背答案"写进生产提示词。
2. 提示词体积监控（size）：跟踪 prompts.py 各 Agent 模块的字符串体积，
   防止 system prompt 成为"知识倾倒场"。
3. holdout 场景解析（resolve_scenarios）：日常调优默认排除 holdout 场景，
   仅最终回归可运行。
4. 对照组回归判定（regression_verdict）：compare.py 双 tag 模式调用，
   目标场景提升的同时对照组不得显著回退，负向变化即打回。

用法：
    python tools/generality_gate.py check [--full] [--base <rev>]
                                          [--fail-on-warning] [--report out.json]
    python tools/generality_gate.py size [--baseline base.json] [--write]
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

TOOLS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_ROOT.parent

# 扫描范围：生产提示词 + 三个 Agent 的运行时模块（工具/评测目录不扫）
DEFAULT_SCAN_PATHS: list[str] = [
    "backend/core/prompts.py",
    "backend/modules/template_gen",
    "backend/modules/plot_gen",
    "backend/modules/style_modify",
]

PROMPT_MODULES = ("template_gen", "style_modify", "plot_gen")

# 强特征：命中即拒绝（fail）。这些是"部署事实/评测数据"，不属于通用能力。
STRONG_PATTERNS: list[tuple[str, str]] = [
    ("学校/模板名", r"tjuthesis|天津大学|答辩委员会"),
    ("评测场景数值（TJU 边距）", r"27\.5\s*mm|31\.7\s*mm|35\.7\s*mm|17\.5\s*mm"),
    ("评测骨架常量", r"THESIS_SKELETON"),
    # TJU 命名风格残留（如 \mktjtitlepage / \tj@...）
    ("TJU 前缀私有宏残留", r"\\mktj[a-zA-Z@]*|\\tj@"),
    # OS 环境断言：具体操作系统/字体可用性属于部署事实，不得进入生产提示词
    ("OS 环境断言", r"Windows-safe|Windows TeX"),
    # Windows 盘符路径排除常见转义序列（\n \t \\ \" \d 等）避免误报
    (
        "OS 路径",
        r"/usr/local/texlive|/opt/texlive|[A-Za-z]:\\(?![ntr\\\"0-9dswb])|/home/|/Users/",
    ),
]

# 弱特征：具体字体名。带 e.g./such as 等示例上下文的降级为 example，
# 其余标记 suspicious，需要人工确认是否为环境事实。
WEAK_FONT_PATTERNS: list[tuple[str, str]] = [
    ("字体名", r"\bSimSun\b|\bSimHei\b|\bKaiTi\b|\bFangSong\b|\bFandol\w*"),
    ("字体名", r"Noto (?:Sans|Serif) CJK|Times New Roman|\bArial\b|Helvetica"),
    ("字体名", r"Fira Sans|Microsoft YaHei|微软雅黑|STIX (?:Two|General)"),
    ("中文字体名", r"宋体|黑体|楷体|仿宋"),
]

# 信息级弱特征：宏包名。既可能是通用领域知识（如 GB/T 7714 → gbt7714），
# 也可能是"本环境有这个宏包"的部署事实，仅提示不计数。
WEAK_PACKAGE_PATTERNS: list[tuple[str, str]] = [
    ("宏包名", r"gbt7714|biblatex-gb7714|IEEEtran|ctexbook|ctexart"),
]

# 弱特征：模板级默认值/具体主题名。可能是通用起点，也可能是"背答案"，
# 命中标 suspicious，需要人工确认是否把评测场景/模板细节写进了提示词。
WEAK_TEMPLATE_PATTERNS: list[tuple[str, str]] = [
    (
        "模板默认数值/结构",
        r"1\.5\s*line spacing|9[–-]10\s*pt|28\s*pt|1\.5[–-]2\s*cm"
        r"|letterpaper\s*\+\s*1in",
    ),
    ("模板/主题名", r"metropolis|fontawesome"),
    ("模板默认结构", r"第N章|图 3-1|single page"),
]

EXAMPLE_MARKERS = re.compile(
    r"e\.g\.|such as|for example|example|i\.e\.|如|例如"
    r"|fall back|fallback|or the default|unless",
    re.IGNORECASE,
)


def _is_example_line(text: str) -> bool:
    """示例性提及（e.g. SimSun）与"环境事实断言"（本机只有 Fandol）区分开。"""
    return bool(EXAMPLE_MARKERS.search(text))


def parse_git_diff(diff_text: str) -> list[dict[str, Any]]:
    """解析 `git diff --unified=0` 输出，返回新增行 [{file, line, text}]。"""
    rows: list[dict[str, Any]] = []
    current_file: str | None = None
    new_line: int | None = None
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
        elif raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?(?: @@|$)", raw)
            new_line = int(m.group(1)) if m else None
        elif raw.startswith("+") and not raw.startswith("+++"):
            if current_file and new_line is not None:
                rows.append({"file": current_file, "line": new_line, "text": raw[1:]})
                new_line += 1
        elif raw.startswith(" "):
            # 上下文行也占据新文件行号
            if new_line is not None:
                new_line += 1
    return rows


def _git_diff_added_lines(
    base: str | None = None,
    paths: list[str] | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """获取工作区（或相对 base 提交）新增行。"""
    cmd = ["git", "diff", "--unified=0"]
    if base:
        cmd.append(base)
    if paths:
        cmd += ["--", *paths]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(project_root or PROJECT_ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    return parse_git_diff(proc.stdout)


def _iter_source_lines(files: list[Path], project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(project_root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            rows.append({"file": str(rel), "line": i, "text": line})
    return rows


def _scan_lines(lines: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    infos: list[dict[str, Any]] = []
    for item in lines:
        text = item["text"]
        for name, pat in STRONG_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                failures.append({**item, "pattern": name})
        for name, pat in WEAK_FONT_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                kind = "example" if _is_example_line(text) else "suspicious"
                warnings.append({**item, "pattern": name, "kind": kind})
        for name, pat in WEAK_PACKAGE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                infos.append({**item, "pattern": name})
        for name, pat in WEAK_TEMPLATE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                warnings.append({**item, "pattern": name, "kind": "suspicious"})
    return {"failures": failures, "warnings": warnings, "infos": infos}


def check_hardcoding(
    *,
    full: bool = False,
    base: str | None = None,
    paths: list[str] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """扫描提示词/Agent 代码中的硬编码特征。

    默认只检查 git 未提交的新增行（diff 模式），--full 检查全部内容，
    用于定期审计历史遗留。返回结构：
    {passed, failures, warnings, infos, scanned_files, mode}
    """
    root = project_root or PROJECT_ROOT
    scan_paths = [Path(p) for p in (paths or DEFAULT_SCAN_PATHS)]
    files: list[Path] = []
    for p in scan_paths:
        full_path = p if p.is_absolute() else root / p
        if full_path.is_dir():
            files.extend(sorted(full_path.rglob("*.py")))
        elif full_path.is_file():
            files.append(full_path)

    if full:
        lines = _iter_source_lines(files, root)
        mode = "full"
    else:
        lines = _git_diff_added_lines(
            base=base, paths=[str(p) for p in scan_paths], project_root=root
        )
        mode = f"diff(base={base or 'worktree'})"

    result = _scan_lines(lines)
    result["passed"] = not result["failures"]
    result["scanned_files"] = sorted({f["file"] for f in lines})
    result["mode"] = mode
    return result


def prompt_size_metrics(
    prompts_path: Path | None = None,
) -> dict[str, int]:
    """用 AST 统计 prompts.py 中每个 Agent 模块的提示词字符串总体积。"""
    path = prompts_path or PROJECT_ROOT / "backend" / "core" / "prompts.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    metrics: dict[str, int] = {
        "file": str(path),
        "lines": src.count("\n") + 1,
        "total_chars": len(src),
    }
    for node in ast.walk(tree):
        value = None
        target_name: str | None = None
        if isinstance(node, ast.Assign):
            value = node.value
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            target_name = targets[0].id if targets else None
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            if isinstance(node.target, ast.Name):
                target_name = node.target.id
        if target_name != "_PROMPTS" or not isinstance(value, ast.Dict):
            continue
        for key, module_value in zip(value.keys, value.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value in PROMPT_MODULES
                and isinstance(module_value, ast.Dict)
            ):
                chars = sum(
                    len(item.value)
                    for item in ast.walk(module_value)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
                metrics[f"{key.value}_chars"] = chars
                metrics[f"{key.value}_entries"] = len(module_value.keys)
    return metrics


def compare_prompt_size(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    warn_chars: int = 5000,
    warn_ratio: float = 0.15,
) -> list[str]:
    """对比提示词体积基线，返回需要人工确认的增量说明。"""
    messages: list[str] = []
    for key in sorted(baseline):
        if not key.endswith("_chars"):
            continue
        old = baseline.get(key, 0)
        new = current.get(key, 0)
        delta = new - old
        ratio = delta / old if old else 0.0
        if delta > warn_chars or ratio > warn_ratio:
            messages.append(
                f"{key} 提示词体积增加 {delta} 字符（{old} → {new}，"
                f"+{ratio:.0%}）：需证明新增内容对 ≥2 个异构场景有效"
            )
    return messages


def resolve_scenarios(
    all_keys: list[str],
    holdout_keys: list[str] | set[str],
    *,
    requested: list[str] | None = None,
    include_holdout: bool = False,
) -> tuple[list[str], list[str]]:
    """按 holdout 策略解析要运行的场景 key。

    返回 (keys, messages)：
    - 未显式指定场景时，默认排除 holdout（仅最终回归可全量运行）；
    - 显式指定 holdout 场景时运行但警告：结果不得用于调优决策。
    """
    holdout = set(holdout_keys)
    messages: list[str] = []
    if requested is None:
        keys = [k for k in all_keys if include_holdout or k not in holdout]
        skipped = sorted(k for k in all_keys if k in holdout and not include_holdout)
        if skipped:
            messages.append(
                "已跳过 holdout 场景 "
                f"{', '.join(skipped)}（仅最终回归使用；"
                "可用 --include-holdout 运行）"
            )
    else:
        keys = [k for k in requested if k in all_keys]
        active_holdout = sorted(set(keys) & holdout)
        if active_holdout and not include_holdout:
            messages.append(
                "注意：正在运行 holdout 场景 "
                f"{', '.join(active_holdout)}，其结果不得用于调优决策，"
                "只用于最终回归"
            )
    return keys, messages


def load_visual_reviews(tag: str, tool_root: Path) -> dict[str, float]:
    """读取 results/<tag>/visual_reviews.json，返回 {scenario: overall}。"""
    path = tool_root / "results" / tag / "visual_reviews.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        key: float(value["overall"])
        for key, value in data.items()
        if isinstance(value, dict) and value.get("overall") is not None
    }


def regression_verdict(
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    *,
    score_of: Callable[[dict[str, Any]], float],
    visual_a: dict[str, float] | None = None,
    visual_b: dict[str, float] | None = None,
    tolerance: float = 5.0,
    visual_tolerance: float = 1.0,
    target_keys: list[str] | set[str] = (),
) -> dict[str, Any]:
    """对照组回归判定：任一场景显著负向变化即打回。

    target_keys 声明本次优化的目标场景；未声明时所有场景都按对照处理。
    """
    targets = set(target_keys)
    keys = sorted(set(metrics_a) & set(metrics_b))
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for key in keys:
        sa = float(score_of(metrics_a[key]))
        sb = float(score_of(metrics_b[key]))
        delta = round(sb - sa, 2)
        kind = "target" if key in targets else "control"
        row: dict[str, Any] = {
            "scenario": key,
            "kind": kind,
            "score_a": sa,
            "score_b": sb,
            "delta": delta,
        }
        if delta < -tolerance:
            issues.append(f"{kind} 场景 {key} 显著回退 {delta} 分（{sa} → {sb}）")
        va = (visual_a or {}).get(key)
        vb = (visual_b or {}).get(key)
        if va is not None and vb is not None:
            dv = round(vb - va, 2)
            row["visual_a"] = va
            row["visual_b"] = vb
            row["visual_delta"] = dv
            if dv < -visual_tolerance:
                issues.append(f"{kind} 场景 {key} 视觉回退 {dv} 分（{va} → {vb}）")
        rows.append(row)

    missing_b = sorted(set(metrics_a) - set(metrics_b))
    missing_a = sorted(set(metrics_b) - set(metrics_a))
    notes: list[str] = []
    if missing_b:
        notes.append(f"candidate 缺少场景（无法对比）：{', '.join(missing_b)}")
    if missing_a:
        notes.append(f"baseline 缺少场景（无法对比）：{', '.join(missing_a)}")
    if not targets and keys:
        notes.append("未通过 --targets 声明优化目标场景，所有场景按对照处理")

    return {
        "failed": bool(issues),
        "issues": issues,
        "notes": notes,
        "rows": rows,
        "target_keys": sorted(targets),
        "tolerance": tolerance,
        "visual_tolerance": visual_tolerance,
    }


def _print_report(report: dict[str, Any]) -> None:
    print(f"\n=== Generality Gate（{report['mode']}）===")
    if report["failures"]:
        print("FAIL：发现硬编码特征（评测数据/部署事实流入生产代码）：")
        for hit in report["failures"]:
            print(
                f"  [{hit['pattern']}] {hit['file']}:{hit['line']}: "
                f"{hit['text'].strip()}"
            )
    else:
        print("PASS：未发现强硬编码特征。")
    for hit in report["warnings"]:
        if hit["kind"] == "suspicious":
            print(
                f"  WARN：疑似环境事实 {hit['file']}:{hit['line']}: "
                f"{hit['text'].strip()}"
            )
    examples = [h for h in report["warnings"] if h["kind"] == "example"]
    if examples:
        print(
            f"  提示：{len(examples)} 处示例性提及（e.g. 字体名），"
            "可接受但注意控制累积"
        )
    if report["infos"]:
        print(
            f"  信息：{len(report['infos'])} 处宏包名提及，"
            "请确认属于通用领域知识而非环境事实"
        )
    if not report["scanned_files"]:
        print("  无新增行可扫描（提交前运行会漏检，建议在提交前执行本检查）")
    print(f"  扫描文件：{len(report['scanned_files'])} 个")


def _cmd_check(argv: list[str]) -> int:
    base: str | None = None
    report_path: Path | None = None
    consumed: set[int] = set()
    for i, arg in enumerate(argv):
        if arg == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            consumed.update((i, i + 1))
        elif arg == "--report" and i + 1 < len(argv):
            report_path = Path(argv[i + 1])
            consumed.update((i, i + 1))
        elif arg in ("--full", "--fail-on-warning"):
            consumed.add(i)
    rest = [a for j, a in enumerate(argv) if j not in consumed]
    if rest:
        print(f"unexpected arguments: {', '.join(rest)}")
        return 2

    report = check_hardcoding(full="--full" in argv, base=base)
    _print_report(report)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if report["failures"]:
        return 1
    suspicious = [h for h in report["warnings"] if h["kind"] == "suspicious"]
    if "--fail-on-warning" in argv and suspicious:
        return 1
    return 0


def _cmd_size(argv: list[str]) -> int:
    baseline_path: Path | None = None
    consumed: set[int] = set()
    for i, arg in enumerate(argv):
        if arg == "--baseline" and i + 1 < len(argv):
            baseline_path = Path(argv[i + 1])
            consumed.update((i, i + 1))
        elif arg == "--write":
            consumed.add(i)
    rest = [a for j, a in enumerate(argv) if j not in consumed]
    if rest:
        print(f"unexpected arguments: {', '.join(rest)}")
        return 2

    current = prompt_size_metrics()
    print("\n=== 提示词体积 ===")
    for key in sorted(current):
        if key.endswith("_chars"):
            print(f"  {key}: {current[key]} 字符")
    print(
        f"  prompts.py 总行数: {current['lines']}，文件总字符: {current['total_chars']}"
    )

    if baseline_path is None:
        return 0
    baseline: dict[str, Any] = {}
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            baseline = {}
    if baseline:
        messages = compare_prompt_size(baseline, current)
        if messages:
            print("\n=== 体积增量需人工确认 ===")
            for msg in messages:
                print(f"  WARN：{msg}")
    if "--write" in argv or not baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  已写入基线：{baseline_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        return _cmd_check(rest)
    if cmd == "size":
        return _cmd_size(rest)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

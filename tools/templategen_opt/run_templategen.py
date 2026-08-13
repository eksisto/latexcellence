# -*- coding: utf-8 -*-
"""运行 template_gen Agent 并采集逐场景指标（基线/迭代共用）。

用法：
    python tools/templategen_opt/run_templategen.py <tag> [scenario1,scenario2,...]
        [--include-holdout] [--dry-run]

每个场景输出到 tools/templategen_opt/results/<tag>/<scenario>/
    - events.json        所有 SSE 事件（含时间戳）
    - trace.jsonl        Agent 运行追踪日志副本
    - sandbox/           Agent 产物快照（自包含，不受 cleanup 影响）
    - summary.json       指标汇总（轮数/时长/编译/需求遵守/启发式质量分）
"""

import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
PROJECT_ROOT = TOOLS_ROOT.parent  # 项目根（backend 所在）
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TOOLS_ROOT))

from backend.core.config import load_config  # noqa: E402
from backend.core.llm_client import get_llm_client  # noqa: E402
from backend.modules.template_gen.service import TemplateGenService  # noqa: E402

load_config("config.json")

from generality_gate import resolve_scenarios  # noqa: E402
from templategen_opt.descriptions import (  # noqa: E402
    CATEGORY_LABELS,
    HOLDOUT_SCENARIOS,
    REQUIREMENT_PATTERNS,
    SCENARIOS,
)
from tex_path import ensure_tex_path  # noqa: E402


def _page_count(pdf_path: Path) -> int | None:
    """用 pdfinfo（TeX Live 自带）统计 PDF 页数，失败返回 None。"""
    import shutil as _shutil
    import subprocess

    if not pdf_path.exists() or _shutil.which("pdfinfo") is None:
        return None
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True,
            timeout=30,
        )
        stdout = out.stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if line.lower().startswith("pages:"):
                return int(line.split(":")[1].strip())
    except Exception:  # noqa: BLE001
        return None
    return None


def _requirement_adherence(
    sandbox: Path, key: str, pages: int | None = None
) -> dict[str, Any]:
    """在生成的工程中检索每个硬性要求是否落地（关键词级启发式检查）。"""
    text = ""
    if sandbox.exists():
        for p in sandbox.rglob("*"):
            if p.is_file() and p.suffix.lower() in (
                ".tex",
                ".sty",
                ".cls",
                ".md",
                ".txt",
                ".bib",
                ".cfg",
                ".def",
            ):
                try:
                    text += "\n" + p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

    table = REQUIREMENT_PATTERNS.get(key, REQUIREMENT_PATTERNS["thesis"])

    results: dict[str, Any] = {}
    for label, pats in table:
        if label == "单页（PDF 页数 ≤ 2）":
            matched = [f"pages={pages}"] if (pages is not None and pages <= 2) else []
        else:
            matched = [
                pat for pat in pats if re.search(pat, text, re.IGNORECASE | re.DOTALL)
            ]
        results[label] = {"matched": bool(matched), "patterns": pats, "hits": matched}
    passed = sum(1 for r in results.values() if r["matched"])
    return {
        "passed": passed,
        "total": len(results),
        "ratio": passed / len(results),
        "detail": results,
    }


def quality_score(metrics: dict[str, Any]) -> dict[str, int]:
    """启发式模板质量评分（0-100）：可编译性 + 需求遵守 + 工程结构 + 稳定性。"""
    detail: dict[str, int] = {}
    files = [f["path"] for f in metrics.get("files", [])]
    tex_files = [f for f in files if f.endswith(".tex")]

    detail["compile_success"] = 20 if metrics.get("compile_success") is True else 0
    detail["requirements"] = round(metrics.get("req_ratio", 0) * 40)
    detail["custom_style"] = (
        10 if any(f.endswith((".sty", ".cls")) for f in files) else 0
    )
    detail["readme"] = (
        5
        if any(f.lower().endswith("readme.md") or "readme" in f.lower() for f in files)
        else 0
    )
    detail["multi_file"] = 5 if len(tex_files) >= 3 else 0
    detail["bib"] = 5 if any(f.endswith(".bib") for f in files) else 0
    detail["no_warnings"] = 5 if metrics.get("warnings", 0) == 0 else 0
    detail["not_truncated"] = 5 if metrics.get("truncated") is False else 0
    detail["summary"] = 5 if metrics.get("summary") else 0

    return {"score": sum(detail.values()), "detail": detail}


async def run_scenario(
    service: TemplateGenService,
    llm: Any,
    key: str,
    description: str,
    tag: str,
    out_root: Path,
) -> dict[str, Any]:
    out_dir = out_root / tag / key
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    events: list[dict[str, Any]] = []
    try:

        async def _drain():
            async for ev in service.generate(description, "zh", llm=llm):
                events.append(
                    {"ts_ms": round((time.perf_counter() - t0) * 1000, 1), **ev}
                )

        await asyncio.wait_for(_drain(), timeout=1800)
    except asyncio.TimeoutError:
        events.append(
            {
                "ts_ms": round((time.perf_counter() - t0) * 1000, 1),
                "type": "error",
                "message": "Scenario timed out after 1800s",
            }
        )
    except Exception as e:  # noqa: BLE001
        events.append(
            {
                "ts_ms": round((time.perf_counter() - t0) * 1000, 1),
                "type": "error",
                "message": str(e),
            }
        )

    total_s = round(time.perf_counter() - t0, 2)
    (out_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    done = next((e for e in events if e["type"] == "done"), {})
    # 事件流里没有 run_end（服务不转发），从 trace.jsonl 读取；
    # 无 done 说明生成未正常完成（超时/异常），此时不得回退到复制当前目录。
    sandbox = Path(done["sandbox"]) if done else None
    is_sandbox = sandbox is not None
    trace_events: list[dict[str, Any]] = []
    run_meta: dict[str, Any] = {}
    if is_sandbox and sandbox.exists():
        trace_path = sandbox / "trace.jsonl"
        if trace_path.exists():
            for line in trace_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    trace_events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            shutil.copy2(trace_path, out_dir / "trace.jsonl")
        run_start = next((e for e in trace_events if e.get("type") == "run_start"), {})
        run_end = next((e for e in trace_events if e.get("type") == "run_end"), {})
        run_meta = {
            "rounds": (run_end.get("data") or {}).get("rounds"),
            "compile_success": (run_end.get("data") or {}).get("compile_success"),
            "summary": (run_end.get("data") or {}).get("summary", ""),
            "description": (run_start.get("data") or {}).get("description", ""),
        }

    # 复制沙箱快照（自包含，避免 cleanup 删除后无法回看）
    sandbox_snapshot = out_dir / "sandbox"
    if is_sandbox and sandbox.exists() and sandbox.is_dir():
        if sandbox_snapshot.exists():
            shutil.rmtree(sandbox_snapshot)
        shutil.copytree(
            sandbox,
            sandbox_snapshot,
            ignore=shutil.ignore_patterns(
                "*.aux",
                "*.log",
                "*.fls",
                "*.fdb_latexmk",
                "*.out",
                "*.toc",
                "*.bbl",
                "*.blg",
                "*.bcf",
                "*.run.xml",
                "*.xdv",
                "*.nav",
                "*.snm",
                "*.vrb",
                "*.lof",
                "*.lot",
            ),
        )

    warnings = [e for e in events if e["type"] == "warning"]
    finish_events = [e for e in events if e.get("tool") == "finish"]
    files = done.get("files", [])

    # PDF 页数
    pdfs = list(sandbox_snapshot.rglob("*.pdf")) if sandbox_snapshot.exists() else []
    main_pdf = next(
        (p for p in pdfs if p.name.startswith(("main", "resume", "cv", "thesis"))),
        pdfs[0] if pdfs else None,
    )
    pages = _page_count(main_pdf) if main_pdf else None

    # 需求遵守检查
    req = _requirement_adherence(sandbox_snapshot, key, pages=pages)

    metrics = {
        "tag": tag,
        "scenario": key,
        "category": CATEGORY_LABELS.get(key, key),
        "rounds": run_meta.get("rounds"),
        "total_s": total_s,
        "compile_success": run_meta.get("compile_success"),
        "warnings": len(warnings),
        "warning_messages": [w.get("message", "") for w in warnings],
        "truncated": len(finish_events) == 0,
        "summary": run_meta.get("summary", ""),
        "files": files,
        "pdf_pages": pages,
        "req": req,
        "req_ratio": req["ratio"],
    }
    metrics["quality"] = quality_score(metrics)

    (out_dir / "summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{key}] rounds={metrics['rounds']} compile={metrics['compile_success']} "
        f"req={req['passed']}/{req['total']} score={metrics['quality']['score']} "
        f"pages={pages} files={len(files)} warnings={len(warnings)}"
    )
    return metrics


async def main() -> None:
    ensure_tex_path()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    include_holdout = "--include-holdout" in sys.argv[1:]
    if not args:
        print(__doc__)
        return
    tag = args[0]
    keys, messages = resolve_scenarios(
        list(SCENARIOS),
        HOLDOUT_SCENARIOS,
        requested=args[1].split(",") if len(args) > 1 else None,
        include_holdout=include_holdout,
    )
    for msg in messages:
        print(msg)
    if "--dry-run" in sys.argv[1:]:
        print(f"[dry-run] 将运行 {len(keys)} 个场景: {', '.join(keys)}")
        return
    out_root = Path(__file__).resolve().parent / "results"

    service = TemplateGenService()
    llm = get_llm_client()
    all_metrics: dict[str, Any] = {}
    for key in keys:
        if key not in SCENARIOS:
            print(f"unknown scenario: {key}")
            continue
        print(f"\n=== running {tag}/{key} ({CATEGORY_LABELS.get(key)}) ===")
        all_metrics[key] = await run_scenario(
            service, llm, key, SCENARIOS[key], tag, out_root
        )

    # 部分重跑时与已有 _all.json 合并，避免丢失其他场景的指标
    all_path = out_root / tag / "_all.json"
    all_path.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, Any] = {}
    if all_path.exists():
        try:
            merged = json.loads(all_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            merged = {}
    merged.update(all_metrics)
    all_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    avg = (
        sum(m["quality"]["score"] for m in merged.values()) / len(merged)
        if merged
        else 0
    )
    print(
        f"\n=== {tag} avg heuristic score ({len(merged)} scenarios) = {avg:.1f}/100 ==="
    )


if __name__ == "__main__":
    asyncio.run(main())

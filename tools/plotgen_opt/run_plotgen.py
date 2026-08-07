"""运行 plot_gen Agent 并采集逐场景指标（基线/迭代共用）。

用法：
    python tools/plotgen_opt/run_plotgen.py <tag> [s1,s2,...]

每个场景输出到 tools/plotgen_opt/results/<tag>/<scenario>/
    - events.json       所有 SSE 事件（含时间戳与工具调用摘要）
    - trace.jsonl       Agent 运行追踪日志副本
    - summary.json      指标汇总（轮数/时长/成功率/质量评分）
"""

import asyncio
import json
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
from backend.modules.plot_gen.service import PlotGenService  # noqa: E402

load_config("config.json")

from backend.core.config import get_config  # noqa: E402

from plotgen_opt.gen_data import SCENARIOS  # noqa: E402


def quality_score(metrics: dict[str, Any]) -> dict[str, int]:
    """启发式图表质量评分（0-100）。"""
    score = 0
    detail: dict[str, int] = {}
    artifacts = {a["path"] for a in metrics.get("artifacts", [])}

    # 1) 核心矢量产物齐全（PDF + SVG + TikZ 源）
    if {"artifacts/figure.pdf", "artifacts/figure.svg"} <= artifacts:
        detail["vector_outputs"] = 25
    elif {"artifacts/figure.pdf"} <= artifacts:
        detail["vector_outputs"] = 15
    else:
        detail["vector_outputs"] = 0

    # 2) 脚本执行成功
    detail["execution_success"] = 15 if metrics.get("execution_success") is True else 0
    # 3) TikZ 编译成功
    detail["tikz_success"] = 15 if metrics.get("tikz_success") is True else 0
    # 4) 轮数控制（越少越好）
    rounds = metrics.get("rounds", 99)
    detail["rounds"] = 10 if rounds <= 10 else (5 if rounds <= 16 else 0)
    # 5) 无失败警告（无重复失败/无 finish 前未编译警告）
    detail["no_warnings"] = 10 if metrics.get("warnings", 0) == 0 else 0
    # 6) PNG 预览产物（前端可直接预览）
    detail["png_preview"] = 10 if any(p.endswith(".png") for p in artifacts) else 0
    # 7) 最终 summary 非空
    detail["summary"] = 5 if metrics.get("summary") else 0
    # 8) 未触发 max_rounds 截断
    detail["not_truncated"] = 10 if metrics.get("truncated") is False else 0

    score = sum(detail.values())
    return {"score": score, "detail": detail}


async def run_scenario(
    service: PlotGenService,
    llm: Any,
    key: str,
    spec: dict[str, Any],
    tag: str,
    out_root: Path,
) -> dict[str, Any]:
    data_path = out_root.parent / "data" / spec["file"]
    out_dir = out_root / tag / key
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    upload = await service.create_session_from_upload(
        data_path.read_bytes(),
        spec["file"],
        description=spec["description"],
        style=spec["style"],
    )
    session_id = upload["session_id"]
    t_upload = time.perf_counter()

    events: list[dict[str, Any]] = []
    phase = "recommendation"
    phase_start = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        async for ev in service.generate(
            session_id,
            spec["description"],
            spec["style"],
            "zh",
            llm=llm,
        ):
            now = time.perf_counter()
            events.append({"ts_ms": round((now - t0) * 1000, 1), **ev})
            if ev["type"] == "chart_plan" and phase == "recommendation":
                timings["recommendation_s"] = round(now - phase_start, 2)
                phase = "agent"
                phase_start = now
    except Exception as e:  # noqa: BLE001
        events.append(
            {
                "ts_ms": round((time.perf_counter() - t0) * 1000, 1),
                "type": "error",
                "message": str(e),
            }
        )
    timings["agent_s"] = round(time.perf_counter() - phase_start, 2)
    timings["total_s"] = round(time.perf_counter() - t0, 2)
    timings["upload_s"] = round(t_upload - t0, 2)

    sandbox = get_config().features.plot_gen.output_dir
    sandbox_path = Path(sandbox).resolve() / session_id
    trace_src = sandbox_path / "trace.jsonl"
    if trace_src.exists():
        shutil.copy2(trace_src, out_dir / "trace.jsonl")

    # 提取指标
    tool_calls: dict[str, int] = {}
    warnings = 0
    errors = 0
    summary = ""
    execution_success = None
    tikz_success = None
    rounds = None
    truncated = False
    chart_plan: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    for ev in events:
        t = ev["type"]
        if t == "tool_call":
            tool_calls[ev["tool"]] = tool_calls.get(ev["tool"], 0) + 1
        elif t == "warning":
            warnings += 1
        elif t == "error":
            errors += 1
        elif t == "execution":
            execution_success = ev.get("success")
        elif t == "tikz":
            tikz_success = ev.get("success")
        elif t == "chart_plan":
            chart_plan = ev.get("chart_plan", {})
        elif t == "done":
            summary = ev.get("summary", "")
            artifacts = ev.get("artifacts", [])
    # 从 trace 补全 rounds
    if trace_src.exists():
        for line in trace_src.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "run_end":
                rounds = rec.get("data", {}).get("rounds")
    truncated = rounds is not None and rounds >= int(
        get_config().features.plot_gen.agent_max_rounds
    )

    metrics = {
        "scenario": key,
        "description": spec["description"],
        "session_id": session_id,
        "sandbox": str(sandbox_path),
        **timings,
        "tool_calls": tool_calls,
        "rounds": rounds,
        "truncated": truncated,
        "execution_success": execution_success,
        "tikz_success": tikz_success,
        "warnings": warnings,
        "errors": errors,
        "summary": summary[:500],
        "chart_plan": chart_plan,
        "artifacts": artifacts,
        "event_count": len(events),
        "event_types": sorted({e["type"] for e in events}),
    }
    metrics.update(quality_score(metrics))

    (out_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{key}] rounds={rounds} exec={execution_success} tikz={tikz_success} "
        f"score={metrics['score']} total={timings['total_s']}s"
    )
    return metrics


async def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 else list(SCENARIOS)
    out_root = Path(__file__).resolve().parent / "results"
    out_root.mkdir(parents=True, exist_ok=True)

    service = PlotGenService()
    llm = get_llm_client()
    all_metrics: dict[str, Any] = {}
    for key in keys:
        if key not in SCENARIOS:
            print(f"unknown scenario: {key} (available: {', '.join(SCENARIOS)})")
            continue
        spec = SCENARIOS[key]
        all_metrics[key] = await run_scenario(service, llm, key, spec, tag, out_root)

    tag_dir = out_root / tag
    tag_dir.mkdir(parents=True, exist_ok=True)
    (tag_dir / "all_summary.json").write_text(
        json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== ALL ===")
    for key, m in all_metrics.items():
        print(
            f"{key}: score={m['score']} rounds={m['rounds']} "
            f"exec={m['execution_success']} tikz={m['tikz_success']} "
            f"warn={m['warnings']} err={m['errors']} time={m['total_s']}s"
        )


if __name__ == "__main__":
    asyncio.run(main())

# -*- coding: utf-8 -*-
"""分析 style_modify Agent 运行追踪，定位失败与低效环节。

用法：
    python tools/stylemodify_opt/analyze_trace.py <tag> [scenario1,scenario2,...]

输出：
    - 轮数、工具调用分布、失败工具、编译尝试与状态
    - 每轮 LLM 文本摘要 + 工具调用序列（可读 timeline）
    - 写入 results/<tag>/<scenario>/trace_analysis.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # tools/


def load_trace(result_dir: Path) -> list[dict]:
    trace_path = result_dir / "trace.jsonl"
    events = []
    if trace_path.exists():
        for line in trace_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _arg_summary(args: dict) -> dict:
    return (
        {k: v for k, v in args.items() if k != "content"}
        if isinstance(args, dict)
        else args
    )


def analyze(result_dir: Path) -> dict:
    events = load_trace(result_dir)
    run_start = next((e for e in events if e.get("type") == "run_start"), {})
    run_end = next((e for e in events if e.get("type") == "run_end"), {})

    tool_calls: dict[str, int] = {}
    failed_tools: list[dict] = []
    compiles: list[dict] = []
    stage_replacements: list[dict] = []
    llm_rounds: list[dict] = []
    timeline: list[dict] = []

    for ev in events:
        typ = ev.get("type")
        if typ == "llm":
            data = ev.get("data") or {}
            content = data.get("content", "") or ""
            calls = data.get("tool_calls") or []
            entry = {
                "round": len(llm_rounds) + 1,
                "content": content[:500],
                "tool_calls": [tc["function"]["name"] for tc in calls],
                "call_count": len(calls),
            }
            llm_rounds.append(entry)
            timeline.append({"event": "llm", **entry})
            for tc in calls:
                name = tc["function"]["name"]
                tool_calls[name] = tool_calls.get(name, 0) + 1
        elif typ == "tool_result":
            data = ev.get("data") or {}
            tool = data.get("tool", "")
            ok = data.get("ok")
            args = data.get("args") or {}
            result = data.get("result_text", "")
            if not ok:
                failed_tools.append(
                    {
                        "tool": tool,
                        "args_summary": _arg_summary(args),
                        "result": result[:300],
                    }
                )
            if tool == "compile_staged_project":
                event = data.get("event") or {}
                compiles.append(
                    {
                        "ok": ok,
                        "success": event.get("success"),
                        "status": event.get("status"),
                        "main_file": args.get("main_file", ""),
                        "result_head": result[:240],
                    }
                )
            elif tool == "stage_replace":
                stage_replacements.append(
                    {
                        "ok": ok,
                        "file_path": args.get("file_path", ""),
                        "content_length": len(args.get("content", "")),
                        "result_head": result[:200],
                    }
                )
            timeline_entry: dict = {
                "event": "tool_result",
                "tool": tool,
                "ok": ok,
                "args_summary": _arg_summary(args),
            }
            if tool == "compile_staged_project":
                event = data.get("event") or {}
                timeline_entry["success"] = event.get("success")
                timeline_entry["status"] = event.get("status")
            timeline.append(timeline_entry)
        elif typ == "warning":
            timeline.append(
                {"event": "warning", "message": ev.get("data", {}).get("message", "")}
            )
        elif typ == "error":
            timeline.append(
                {"event": "error", "message": ev.get("data", {}).get("message", "")}
            )

    last_compile_idx = -1
    for i, entry in enumerate(timeline):
        if (
            entry.get("event") == "tool_result"
            and entry.get("tool") == "compile_staged_project"
            and entry.get("status") in ("passed", "failed")
        ):
            last_compile_idx = i
    first_finish_idx = -1
    for i, entry in enumerate(timeline):
        if entry.get("event") == "tool_result" and entry.get("tool") == "finish":
            first_finish_idx = i
            break
    stale_finish_risks = [
        {
            "file_path": e["args_summary"].get("file_path", ""),
            "after_last_compile": True,
        }
        for i, e in enumerate(timeline)
        if (
            last_compile_idx >= 0
            and e.get("event") == "tool_result"
            and e.get("tool") == "stage_replace"
            and e.get("ok") is True
            and i > last_compile_idx
            and (first_finish_idx < 0 or i < first_finish_idx)
        )
    ]

    analysis = {
        "rounds": len(llm_rounds),
        "llm_no_tool_rounds": sum(1 for r in llm_rounds if r["call_count"] == 0),
        "tool_call_distribution": tool_calls,
        "failed_tool_calls": failed_tools,
        "compile_attempts": len(compiles),
        "compile_statuses": [c["status"] for c in compiles],
        "compile_successes": sum(1 for c in compiles if c["success"] is True),
        "compile_failures": sum(1 for c in compiles if c["success"] is False),
        "stale_finish_risks": stale_finish_risks,
        "stage_replacements": len(stage_replacements),
        "staged_paths": sorted({s["file_path"] for s in stage_replacements}),
        "final_compile_success": (run_end.get("data") or {}).get("compile_success"),
        "run_end_summary": (run_end.get("data") or {}).get("summary", ""),
        "system_prompt": (run_start.get("data") or {}).get("system_prompt", "")[:2000],
        "llm_rounds": llm_rounds,
        "compile_events": compiles,
        "timeline": timeline,
    }
    (result_dir / "trace_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return analysis


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    tag = sys.argv[1]
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    out_root = ROOT / "stylemodify_opt" / "results" / tag
    if not out_root.exists():
        print(f"no results for tag '{tag}'")
        return
    scenarios = keys or sorted(p.name for p in out_root.iterdir() if p.is_dir())
    for key in scenarios:
        result_dir = out_root / key
        if not result_dir.exists():
            print(f"skip {key}: not found")
            continue
        a = analyze(result_dir)
        print(
            f"[{key}] rounds={a['rounds']} tools={a['tool_call_distribution']} "
            f"compile={a['compile_attempts']} "
            f"(statuses={a['compile_statuses']}) failed_tools={len(a['failed_tool_calls'])}"
        )


if __name__ == "__main__":
    main()

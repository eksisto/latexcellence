# -*- coding: utf-8 -*-
"""分析 template_gen Agent 运行追踪，定位质量缺失发生在哪个环节。

用法：
    python tools/templategen_opt/analyze_trace.py <tag> [scenario1,scenario2,...]

输出：
    - 轮数、工具调用分布、失败工具、编译尝试与失败次数
    - 每轮 LLM 文本摘要 + 工具调用序列（可读 timeline）
    - 写入 results/<tag>/<scenario>/trace_analysis.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def analyze(result_dir: Path) -> dict:
    events = load_trace(result_dir)
    run_start = next((e for e in events if e.get("type") == "run_start"), {})
    run_end = next((e for e in events if e.get("type") == "run_end"), {})

    tool_calls: dict[str, int] = {}
    failed_tools: list[dict] = []
    compiles: list[dict] = []
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
                        "args_summary": {
                            k: v for k, v in args.items() if k != "content"
                        },
                        "result": result[:300],
                    }
                )
            if tool == "compile_latex":
                compiles.append(
                    {
                        "success": ok and (data.get("event") or {}).get("success"),
                        "ok": ok,
                        "main": args.get("main_file", ""),
                        "result_head": result[:200],
                    }
                )
            timeline.append(
                {
                    "event": "tool_result",
                    "tool": tool,
                    "ok": ok,
                    "args_summary": {k: v for k, v in args.items() if k != "content"},
                }
            )
        elif typ == "warning":
            timeline.append(
                {"event": "warning", "message": ev.get("data", {}).get("message", "")}
            )
        elif typ == "error":
            timeline.append(
                {"event": "error", "message": ev.get("data", {}).get("message", "")}
            )

    compile_failures = [c for c in compiles if c["success"] is False]
    compile_successes = [c for c in compiles if c["success"] is True]
    write_calls = [
        ev
        for ev in events
        if ev.get("type") == "tool_result"
        and (ev.get("data") or {}).get("tool") == "write_file"
    ]
    rewrites = sum(1 for ev in write_calls if (ev.get("data") or {}).get("ok") is True)

    analysis = {
        "rounds": len(llm_rounds),
        "llm_no_tool_rounds": sum(1 for r in llm_rounds if r["call_count"] == 0),
        "tool_call_distribution": tool_calls,
        "failed_tool_calls": failed_tools,
        "compile_attempts": len(compiles),
        "compile_successes": len(compile_successes),
        "compile_failures": len(compile_failures),
        "write_file_calls": rewrites,
        "final_compile_success": (run_end.get("data") or {}).get("compile_success"),
        "run_end_summary": (run_end.get("data") or {}).get("summary", ""),
        "description": (run_start.get("data") or {}).get("description", ""),
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
    out_root = ROOT / "templategen_opt" / "results" / tag
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
            f"compile={a['compile_attempts']} (ok={a['compile_successes']}/fail={a['compile_failures']}) "
            f"failed_tools={len(a['failed_tool_calls'])}"
        )


if __name__ == "__main__":
    main()

"""分析 trace.jsonl：输出每轮工具调用序列、失败点与 LLM 决策摘要。"""

import json
import sys
from pathlib import Path


def summarize(path: Path) -> None:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    print(f"### {path.parent.name} — {len(events)} events")
    round_no = 0
    last_tool = ""
    for ev in events:
        t = ev.get("type")
        data = ev.get("data", {})
        if t == "run_start":
            print(
                f"run_start: {data.get('run_type')} max_rounds={data.get('max_rounds')}"
            )
        elif t == "llm":
            calls = data.get("tool_calls") or []
            names = [c["function"]["name"] for c in calls]
            content = (data.get("content") or "").replace("\n", " ")[:140]
            round_no += 1
            print(f"  R{round_no:02d} llm: tools={names} | content={content[:120]}")
        elif t == "tool_result":
            ok = data.get("ok")
            tool = data.get("tool")
            result_text = (data.get("result_text") or "").replace("\n", " ")[:180]
            mark = "OK " if ok else "FAIL"
            print(f"      -> {mark} {tool}: {result_text[:160]}")
            if not ok:
                args = data.get("args", {})
                print(f"         args={json.dumps(args, ensure_ascii=False)[:200]}")
        elif t == "warning":
            print(f"  WARN: {data.get('message', '')[:160]}")
        elif t == "error":
            print(f"  ERROR: {data.get('message', '')[:160]}")
        elif t == "run_end":
            print(
                f"run_end: rounds={data.get('rounds')} exec_success={data.get('execution_success')} "
                f"tikz_success={data.get('tikz_success')}"
            )
    print()


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    base = Path(__file__).resolve().parent / "results" / tag
    targets = []
    if keys:
        for k in keys:
            p = base / k / "trace.jsonl"
            if p.exists():
                targets.append(p)
            else:
                print(f"missing: {p}")
    else:
        targets = sorted(base.glob("*/trace.jsonl"))
    for p in targets:
        summarize(p)


if __name__ == "__main__":
    main()

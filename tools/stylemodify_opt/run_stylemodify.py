# -*- coding: utf-8 -*-
"""运行 style_modify 项目 Agent 并采集逐场景指标（基线/迭代共用）。

用法：
    python tools/stylemodify_opt/run_stylemodify.py <tag> [s1,s2,...]
        [--no-apply] [--include-holdout] [--dry-run]

每个场景输出到 tools/stylemodify_opt/results/<tag>/<scenario>/
    - events.json        所有 SSE 事件（含时间戳）
    - trace.jsonl        Agent 运行追踪日志副本
    - session/           会话快照（baseline / overlay / meta / trace）
    - summary.json       指标汇总（轮数/时长/编译/proposal/需求遵守/质量分）
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

load_config("config.json")

from backend.core.config import get_config  # noqa: E402
from backend.core.workspace_manager import (  # noqa: E402
    get_project_manager_for_workspace,
)
from backend.modules.style_modify.service import StyleModifyService  # noqa: E402

from generality_gate import resolve_scenarios  # noqa: E402
from stylemodify_opt.scenarios import (  # noqa: E402
    HOLDOUT_SCENARIOS,
    SCENARIOS,
    build_scenario_projects,
    check_allowed_targets,
    check_preservation,
    check_requirements,
)
from tex_path import ensure_tex_path  # noqa: E402


def _ignore_session_files(directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name.endswith((".aux", ".log", ".fls", ".fdb_latexmk", ".out", ".toc")):
            ignored.add(name)
    return ignored


def quality_score(metrics: dict[str, Any]) -> dict[str, int]:
    """启发式样式质量评分（0-100）。"""
    detail: dict[str, int] = {}
    detail["compile_success"] = 15 if metrics.get("compile_success") is True else 0
    detail["proposal_applicable"] = (
        10 if metrics.get("proposal_applicable") is True else 0
    )
    detail["staged_files"] = 10 if metrics.get("staged_file_count", 0) > 0 else 0
    detail["requirements"] = round(metrics.get("req_ratio", 0) * 30)
    detail["preservation"] = round(metrics.get("preservation_ratio", 0) * 15)
    detail["no_warnings"] = 5 if metrics.get("warnings", 0) == 0 else 0
    detail["no_errors"] = 5 if metrics.get("errors", 0) == 0 else 0
    rounds = metrics.get("rounds")
    detail["rounds"] = 5 if rounds is not None and rounds <= 10 else 0
    detail["not_truncated"] = 5 if metrics.get("truncated") is False else 0
    return {"score": sum(detail.values()), "detail": detail, "max_possible": 100}


def _compile_available() -> bool:
    return shutil.which("latexmk") is not None


async def run_scenario(
    service: StyleModifyService,
    llm: Any,
    key: str,
    project_root: Path,
    tag: str,
    out_root: Path,
    *,
    do_apply_test: bool,
) -> dict[str, Any]:
    out_dir = out_root / tag / key
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace_id = f"stylemodify-opt-{tag}-{key}-{int(time.time() * 1000) % 100000}"
    project = get_project_manager_for_workspace(workspace_id)
    project.open(str(project_root))
    spec = SCENARIOS[key]

    t0 = time.perf_counter()
    events: list[dict[str, Any]] = []
    try:

        async def _drain() -> None:
            async for ev in service.agent_run(
                workspace_id,
                spec["requirement"],
                "zh",
                llm=llm,
            ):
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
    session_event = next((e for e in events if e["type"] == "session"), None)
    session_id = session_event.get("session_id") if session_event else None

    snapshot_root: Path | None = None
    if session_id:
        cfg = get_config()
        session_dir = Path(cfg.features.style_modify.agent.sessions_dir) / session_id
        if session_dir.is_dir():
            snapshot_root = out_dir / "session"
            shutil.copytree(
                session_dir,
                snapshot_root,
                ignore=_ignore_session_files,
            )
            trace_src = session_dir / "trace.jsonl"
            if trace_src.exists():
                shutil.copy2(trace_src, out_dir / "trace.jsonl")

    (out_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    session_data: dict[str, Any] = {}
    proposal: dict[str, Any] | None = None
    if session_id:
        try:
            session_data = service.get_agent_session(session_id, workspace_id)
            proposal = session_data.get("proposal")
        except ValueError as e:
            events.append(
                {
                    "ts_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "type": "error",
                    "message": f"session read failed: {e}",
                }
            )

    # trace 指标
    rounds: int | None = None
    trace_events: list[dict[str, Any]] = []
    trace_path = out_dir / "trace.jsonl"
    if trace_path.exists():
        for line in trace_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if not line.strip():
                continue
            try:
                trace_events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    run_end = next((e for e in trace_events if e.get("type") == "run_end"), {})
    rounds = (run_end.get("data") or {}).get("rounds")

    # SSE 指标
    tool_calls: dict[str, int] = {}
    warnings: list[str] = []
    errors = 0
    compile_events: list[dict[str, Any]] = []
    finish_calls = 0
    for ev in events:
        t = ev["type"]
        if t == "tool_call":
            tool_calls[ev["tool"]] = tool_calls.get(ev["tool"], 0) + 1
        elif t == "warning":
            warnings.append(ev.get("message", ""))
        elif t == "error":
            errors += 1
        elif t == "compile":
            compile_events.append(ev)
        elif t == "tool_result" and ev.get("tool") == "finish":
            finish_calls += 1

    failed_tool_calls: list[dict[str, Any]] = []
    for ev in trace_events:
        if ev.get("type") != "tool_result":
            continue
        data = ev.get("data") or {}
        if data.get("ok") is False:
            failed_tool_calls.append(
                {
                    "tool": data.get("tool"),
                    "args": data.get("args"),
                    "result": (data.get("result_text") or "")[:300],
                }
            )

    last_compile = compile_events[-1] if compile_events else {}
    compile_status = last_compile.get("status")
    compile_success = compile_status == "passed"
    truncated = finish_calls == 0 or (
        rounds is not None
        and rounds >= int(get_config().features.style_modify.agent.max_rounds)
    )

    proposal_files = (proposal or {}).get("files", [])
    overlay_root = snapshot_root / "overlay" if snapshot_root else None
    req = check_requirements(
        key, proposal_files, project_root, overlay_root or project_root
    )
    allowed = check_allowed_targets(key, proposal_files)
    preservation = check_preservation(key, project_root, overlay_root or project_root)

    apply_test: dict[str, Any] = {"ran": False}
    if do_apply_test and session_id and proposal:
        try:
            operation = await service.apply_agent_proposal(
                session_id,
                workspace_id,
                proposal["revision"],
            )
            undo = await service.undo_agent_operation(
                operation["operation_id"],
                workspace_id,
            )
            apply_test = {
                "ran": True,
                "ok": True,
                "operation_id": operation["operation_id"],
                "undo_status": undo["status"],
            }
        except Exception as e:  # noqa: BLE001
            apply_test = {"ran": True, "ok": False, "error": str(e)}

    metrics: dict[str, Any] = {
        "tag": tag,
        "scenario": key,
        "label": spec["label"],
        "requirement": spec["requirement"],
        "workspace_id": workspace_id,
        "session_id": session_id,
        "total_s": total_s,
        "rounds": rounds,
        "truncated": truncated,
        "tool_calls": tool_calls,
        "failed_tool_calls": failed_tool_calls,
        "compile_available": _compile_available(),
        "compile_attempts": len(compile_events),
        "compile_status": compile_status,
        "compile_success": compile_success if compile_events else None,
        "warnings": len(warnings),
        "warning_messages": warnings,
        "errors": errors,
        "event_count": len(events),
        "event_types": sorted({e["type"] for e in events}),
        "proposal": proposal,
        "proposal_applicable": bool(proposal and proposal.get("applicable")),
        "staged_file_count": len(proposal_files),
        "staged_paths": [f["path"] for f in proposal_files],
        "summary": (proposal or {}).get("summary", ""),
        "req": req,
        "req_ratio": req["ratio"],
        "allowed_targets": allowed,
        "preservation": preservation,
        "preservation_ratio": preservation["ratio"],
        "apply_test": apply_test,
    }
    metrics["quality"] = quality_score(metrics)

    (out_dir / "summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{key}] rounds={rounds} compile={compile_status or '-'} "
        f"req={req['passed']}/{req['total']} preserve={preservation['passed']}/"
        f"{preservation['total']} score={metrics['quality']['score']} "
        f"warn={len(warnings)} err={errors} total={total_s}s"
    )
    return metrics


async def main() -> None:
    ensure_tex_path()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_apply_test = "--no-apply" not in sys.argv[1:]
    include_holdout = "--include-holdout" in sys.argv[1:]
    tag = args[0] if args else "baseline"
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
    out_root.mkdir(parents=True, exist_ok=True)

    # 评测会话与用户会话隔离，运行后不污染 style_modify_sessions/
    runtime_root = Path(__file__).resolve().parent / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    cfg = get_config()
    cfg.features.style_modify.agent.sessions_dir = str(runtime_root / "sessions")

    projects = build_scenario_projects(Path(__file__).resolve().parent / "data")
    service = StyleModifyService()
    llm = get_llm_client()
    all_metrics: dict[str, Any] = {}
    for key in keys:
        if key not in SCENARIOS:
            print(f"unknown scenario: {key} (available: {', '.join(SCENARIOS)})")
            continue
        print(f"\n=== running {tag}/{key} ({SCENARIOS[key]['label']}) ===")
        all_metrics[key] = await run_scenario(
            service,
            llm,
            key,
            projects[key],
            tag,
            out_root,
            do_apply_test=do_apply_test,
        )

    all_path = out_root / tag / "all_summary.json"
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
    if merged:
        avg = sum(m["quality"]["score"] for m in merged.values()) / len(merged)
        print(
            f"\n=== {tag} avg heuristic score ({len(merged)} scenarios) = "
            f"{avg:.1f}/100 ==="
        )


if __name__ == "__main__":
    asyncio.run(main())

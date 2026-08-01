"""科研绘图自主 Agent。"""

import json
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt
from backend.modules.plot_gen.executor import PlotExecutor
from backend.modules.plot_gen.models import ChartPlan, DataProfile, PlotSessionMeta
from backend.modules.plot_gen.trace import append_trace, now_iso

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_plot_output_dir(cfg: Any | None = None) -> Path:
    """返回 plot_gen session 输出根目录。"""
    cfg = cfg or get_config()
    output_dir = Path(cfg.features.plot_gen.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    return output_dir


def load_plot_sandbox(output_dir: str | Path, session_id: str) -> Path:
    """按 session_id 解析沙箱目录并校验边界。"""
    if not session_id or not session_id.strip():
        raise ValueError("session_id cannot be empty")
    if not SESSION_ID_RE.match(session_id):
        raise ValueError("session_id contains invalid characters")
    base = Path(output_dir)
    if not base.is_absolute():
        base = Path.cwd() / base
    root = base.resolve()
    sandbox = (root / session_id).resolve()
    if not sandbox.is_relative_to(root):
        raise ValueError("session_id resolves outside the sandbox root")
    return sandbox


def safe_read_plot_file(sandbox: Path, rel_path: str) -> tuple[str | None, str | None]:
    """安全读取沙箱内文件，返回 (content, error)。"""
    from backend.utils.path_guard import resolve_project_path

    try:
        resolved = resolve_project_path(sandbox, rel_path)
    except ValueError as e:
        return None, str(e)
    if not resolved.exists() or not resolved.is_file():
        return None, f"File not found: {rel_path}"
    return resolved.read_text(encoding="utf-8", errors="replace"), None


def read_session_meta(sandbox: Path) -> PlotSessionMeta:
    meta_path = sandbox / "meta.json"
    if not meta_path.exists():
        raise ValueError("Session meta not found")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return PlotSessionMeta.model_validate(data)


def write_session_meta(sandbox: Path, meta: PlotSessionMeta) -> None:
    meta_path = sandbox / "meta.json"
    meta_path.write_text(
        meta.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class PlotGenAgent:
    """基于 Function Calling 的科研绘图 Agent。"""

    def __init__(self) -> None:
        self._logger = get_logger("plot_gen.agent")

    async def generate(
        self,
        session_id: str,
        description: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
        style: str = "nature",
        chart_plan: ChartPlan | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = get_config()
        if not cfg.llm.api_key and llm is None:
            yield {"type": "error", "message": "No API key configured"}
            return
        if not cfg.features.plot_gen.agent_enabled:
            yield {"type": "error", "message": "Agent mode disabled by config"}
            return

        if llm is None:
            llm = get_llm_client()

        sandbox = load_plot_sandbox(cfg.features.plot_gen.output_dir, session_id)
        meta = read_session_meta(sandbox)
        meta.description = description or meta.description
        meta.style = style or meta.style
        if chart_plan is not None:
            meta.chart_plan = chart_plan
        write_session_meta(sandbox, meta)

        profile = meta.data_profile
        if profile is None:
            yield {"type": "error", "message": "Data profile is missing"}
            return

        plan_text = (
            meta.chart_plan.model_dump_json(indent=2)
            if meta.chart_plan
            else "No chart plan generated"
        )
        system_prompt = get_prompt(
            "plot_gen",
            "agent_system",
            profile=profile.raw_summary,
            chart_plan=plan_text,
            style=meta.style,
            language=language,
            max_rounds=cfg.features.plot_gen.agent_max_rounds,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Generate a publication-ready figure for:\n\n{description}\n\n"
                    f"Data file in sandbox: data/{meta.data_file}\n"
                    "Respond in {language} for explanatory text.".format(
                        language=language
                    )
                ),
            },
        ]
        async for event in self._run_agent(
            session_id,
            messages,
            llm,
            run_type="generate",
            style=meta.style,
        ):
            yield event

    async def refine(
        self,
        session_id: str,
        message: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = get_config()
        if llm is None:
            llm = get_llm_client()
        sandbox = load_plot_sandbox(cfg.features.plot_gen.output_dir, session_id)
        meta = read_session_meta(sandbox)
        messages = list(meta.messages)
        refine_prompt = get_prompt(
            "plot_gen", "refine_system", message=message, language=language
        )
        messages.append(
            {
                "role": "user",
                "content": f"{refine_prompt}\n\nUser refinement: {message}",
            }
        )
        async for event in self._run_agent(
            session_id,
            messages,
            llm,
            run_type="refine",
            style=meta.style,
        ):
            yield event

    async def _run_agent(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        llm: Any,
        run_type: str = "generate",
        style: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = get_config()
        fcfg = cfg.features.plot_gen
        sandbox = load_plot_sandbox(cfg.features.plot_gen.output_dir, session_id)
        executor = PlotExecutor(sandbox, cfg, self._logger, style_key=style)
        tools = self._build_tools_schema()
        max_rounds = fcfg.agent_max_rounds
        final_summary = ""
        execution_state = {"called": False, "success": None}
        tikz_state = {"called": False, "success": None}
        run_id = uuid.uuid4().hex[:12]
        rounds_used = 0
        last_fail = {"tool": "", "text": "", "count": 0}
        repeat_limit = 3

        await append_trace(
            sandbox,
            {
                "ts": now_iso(),
                "run_id": run_id,
                "type": "run_start",
                "data": {
                    "run_type": run_type,
                    "system_prompt": messages[0]["content"] if messages else "",
                    "messages": messages,
                    "max_rounds": max_rounds,
                },
            },
        )

        for round_idx in range(max_rounds):
            rounds_used = round_idx + 1
            if round_idx == 0:
                yield {
                    "type": "plan",
                    "content": f"Starting scientific plotting agent (up to {max_rounds} rounds)...",
                }
            try:
                response = await llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=cfg.llm.temperature,
                    max_tokens=cfg.llm.max_tokens,
                )
            except Exception as e:
                self._logger.error("plot_gen chat_with_tools failed: %s", e)
                await append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "error",
                        "data": {"message": str(e)},
                    },
                )
                yield {"type": "error", "message": str(e)}
                return

            choice = response.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls
            if msg.content:
                yield {"type": "plan", "content": msg.content}
                final_summary = msg.content

            await append_trace(
                sandbox,
                {
                    "ts": now_iso(),
                    "run_id": run_id,
                    "type": "llm",
                    "data": {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": getattr(tc, "type", "function"),
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in (tool_calls or [])
                        ],
                    },
                },
            )

            if not tool_calls:
                # 正常收尾：把最终 assistant 回复并入会话并持久化，
                # 否则 refine 读取 meta.messages 时会丢失这一轮上下文。
                messages.append(msg.model_dump())
                self._persist_messages(
                    sandbox, messages, execution_state, tikz_state, executor
                )
                break

            messages.append(msg.model_dump())
            finish_this_round = False
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except Exception:
                    tool_args = {}
                yield {
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": _summarize_args(tool_name, tool_args),
                }
                try:
                    result_text, result_event = await executor.execute(
                        tool_name, tool_args
                    )
                except Exception as e:
                    self._logger.warning("plot_gen tool exception %s: %s", tool_name, e)
                    result_text = f"Tool execution failed: {e}"
                    result_event = {
                        "tool": tool_name,
                        "ok": False,
                        "summary": result_text,
                    }

                await append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "tool_result",
                        "data": {
                            "tool": tool_name,
                            "args": tool_args,
                            "ok": result_event.get("ok"),
                            "result_text": result_text,
                            "event": result_event,
                        },
                    },
                )

                # 连续相同失败检测：阻止 Agent 盲目重试同一个失败的工具调用
                failed = (
                    result_event.get("ok") is False
                    or result_event.get("success") is False
                )
                if (
                    failed
                    and tool_name == last_fail["tool"]
                    and result_text == last_fail["text"]
                ):
                    last_fail["count"] += 1
                else:
                    last_fail["tool"] = tool_name if failed else ""
                    last_fail["text"] = result_text if failed else ""
                    last_fail["count"] = 1 if failed else 0
                if last_fail["count"] >= repeat_limit:
                    last_fail["count"] = 0
                    repeat_msg = (
                        f"STOP: the tool call `{tool_name}` has failed with the "
                        "identical result multiple times in a row. Do not retry the "
                        "same call. Read the last log/error and change your approach, "
                        "or call `finish` with an explicit warning that TikZ "
                        "compilation is blocked."
                    )
                    messages.append({"role": "system", "content": repeat_msg})
                    yield {"type": "warning", "message": repeat_msg}

                if tool_name == "python_sandbox":
                    execution_state["called"] = True
                    execution_state["success"] = result_event.get("success")
                    yield {"type": "execution", **result_event}
                elif tool_name == "compile_tikz":
                    tikz_state["called"] = True
                    tikz_state["success"] = result_event.get("success")
                    yield {"type": "tikz", **result_event}
                else:
                    yield {"type": "tool_result", **result_event}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )

                if tool_name == "finish":
                    if fcfg.tikz_compile_enabled and (
                        not tikz_state["called"] or tikz_state["success"] is not True
                    ):
                        warning_msg = (
                            "Agent called finish without a successful TikZ "
                            "compilation. The TikZ artifact may be missing or invalid."
                        )
                        await append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "warning",
                                "data": {"message": warning_msg},
                            },
                        )
                        yield {
                            "type": "warning",
                            "message": warning_msg,
                        }
                    final_summary = tool_args.get("summary", final_summary)
                    finish_this_round = True
                    break

            self._persist_messages(
                sandbox, messages, execution_state, tikz_state, executor
            )
            if finish_this_round:
                break
        else:
            final_summary = final_summary or (
                f"Agent reached the maximum number of rounds {max_rounds}, automatically ending."
            )
            yield {
                "type": "plan",
                "content": f"Reached maximum rounds {max_rounds}, stopping.",
            }
            if fcfg.tikz_compile_enabled and (
                not tikz_state["called"] or tikz_state["success"] is not True
            ):
                warning_msg = (
                    "Reached maximum rounds without successful TikZ compilation."
                )
                await append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "warning",
                        "data": {"message": warning_msg},
                    },
                )
                yield {
                    "type": "warning",
                    "message": warning_msg,
                }

        await append_trace(
            sandbox,
            {
                "ts": now_iso(),
                "run_id": run_id,
                "type": "run_end",
                "data": {
                    "summary": final_summary,
                    "rounds": rounds_used,
                    "execution_success": execution_state["success"],
                    "tikz_success": tikz_state["success"],
                },
            },
        )

        files = executor.list_files()
        artifacts = executor.scan_artifacts()
        yield {
            "type": "done",
            "session_id": session_id,
            "sandbox": str(sandbox),
            "files": files,
            "artifacts": [a.model_dump() for a in artifacts],
            "summary": final_summary,
        }

    def _persist_messages(
        self,
        sandbox: Path,
        messages: list[dict[str, Any]],
        execution_state: dict[str, Any],
        tikz_state: dict[str, Any],
        executor: PlotExecutor,
    ) -> None:
        try:
            meta = read_session_meta(sandbox)
            meta.messages = messages
            meta.last_execution_success = execution_state["success"]
            meta.last_tikz_success = tikz_state["success"]
            meta.artifacts = executor.scan_artifacts()
            write_session_meta(sandbox, meta)
        except Exception as e:
            self._logger.warning("Failed to persist plot session meta: %s", e)

    @staticmethod
    def _build_tools_schema() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create or overwrite a file inside the plot sandbox. Use forward slashes, e.g. scripts/plot.py.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file inside the plot sandbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {"file_path": {"type": "string"}},
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List all files currently in the plot sandbox.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "python_sandbox",
                    "description": "Execute a Python script inside the sandbox and return stdout/stderr plus generated artifacts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "script_name": {
                                "type": "string",
                                "description": "Relative script path, e.g. scripts/plot.py.",
                            }
                        },
                        "required": ["script_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compile_tikz",
                    "description": "Compile a TikZ/PGFPlots .tex artifact in artifacts/ with latexmk.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tikz_file": {
                                "type": "string",
                                "description": "Relative TikZ file, e.g. artifacts/figure.tex.",
                            }
                        },
                        "required": ["tikz_file"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "stats_test",
                    "description": "Run a scipy statistical test on the staged data file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "data_file": {"type": "string"},
                            "test": {
                                "type": "string",
                                "enum": [
                                    "t-test",
                                    "wilcoxon",
                                    "anova",
                                    "kruskal",
                                    "chi2",
                                    "pearson",
                                    "spearman",
                                ],
                            },
                            "x": {"type": "string"},
                            "y": {"type": "string"},
                            "group": {"type": "string"},
                            "alternative": {
                                "type": "string",
                                "enum": ["two-sided", "less", "greater"],
                            },
                            "multiple_comparison": {
                                "type": "string",
                                "enum": ["none", "bonferroni", "fdr_bh"],
                            },
                        },
                        "required": ["data_file", "test"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_styles",
                    "description": "List available journal style presets.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "apply_style",
                    "description": "Change the style preset used for subsequent runs.",
                    "parameters": {
                        "type": "object",
                        "properties": {"style": {"type": "string"}},
                        "required": ["style"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "Signal that the plotting session is complete. Call only after python_sandbox and compile_tikz succeeded.",
                    "parameters": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                    },
                },
            },
        ]


_agent: PlotGenAgent | None = None


def get_plot_gen_agent() -> PlotGenAgent:
    global _agent
    if _agent is None:
        _agent = PlotGenAgent()
    return _agent


def _summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "write_file":
        content = args.get("content", "")
        return {
            "file_path": args.get("file_path", ""),
            "content_length": len(content),
            "preview": content[:300],
        }
    if tool_name in ("read_file", "python_sandbox", "compile_tikz"):
        return {
            key: value
            for key, value in args.items()
            if key in ("file_path", "script_name", "tikz_file")
        }
    if tool_name == "stats_test":
        return {k: v for k, v in args.items() if k != "data_file"} | {
            "data_file": args.get("data_file", "")
        }
    if tool_name == "finish":
        return {"summary": args.get("summary", "")}
    return args

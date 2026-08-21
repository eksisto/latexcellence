"""科研绘图自主 Agent。"""

import json
import posixpath
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt, render_pgfplots_reference
from backend.modules.plot_gen.data_parser import build_profile_context
from backend.modules.plot_gen.executor import PlotExecutor
from backend.modules.plot_gen.models import ChartPlan, DataProfile, PlotSessionMeta
from backend.modules.plot_gen.trace import append_trace, now_iso
from backend.utils.agent_file_tools import build_agent_file_tools_schema

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 影响最终图表产物的文件：脚本、spec 与数据 CSV 变更后必须重新生成/编译，
# 其余文件（说明、日志等）不参与“产物是否已验证”判定。
FIGURE_AFFECTING_ARTIFACT_SUFFIXES = (".tex", ".csv", ".json")


def _is_figure_affecting_path(rel_path: str) -> bool:
    """判断相对路径是否影响最终图表产物。

    手写 PGFPlots 回退允许任意 artifacts/*.csv 与 artifacts/*.tex，因此不能
    只精确匹配 data_ 前缀；先做 POSIX 归一化（处理 ./、重复斜杠等），再按
    scripts/ 前缀或 artifacts/ 下的 .tex/.csv/.json 后缀判定。
    （`..` 段会被 path_guard 在写入前拒绝，这里仍归一化以防解析规则变化。）
    """
    rel = posixpath.normpath((rel_path or "").replace("\\", "/"))
    if rel == "." or not rel:
        return False
    if rel.startswith("scripts/"):
        return True
    if rel.startswith("artifacts/"):
        return Path(rel).suffix.lower() in FIGURE_AFFECTING_ARTIFACT_SUFFIXES
    return False


def get_plot_output_dir(cfg: Any | None = None) -> Path:
    """返回 plot_gen session 输出根目录。

    多用户模式：按当前用户隔离到 <users_root>/<user_id>/plots/；
    单用户模式：返回存量全局路径（cfg.features.plot_gen.output_dir），行为不变。
    """
    from backend.core.multiuser import userspace

    cfg = cfg or get_config()
    legacy_root = Path(cfg.features.plot_gen.output_dir)
    if not legacy_root.is_absolute():
        legacy_root = Path.cwd() / legacy_root
    return userspace.user_resource_root("plots", legacy_root)


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

    async def _append_trace(self, sandbox: Path, event: dict[str, Any]) -> None:
        """追加追踪事件；写入失败仅记日志，不中断生成主流程。"""
        try:
            await append_trace(sandbox, event)
        except Exception as e:
            self._logger.warning("Failed to append plot_gen trace event: %s", e)

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

        if llm is None:
            llm = get_llm_client()

        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
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
            profile=build_profile_context(profile),
            chart_plan=plan_text,
            pgfplots_reference=render_pgfplots_reference(
                meta.chart_plan.chart_type if meta.chart_plan else None
            ),
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
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
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
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
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
        # 按工具族累计连续失败次数：python_sandbox / compile_tikz 各自达到阈值时
        # 注入强约束消息，强制 Agent 换思路（简化图表/tikz_spec 回修/finish 带警告），
        # 防止在故障 TikZ 产物上反复小修小补形成死循环。
        fail_budget: dict[str, int] = {"python_sandbox": 0, "compile_tikz": 0}
        fail_budget_limit = 3
        # 产物验证状态：compile_tikz 成功后为 True；任何影响产物的写入、
        # apply_style 或 python_sandbox 重跑都会置回 False，直到再次编译成功。
        verified = False
        stale_finish_blocked = 0

        await self._append_trace(
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
                await self._append_trace(
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

            await self._append_trace(
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
                # 首轮退化防护：generate 的第一个响应只输出文字、不调用任何
                # 工具时，直接收尾会得到零产物的空会话。注入强约束消息让
                # LLM 重试一次（仅首轮一次，此后仍按正常收尾处理）。
                if run_type == "generate" and round_idx == 0:
                    messages.append(msg.model_dump())
                    nudge_msg = (
                        "You replied with explanatory text but called NO tool, "
                        "so nothing was produced. Start the plotting workflow "
                        "NOW: call `write_file` to create `scripts/plot.py` "
                        "(following OUTPUTS and TIKZ PROTOCOL), then run it "
                        "with `python_sandbox`. Respond with tool calls, not "
                        "prose."
                    )
                    messages.append({"role": "system", "content": nudge_msg})
                    await self._append_trace(
                        sandbox,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "warning",
                            "data": {
                                "message": (
                                    "First-round reply had no tool call; "
                                    "nudged the LLM to start the plotting tools."
                                )
                            },
                        },
                    )
                    yield {
                        "type": "plan",
                        "content": (
                            "First reply contained no tool call; retrying with "
                            "an explicit instruction to start the plotting tools."
                        ),
                    }
                    continue
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
                # 成功后验证循环防呆：compile_tikz 已成功且此后没有修改任何
                # 影响产物的文件时，read/list/rerun/compile 都是无效验证，
                # 直接拦截并提示 finish，避免在已收敛结果上空转。
                verified_loop_blocked = (
                    fcfg.tikz_compile_enabled
                    and verified
                    and tool_name
                    in ("read_file", "list_files", "python_sandbox", "compile_tikz")
                )
                # 未验证交付防呆：compile 成功后修改了影响产物的文件又直接
                # finish 时，第一次在执行前拦截（stale_finish_blocked==0），
                # 第二次允许但带警告，避免 Agent 被困在无法收尾的死循环。
                blocked_stale_finish = (
                    tool_name == "finish"
                    and fcfg.tikz_compile_enabled
                    and tikz_state["success"] is True
                    and not verified
                    and stale_finish_blocked == 0
                )
                # 被 guard 拦截的调用不是真实执行：不得覆盖 execution/tikz 状态，
                # 也不得进入失败统计（last_fail / fail_budget）。
                guard_blocked = verified_loop_blocked or blocked_stale_finish
                if verified_loop_blocked:
                    guard_msg = (
                        "VERIFIED-LOOP BLOCK: `compile_tikz` already succeeded "
                        "and no figure-affecting file was modified since. Do not "
                        "call `read_file`/`list_files`/`python_sandbox`/"
                        "`compile_tikz` again; call `finish` now."
                    )
                    messages.append({"role": "system", "content": guard_msg})
                    await self._append_trace(
                        sandbox,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "warning",
                            "data": {"message": guard_msg},
                        },
                    )
                    yield {"type": "warning", "message": guard_msg}
                    result_text = guard_msg
                    result_event = {
                        "tool": tool_name,
                        "ok": False,
                        "summary": guard_msg,
                    }
                elif blocked_stale_finish:
                    stale_msg = (
                        "STALE FINISH BLOCKED: figure-affecting files were "
                        "modified or `python_sandbox` re-ran since the last "
                        "successful `compile_tikz`, so the artifacts may no "
                        "longer match the current source. Rerun "
                        "`python_sandbox` and `compile_tikz`, then call "
                        "`finish` again. If you really cannot compile, call "
                        "`finish` once more to override with a warning."
                    )
                    messages.append({"role": "system", "content": stale_msg})
                    await self._append_trace(
                        sandbox,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "warning",
                            "data": {"message": stale_msg},
                        },
                    )
                    yield {"type": "warning", "message": stale_msg}
                    result_text = stale_msg
                    result_event = {
                        "tool": "finish",
                        "ok": False,
                        "summary": stale_msg,
                    }
                    stale_finish_blocked += 1
                else:
                    try:
                        result_text, result_event = await executor.execute(
                            tool_name, tool_args
                        )
                    except Exception as e:
                        self._logger.warning(
                            "plot_gen tool exception %s: %s", tool_name, e
                        )
                        result_text = f"Tool execution failed: {e}"
                        result_event = {
                            "tool": tool_name,
                            "ok": False,
                            "summary": result_text,
                        }

                await self._append_trace(
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

                # 更新产物验证状态：影响产物的写入/风格切换/脚本重跑都会
                # 使上一次编译结果失效，只有 compile_tikz 成功才重新验证。
                if guard_blocked:
                    pass
                elif tool_name == "write_file" and result_event.get("ok") is True:
                    if _is_figure_affecting_path(result_event.get("file_path") or ""):
                        verified = False
                elif tool_name == "apply_style" and result_event.get("ok") is True:
                    verified = False
                elif tool_name == "python_sandbox":
                    if result_event.get("success") is True:
                        verified = False
                elif tool_name == "compile_tikz":
                    if result_event.get("success") is True:
                        verified = True

                # 连续相同失败检测：阻止 Agent 盲目重试同一个失败的工具调用
                failed = not guard_blocked and (
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

                # 工具族失败预算：失败原因文字不同但同族连续失败同样需要止损
                if failed and tool_name in fail_budget:
                    fail_budget[tool_name] += 1
                    if fail_budget[tool_name] >= fail_budget_limit:
                        fail_budget[tool_name] = 0
                        if tool_name == "python_sandbox":
                            budget_msg = (
                                "STOP: `python_sandbox` has failed "
                                f"{fail_budget_limit} times in a row. Do not keep "
                                "tweaking the same script. Simplify the figure "
                                "(remove error bars, annotations, 3D effects, or any "
                                "advanced feature), double-check every column name "
                                "against the data profile, then rewrite the script "
                                "with `write_file` and rerun. If the data cannot be "
                                "plotted, call `finish` with an explicit warning."
                            )
                        else:
                            budget_msg = (
                                "STOP: `compile_tikz` has failed "
                                f"{fail_budget_limit} times in a row. Do not keep "
                                "patching the generated TikZ output. Write "
                                "`artifacts/tikz_spec.json` + data CSVs and let the "
                                "backend render figure.tex (see TIKZ PROTOCOL), or "
                                "rewrite the script to emit a simple hand-written "
                                "PGFPlots file (labels match the document "
                                "language; non-ASCII labels need xelatex + a "
                                "CJK-capable font, otherwise use ASCII — data in artifacts/*.csv, "
                                "small/relative x values, explicit y error columns if "
                                "needed), rerun `python_sandbox`, then `compile_tikz`. "
                                "If TikZ remains impossible, call `finish` with an "
                                "explicit warning."
                            )
                        messages.append({"role": "system", "content": budget_msg})
                        yield {"type": "warning", "message": budget_msg}
                elif not failed and tool_name in fail_budget:
                    fail_budget[tool_name] = 0

                if guard_blocked:
                    yield {"type": "tool_result", **result_event}
                elif tool_name == "python_sandbox":
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

                if tool_name == "finish" and not blocked_stale_finish:
                    stale_delivery = (
                        fcfg.tikz_compile_enabled
                        and tikz_state["success"] is True
                        and not verified
                    )
                    if fcfg.tikz_compile_enabled and (
                        not tikz_state["called"] or tikz_state["success"] is not True
                    ):
                        warning_msg = (
                            "Agent called finish without a successful TikZ "
                            "compilation. The TikZ artifact may be missing or invalid."
                        )
                        await self._append_trace(
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
                    elif stale_delivery:
                        stale_warning = (
                            "WARNING: `finish` accepted although "
                            "figure-affecting files changed since the last "
                            "successful compile (override after a block); the "
                            "artifacts may be stale."
                        )
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "warning",
                                "data": {"message": stale_warning},
                            },
                        )
                        yield {"type": "warning", "message": stale_warning}
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
                await self._append_trace(
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

        await self._append_trace(
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
            *build_agent_file_tools_schema(
                write_description=(
                    "Create or overwrite a file inside the plot sandbox. Use "
                    "forward slashes, e.g. scripts/plot.py."
                ),
                read_description="Read a file inside the plot sandbox.",
                list_description=("List all files currently in the plot sandbox."),
            ),
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

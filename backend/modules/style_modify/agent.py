"""style_modify 自主 Agent 主循环与私有工具执行器。

Agent 工具只绑定当前 session 和项目根目录，不注册到全局 ToolRegistry。
真实项目只读；`stage_replace` 只写会话 overlay，`compile_staged_project` 在
shadow 项目中编译。`finish` 生成不可变 proposal revision，由用户确认后由
service 层通过 LatexProject 批量事务写回。
"""

import json
import shutil
import uuid
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt
from backend.modules.style_modify.models import CompileSummary
from backend.modules.style_modify.session import get_session_store
from backend.modules.style_modify.trace import append_trace, now_iso
from backend.utils.agent_file_tools import (
    AgentFileRead,
    AgentFileToolBehavior,
    AgentFileToolExecutor,
    AgentFileToolFailure,
    AgentFileToolNames,
    AgentFileWrite,
    build_agent_file_tools_schema,
    normalize_line_range,
)

STYLE_FILE_TOOL_NAMES = AgentFileToolNames(
    write="stage_replace",
    read="read_project_file",
    list="list_project_files",
)


class StyleModifyAgent:
    """基于 Function Calling 的样式修改 Agent。"""

    def __init__(self) -> None:
        self._logger = get_logger("style_modify.agent")
        self._store = get_session_store()

    async def _append_trace(self, session_id: str, event: dict[str, Any]) -> None:
        try:
            session_dir = self._store.session_dir(session_id)
            await append_trace(session_dir, event)
        except Exception as e:
            self._logger.warning("Failed to append style_modify trace: %s", e)

    async def run(
        self,
        session_id: str,
        workspace_id: str,
        requirement: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        meta = self._store.load_meta(session_id)
        self._store.require_workspace(meta, workspace_id)
        meta.status = "running"
        meta.requirement = requirement
        meta.language = language
        self._store.save_meta(session_id, meta)

        cfg = get_config()
        if llm is None:
            llm = get_llm_client()
        compile_enabled = "available" if _tex_toolchain_available() else "unavailable"
        system_prompt = get_prompt(
            "style_modify",
            "agent_system",
            language=language,
            max_rounds=cfg.features.style_modify.agent.max_rounds,
            compile_enabled=compile_enabled,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Modify the currently open LaTeX project according to:\n\n"
                    f"{requirement}\n\n"
                    f"Respond in {language} for any explanatory text."
                ),
            },
        ]
        async for event in self._finalize_session(
            session_id,
            self._run_agent(
                session_id,
                messages,
                run_type="generate",
                llm=llm,
            ),
        ):
            yield event

    async def refine(
        self,
        session_id: str,
        workspace_id: str,
        message: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        meta = self._store.load_meta(session_id)
        self._store.require_workspace(meta, workspace_id)
        meta.refinements.append(message)
        meta.status = "running"
        self._store.save_meta(session_id, meta)

        if llm is None:
            llm = get_llm_client()
        compile_enabled = "available" if _tex_toolchain_available() else "unavailable"
        refine_prompt = get_prompt(
            "style_modify",
            "refine_system",
            language=language,
            message=message,
            compile_enabled=compile_enabled,
        )
        messages = list(meta.messages)
        if not messages:
            cfg = get_config()
            system_prompt = get_prompt(
                "style_modify",
                "agent_system",
                language=language,
                max_rounds=cfg.features.style_modify.agent.max_rounds,
                compile_enabled=compile_enabled,
            )
            messages = [{"role": "system", "content": system_prompt}]
        messages.append(
            {
                "role": "user",
                "content": f"{refine_prompt}\n\nUser refinement: {message}",
            }
        )
        async for event in self._finalize_session(
            session_id,
            self._run_agent(
                session_id,
                messages,
                run_type="refine",
                llm=llm,
            ),
        ):
            yield event

    async def _finalize_session(
        self,
        session_id: str,
        agen: AsyncIterator[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """确保 Agent 生成器无论正常结束、LLM/工具异常、达到上限还是客户端
        断连，都会将会话从 running 恢复到可管理终态。否则会话既不能删除，
        也会被过期清理跳过，形成永久泄漏。"""
        try:
            async for event in agen:
                yield event
        finally:
            try:
                meta = self._store.load_meta(session_id)
                if meta.status == "running":
                    meta.status = "ready"
                    self._store.save_meta(session_id, meta)
            except Exception as e:
                self._logger.warning(
                    "Failed to finalize style_modify session %s: %s",
                    session_id,
                    e,
                )

    async def _run_agent(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        run_type: str,
        llm: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = get_config()
        fcfg = cfg.features.style_modify.agent
        executor = _ToolExecutor(session_id, self._logger)
        tools = self._build_tools_schema()
        tex_available = _tex_toolchain_available()
        max_rounds = fcfg.max_rounds
        max_tool_calls = fcfg.max_tool_calls
        max_compile_runs = fcfg.max_compile_runs
        compile_state: dict[str, Any] = {
            "called": False,
            "success": None,
            "status": None,
            "summary": None,
        }
        final_summary = ""
        run_id = uuid.uuid4().hex[:12]
        rounds_used = 0
        tool_call_count = 0
        compile_run_count = 0
        no_tool_strikes = 0

        await self._append_trace(
            session_id,
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
                    "content": f"Starting style modification agent (up to {max_rounds} rounds)...",
                }

            try:
                response = await llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=cfg.llm.temperature,
                    max_tokens=cfg.llm.max_tokens,
                )
            except Exception as e:
                self._logger.error("style_modify chat_with_tools failed: %s", e)
                await self._append_trace(
                    session_id,
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
                session_id,
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
                if no_tool_strikes < 2 and round_idx < max_rounds - 1:
                    no_tool_strikes += 1
                    nudge = (
                        "You replied without calling a tool, which counts as a "
                        "stalled round. Call the next tool now: inspect the "
                        "project, stage candidate changes, or finish. Keep plan "
                        "text to at most two sentences."
                    )
                    messages.append(msg.model_dump())
                    messages.append({"role": "user", "content": nudge})
                    await self._append_trace(
                        session_id,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "nudge",
                            "data": {"strike": no_tool_strikes},
                        },
                    )
                    continue
                messages.append(msg.model_dump())
                break

            messages.append(msg.model_dump())
            finish_this_round = False

            for tc in tool_calls:
                tool_name = tc.function.name
                tool_call_count += 1
                if tool_call_count > max_tool_calls:
                    yield {
                        "type": "error",
                        "message": f"Tool call limit {max_tool_calls} exceeded",
                    }
                    return

                try:
                    tool_args = json.loads(tc.function.arguments)
                except Exception:
                    tool_args = {}

                yield {
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": _summarize_args(tool_name, tool_args),
                }

                # 编译上限必须在执行前检查：超过上限时拒绝本次编译并回传
                # 明确的工具结果，而不是先跑一次再警告（既不省资源，也让
                # LLM 收不到该次调用的结果）。
                if tool_name == "compile_staged_project":
                    if compile_run_count >= max_compile_runs:
                        warning_msg = (
                            f"Shadow compile limit {max_compile_runs} reached; "
                            "further compilation attempts are refused."
                        )
                        yield {"type": "warning", "message": warning_msg}
                        refusal_text = (
                            f"Refused: shadow compile limit {max_compile_runs} "
                            "reached; no further compilation is allowed in this "
                            "run. Review the staged diff and call finish."
                        )
                        await self._append_trace(
                            session_id,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "tool_result",
                                "data": {
                                    "tool": tool_name,
                                    "args": tool_args,
                                    "ok": False,
                                    "result_text": refusal_text,
                                },
                            },
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": refusal_text,
                            }
                        )
                        continue
                    compile_run_count += 1

                try:
                    result_text, result_event = await executor.execute(
                        tool_name,
                        tool_args,
                    )
                except Exception as e:
                    self._logger.warning("style_modify tool exception: %s", e)
                    result_text = f"Tool execution failed: {e}"
                    result_event = {
                        "tool": tool_name,
                        "ok": False,
                        "summary": result_text,
                    }

                await self._append_trace(
                    session_id,
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

                if tool_name == "compile_staged_project":
                    compile_state["called"] = True
                    compile_state["success"] = result_event.get("success")
                    compile_state["status"] = result_event.get("status")
                    compile_state["summary"] = result_event.get("compile")
                    yield {"type": "compile", **result_event}
                elif tool_name == "stage_replace":
                    yield {"type": "file_staged", **result_event}
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
                    final_summary = tool_args.get("summary", final_summary)
                    compile_summary = self._current_compile_summary(compile_state)
                    if (
                        self._store.list_staged_files(session_id)
                        and not compile_state["called"]
                        and tex_available
                    ):
                        warning_msg = (
                            "Agent finished without running a shadow compilation; "
                            "the proposal may not be verified."
                        )
                        yield {"type": "warning", "message": warning_msg}
                    if (
                        compile_state["called"]
                        and compile_state["success"] is False
                        and compile_state["status"] == "failed"
                    ):
                        warning_msg = (
                            "Agent finished while the last shadow compilation "
                            "failed; review the diff before applying."
                        )
                        yield {"type": "warning", "message": warning_msg}
                    proposal = self._store.make_proposal(
                        session_id,
                        final_summary,
                        compile_summary,
                    )
                    yield {"type": "proposal", "proposal": proposal.model_dump()}
                    finish_this_round = True
                    break

            meta = self._store.load_meta(session_id)
            meta.messages = messages
            self._store.save_meta(session_id, meta)
            if finish_this_round:
                break
        else:
            final_summary = final_summary or (
                f"Agent reached the maximum number of rounds {max_rounds}, "
                "automatically ending."
            )
            yield {
                "type": "plan",
                "content": f"Reached maximum rounds {max_rounds}, stopping.",
            }
            if self._store.list_staged_files(session_id):
                compile_summary = self._current_compile_summary(compile_state)
                proposal = self._store.make_proposal(
                    session_id,
                    final_summary,
                    compile_summary,
                )
                yield {"type": "proposal", "proposal": proposal.model_dump()}

        meta = self._store.load_meta(session_id)
        meta.messages = messages
        meta.status = "ready"
        self._store.save_meta(session_id, meta)

        await self._append_trace(
            session_id,
            {
                "ts": now_iso(),
                "run_id": run_id,
                "type": "run_end",
                "data": {
                    "summary": final_summary,
                    "rounds": rounds_used,
                    "tool_calls": tool_call_count,
                    "compile_success": compile_state["success"],
                },
            },
        )
        yield {
            "type": "done",
            "session_id": session_id,
            "revision": meta.latest_revision,
            "summary": final_summary,
        }

    def _current_compile_summary(
        self,
        compile_state: dict[str, Any],
    ) -> CompileSummary:
        summary = compile_state.get("summary")
        if isinstance(summary, CompileSummary):
            return summary
        if isinstance(summary, dict):
            try:
                return CompileSummary.model_validate(summary)
            except Exception:
                pass
        if compile_state["called"]:
            return CompileSummary(
                status="failed" if compile_state["success"] is False else "passed",
                log="Shadow compilation result was recorded but the full summary "
                "is unavailable.",
            )
        return CompileSummary(
            status="not_run",
            log="Agent finished without running shadow compilation.",
        )

    @staticmethod
    def _build_tools_schema() -> list[dict[str, Any]]:
        file_tools = {
            schema["function"]["name"]: schema
            for schema in build_agent_file_tools_schema(
                names=STYLE_FILE_TOOL_NAMES,
                write_description=(
                    "Stage a COMPLETE replacement of an existing project file "
                    "into the session overlay. The real project is not written. "
                    "Only existing .tex/.sty/.cls/.bib files are allowed; do "
                    "not create, delete, or rename files."
                ),
                read_description=(
                    "Read a project file or a line range. Use this before "
                    "staging changes so you never guess file contents."
                ),
                list_description=(
                    "List LaTeX-related files in the current project. "
                    "Returns relative paths and sizes only."
                ),
                list_properties={
                    "max_results": {
                        "type": "integer",
                        "description": "Optional result limit.",
                    }
                },
                write_path_description="Relative path, e.g. main.tex.",
                write_content_description=(
                    "The complete candidate file content. LaTeX backslashes "
                    "are literal; in JSON tool arguments write every LaTeX "
                    "backslash as two backslash characters so parsing does "
                    "not corrupt them."
                ),
                supports_line_range=True,
            )
        }
        return [
            file_tools[STYLE_FILE_TOOL_NAMES.list],
            {
                "type": "function",
                "function": {
                    "name": "search_project_text",
                    "description": (
                        "Search allowed project files for a plain-text substring. "
                        "Useful for locating style definitions, color commands, "
                        "section formatting, or package options."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Text to search for.",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Optional result limit.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            file_tools[STYLE_FILE_TOOL_NAMES.read],
            file_tools[STYLE_FILE_TOOL_NAMES.write],
            {
                "type": "function",
                "function": {
                    "name": "discard_file_changes",
                    "description": "Discard the staged candidate for one file.",
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
                    "name": "compile_staged_project",
                    "description": (
                        "Build a shadow copy of the project, overlay staged "
                        "candidates, and run latexmk to verify compilation. The "
                        "real project is not modified. If the LaTeX toolchain is "
                        "unavailable, this returns status not_run; do not retry it."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "main_file": {
                                "type": "string",
                                "description": "Optional main .tex path.",
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": (
                        "Finish the current run and generate an immutable proposal "
                        "revision with diffs and the latest compile status. Provide "
                        "a concise summary of what changed, where, and any risks."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "Final summary in the user language.",
                            }
                        },
                        "required": ["summary"],
                    },
                },
            },
        ]


class _ToolExecutor(AgentFileToolExecutor):
    """执行绑定当前 session 的私有工具。"""

    _file_tool_names = STYLE_FILE_TOOL_NAMES
    _supports_line_range = True
    _file_tool_behavior = AgentFileToolBehavior(
        list_header="Project files:",
        empty_list_text="No LaTeX-related files found in the project.",
        empty_list_summary="0 files",
        include_files_in_list_event=True,
        include_write_size_in_event=False,
        preserve_read_line_endings=False,
    )

    def __init__(self, session_id: str, logger) -> None:
        self._session_id = session_id
        self._logger = logger
        self._store = get_session_store()

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if tool_name in self._file_tool_names.values:
            return await self.execute_file_tool(tool_name, args)
        if tool_name == "search_project_text":
            return self._search_project_text(args)
        if tool_name == "discard_file_changes":
            return self._discard_file_changes(args)
        if tool_name == "compile_staged_project":
            return await self._compile_staged_project(args)
        if tool_name == "finish":
            return self._finish(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    def _list_agent_files(
        self,
        args: dict[str, Any],
    ) -> list[dict[str, Any]] | AgentFileToolFailure:
        try:
            return self._store.list_project_files(
                self._session_id,
                max_results=int(args.get("max_results") or 500),
            )
        except (TypeError, ValueError) as e:
            return AgentFileToolFailure(str(e))

    def _search_project_text(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            matches = self._store.search_project_text(
                self._session_id,
                str(args.get("query", "")),
                max_results=int(args.get("max_results") or 40),
            )
        except (TypeError, ValueError) as e:
            return str(e), {
                "tool": "search_project_text",
                "ok": False,
                "summary": str(e),
            }
        if not matches:
            text = "No matches found."
        else:
            text = "Matches:\n" + "\n".join(
                f"{m['path']}:{m['line']}: {m['text']}" for m in matches
            )
        return text, {
            "tool": "search_project_text",
            "ok": True,
            "summary": f"{len(matches)} matches",
            "query": args.get("query", ""),
        }

    def _read_agent_file(
        self,
        file_path: str,
    ) -> AgentFileRead | AgentFileToolFailure:
        try:
            result = self._store.read_project_file(
                self._session_id,
                file_path,
            )
        except (TypeError, ValueError) as e:
            return AgentFileToolFailure(str(e))
        return AgentFileRead(path=result["path"], content=result["content"])

    def _max_read_chars(self) -> int:
        return get_config().features.style_modify.agent.max_tool_output_chars

    def _prepare_agent_line_range(
        self,
        content: str,
        value: Any,
    ) -> tuple[int, int] | None:
        line_range = normalize_line_range(
            value,
            coerce=True,
            allow_out_of_bounds=True,
        )
        if line_range is None:
            return None
        line_count = max(1, len(content.splitlines()))
        start, end = line_range
        start = max(1, start)
        end = min(end or line_count, line_count)
        if start > line_count:
            raise ValueError(f"line_range start out of range: {start}")
        if start > end:
            raise ValueError("line_range start must be less than or equal to end")
        return start, end

    def _format_agent_file_read(
        self,
        result: AgentFileRead,
        selected_text: str,
        line_range: tuple[int, int] | None,
    ) -> tuple[str, str, dict[str, Any]]:
        display_range = line_range or (
            1,
            max(1, len(result.content.splitlines())),
        )
        start, end = display_range
        return (
            f"--- {result.path} lines {start}-{end} ---\n{selected_text}",
            f"{result.path}:{start}-{end}",
            {
                "file_path": result.path,
                "line_range": list(display_range),
            },
        )

    def _write_agent_file(
        self,
        file_path: str,
        content: str,
    ) -> AgentFileWrite | AgentFileToolFailure:
        try:
            staged = self._store.stage_replace(
                self._session_id,
                file_path,
                content,
            )
        except ValueError as e:
            return AgentFileToolFailure(str(e))
        text = (
            f"Staged {file_path} ({staged.baseline_size} -> "
            f"{staged.candidate_size} bytes).\nDiff:\n{staged.diff}"
        )
        return AgentFileWrite(
            path=file_path,
            size=staged.candidate_size,
            text=text,
            summary=f"Staged {file_path}",
            event={
                "baseline_md5": staged.baseline_md5,
                "candidate_md5": staged.candidate_md5,
            },
        )

    def _discard_file_changes(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = str(args.get("file_path", ""))
        try:
            text = self._store.discard_file_changes(self._session_id, file_path)
        except ValueError as e:
            return str(e), {
                "tool": "discard_file_changes",
                "ok": False,
                "summary": str(e),
            }
        return text, {
            "tool": "discard_file_changes",
            "ok": True,
            "summary": text,
            "file_path": file_path,
        }

    async def _compile_staged_project(
        self, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        main_file = args.get("main_file") or None
        if not _tex_toolchain_available():
            text = (
                "LaTeX toolchain unavailable: latexmk was not found on PATH. "
                "Do not call compile_staged_project again in this run. Review "
                "the staged diff and call finish; state in the summary that "
                "compilation was not verified because the toolchain is missing."
            )
            summary = CompileSummary(
                status="not_run",
                log=text,
            )
            return text, {
                "tool": "compile_staged_project",
                "ok": True,
                "success": False,
                "status": "not_run",
                "summary": "Shadow compilation unavailable",
                "compile": summary.model_dump(),
                **summary.model_dump(),
            }
        try:
            summary = await self._store.compile_staged_project(
                self._session_id,
                main_file=main_file,
            )
        except (ValueError, OSError) as e:
            return str(e), {
                "tool": "compile_staged_project",
                "ok": False,
                "success": False,
                "summary": str(e),
            }
        text = (
            f"Shadow compilation {summary.status}."
            + (f" Main file: {summary.main_file}." if summary.main_file else "")
            + "\n"
            + summary.log
        )
        success = summary.status == "passed"
        return text, {
            "tool": "compile_staged_project",
            "ok": True,
            "success": success,
            "summary": f"Shadow compilation {summary.status}",
            "compile": summary.model_dump(),
            **summary.model_dump(),
        }

    def _finish(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = str(args.get("summary", ""))
        return f"Proposal finish requested. Summary: {summary}", {
            "tool": "finish",
            "ok": True,
            "summary": "Finished",
        }


def _summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "stage_replace":
        content = str(args.get("content", ""))
        return {
            "file_path": args.get("file_path", ""),
            "content_length": len(content),
            "preview": content[:300],
        }
    if tool_name in ("read_project_file", "discard_file_changes"):
        return {k: v for k, v in args.items() if k in ("file_path", "line_range")}
    if tool_name == "compile_staged_project":
        return {"main_file": args.get("main_file", "")}
    if tool_name == "finish":
        return {"summary": args.get("summary", "")}
    return args


_agent: StyleModifyAgent | None = None


def _tex_toolchain_available() -> bool:
    """判断当前环境是否具备 shadow 编译所需工具链。"""
    if shutil.which("latexmk") is None:
        return False
    return any(
        shutil.which(engine) is not None
        for engine in ("pdflatex", "xelatex", "lualatex")
    )


def get_style_modify_agent() -> StyleModifyAgent:
    global _agent
    if _agent is None:
        _agent = StyleModifyAgent()
    return _agent

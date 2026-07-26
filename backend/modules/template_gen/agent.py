"""
模板生成自主 Agent。

设计目标：
    将"单次 LLM 出一个 .tex 骨架"升级为基于 Function Calling 的自主 Agent：
    Agent 自主规划文件结构 → 逐个 write_file → 调用 compile_latex 验证 →
    读取编译日志 → 修正错误 → 再编译，最多 N 轮，最终产出一个完整可编译的多文件
    LaTeX 项目。

工具体系（session 级私有）：
    工具 schema 由本服务动态构建并随 chat_with_tools 注入，执行函数在沙箱目录内
    操作。不注册到全局 ToolRegistry——因为这些工具绑定到一次性沙箱目录，注册成
    全局能力会污染其他模块（如 writing_chat）的工具集。

沙箱与路径安全：
    每次生成在 `output_dir/agent/<session_id>/` 下创建独立目录，所有 file_path
    参数经 _resolve_sandbox_path 校验：禁止绝对路径、禁止 '..'、解析后必须仍在
    沙箱内、扩展名白名单限制为 LaTeX 相关文件。

事件流（SSE）：
    {"type":"plan","content":"..."}               Agent 的思考/说明文本
    {"type":"tool_call","tool":"...","args":{...}} 工具调用开始
    {"type":"tool_result","tool":"...","ok":bool,"summary":"..."} 工具执行结果
    {"type":"compile","success":bool,"log":"...","errors":[...]} 编译结果（含结构化错误列表）
    {"type":"warning","message":"..."}            Agent 行为可疑（如编译未过却 finish）
    {"type":"done","session_id":"...","files":[...],"summary":"..."} 完成
    {"type":"error","message":"..."}               错误终止

编译错误修复回路：
    compile_latex 失败时，日志经 _extract_errors() 解析为结构化错误列表
    （type/file/line/detail），再由 _format_error_analysis() 拼成聚焦错误
    摘要回传 LLM。prompt 强制 LLM 走 ANALYZE→READ→FIX→RECOMPILE 循环，
    最新一次编译失败时不允许 finish。循环跟踪 last_compile_success 状态，
    若 Agent 在编译未通过时仍调用 finish，则推送 warning 事件提示前端。
"""

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt

# 允许在沙箱内创建的文件扩展名白名单
_ALLOWED_SUFFIXES = (
    ".tex",
    ".sty",
    ".cls",
    ".bib",
    ".md",
    ".txt",
    ".latex",
    ".ltx",
    ".cfg",
    ".bst",
    ".def",
)

# 编译日志回传给 LLM 时的截断长度（字符）
_LOG_TRUNCATE = 4000


class TemplateAgentService:
    """模板生成自主 Agent 服务（单例）。"""

    def __init__(self) -> None:
        self._logger = get_logger("template_gen.agent")

    # ── 公共入口 ─────────────────────────────────────────────────────────

    async def generate(
        self,
        description: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        自主生成 LaTeX 模板项目，SSE 事件流。

        参数：
            description: 用户对模板的自然语言描述
            language: 输出语言（注入 system prompt）
            llm: 可选 LLM 客户端（测试注入用）
        """
        cfg = get_config()
        if not cfg.llm.api_key:
            yield {"type": "error", "message": "No API key configured"}
            return

        if not cfg.features.template_gen.agent_enabled:
            yield {"type": "error", "message": "Agent mode disabled by config"}
            return

        if llm is None:
            llm = get_llm_client()

        # 1. 创建沙箱工作区
        output_dir = Path(cfg.features.template_gen.output_dir)
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        session_id = uuid.uuid4().hex[:12]
        sandbox = output_dir / "agent" / session_id
        sandbox.mkdir(parents=True, exist_ok=True)
        self._logger.info(f"Agent sandbox has been created: {sandbox}")

        # 2. 构造工具 schema 与执行器
        tools = self._build_tools_schema(cfg)
        executor = _ToolExecutor(sandbox, cfg, self._logger)

        # 3. 构造 messages
        system_prompt = get_prompt(
            "template_gen",
            "agent_system",
            language=language,
            max_rounds=cfg.features.template_gen.agent_max_rounds,
            max_files=cfg.features.template_gen.agent_max_files,
            compile_enabled=(
                "available"
                if cfg.features.template_gen.agent_compile_enabled
                else "unavailable"
            ),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Generate a complete, compilable LaTeX project for the "
                    f"following requirement:\n\n{description}\n\n"
                    f"Respond in {language} for any explanatory text."
                ),
            },
        ]

        # 4. Agent 循环
        max_rounds = cfg.features.template_gen.agent_max_rounds
        final_summary = ""
        # 跟踪编译状态：不允许 Agent 在最后一次编译仍失败时静默 finish
        compile_state = {"called": False, "success": None}
        for round_idx in range(max_rounds):
            if round_idx == 0:
                yield {
                    "type": "plan",
                    "content": f"Start generating (up to {max_rounds} rounds)...",
                }

            try:
                response = await llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=cfg.llm.temperature,
                    max_tokens=cfg.llm.max_tokens,
                )
            except Exception as e:
                self._logger.error(
                    f"chat_with_tools failed (round {round_idx + 1}): {e}"
                )
                yield {"type": "error", "message": str(e)}
                return

            choice = response.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls

            # 推送 LLM 的思考文本（若有）
            if msg.content:
                yield {"type": "plan", "content": msg.content}
                final_summary = msg.content

            if not tool_calls:
                # Agent 主动结束（无工具调用即终止）
                break

            # 将 assistant 含 tool_calls 的消息追加进历史
            messages.append(msg.model_dump())

            # 本轮是否触发了 finish（finish 后立即跳出循环）
            finish_this_round = False
            # 依次执行每个 tool_call
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
                        tool_name,
                        tool_args,
                    )
                except Exception as e:
                    self._logger.warning(f"Tool execution exception {tool_name}: {e}")
                    result_text = f"Tool execution failed: {e}"
                    result_event = {
                        "tool": tool_name,
                        "ok": False,
                        "summary": result_text,
                    }

                # 编译单独事件，便于前端高亮展示
                if tool_name == "compile_latex":
                    compile_state["called"] = True
                    compile_state["success"] = result_event.get("success")
                    yield {"type": "compile", **result_event}
                else:
                    yield {"type": "tool_result", **result_event}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )

                # finish 工具立即终止循环
                if tool_name == "finish":
                    # 校验：若曾被调用过编译且最后一次仍失败，
                    # 不放任 Agent 逃避，推送 warning 提示前端（仍允许结束）
                    if compile_state["called"] and compile_state["success"] is False:
                        yield {
                            "type": "warning",
                            "message": (
                                "Agent called finish, but the last compilation still failed. "
                                "The project may not build successfully. Please check the sandbox files manually."
                            ),
                        }
                    final_summary = tool_args.get("summary", final_summary)
                    finish_this_round = True
                    break

            # finish 已经触发，跳出整体循环
            if finish_this_round:
                self._logger.info(
                    f"Agent finished proactively, ending at round {round_idx + 1}"
                )
                break

        else:
            # for...else：未 break（达到 max_rounds）
            final_summary = (
                final_summary
                or f"Agent reached the maximum number of rounds {max_rounds}, automatically ending."
            )
            yield {
                "type": "plan",
                "content": f"Reached maximum rounds {max_rounds}, stopping generation.",
            }
            # 达到极限时，若仍编译失败，也推送一次 warning
            if compile_state["called"] and compile_state["success"] is False:
                yield {
                    "type": "warning",
                    "message": (
                        f"Reached the maximum number of rounds ({max_rounds}), but the last compilation still did not pass. "
                        "The project may not build successfully. Please check the sandbox files manually."
                    ),
                }

        # 5. 汇总文件
        files = executor.list_files()
        yield {
            "type": "done",
            "session_id": session_id,
            "sandbox": str(sandbox),
            "files": files,
            "summary": final_summary,
        }

    # ── 沙箱路径校验 ─────────────────────────────────────────────────────

    @staticmethod
    def _resolve_sandbox_path(sandbox: Path, file_path: str) -> Path:
        """校验并解析沙箱内相对路径，拒绝绝对路径 / '..' / 越界 / 非白名单扩展名。"""
        if not file_path or not file_path.strip():
            raise ValueError("file_path 不能为空")
        if "\x00" in file_path:
            raise ValueError("file_path 包含非法字符")
        # 统一用正斜杠处理，再判绝对路径
        fp = file_path.replace("\\", "/")
        if fp.startswith("/"):
            raise ValueError("file_path 不能是绝对路径")
        if ".." in Path(fp).parts:
            raise ValueError("file_path 不能包含 '..' 路径段")

        resolved = (sandbox / fp).resolve()
        if not resolved.is_relative_to(sandbox.resolve()):
            raise ValueError("file_path 解析后超出沙箱范围")

        if resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ValueError(
                f"file_path 扩展名不允许：{resolved.suffix}（仅允许 {_ALLOWED_SUFFIXES}）"
            )
        return resolved

    # ── 工具 schema ─────────────────────────────────────────────────────

    def _build_tools_schema(self, cfg: Any) -> list[dict[str, Any]]:
        """构建 agent 专属工具 schema（session 级私有，不进全局 ToolRegistry）。"""
        tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": (
                        "Create or overwrite a file in the project workspace "
                        "with the given content. Use forward slashes for paths "
                        "(e.g. 'chapters/intro.tex'). Create all needed parent "
                        "directories implicitly. Only LaTeX-related extensions "
                        "are allowed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative path within the project, e.g. 'main.tex'.",
                            },
                            "content": {
                                "type": "string",
                                "description": "The full file content to write.",
                            },
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the current contents of a file already created in the workspace. Useful before editing to fix compile errors.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List all files currently in the workspace with their relative paths and sizes. Call this after writing files to verify the project structure.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

        if cfg.features.template_gen.agent_compile_enabled:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "compile_latex",
                        "description": (
                            "Compile the project with latexmk -xelatex to verify it "
                            "builds successfully. Returns success status and a trimmed "
                            "log on failure. Call this after writing all files, and "
                            "again after any fix. Do not loop more than necessary."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "main_file": {
                                    "type": "string",
                                    "description": "Relative path to the main .tex file, e.g. 'main.tex'.",
                                },
                            },
                            "required": ["main_file"],
                        },
                    },
                }
            )

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": (
                        "Signal that the project is complete and ready. Call this "
                        "exactly once at the end, after successful compilation (or "
                        "when compilation is unavailable). Provide a short summary."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "A brief summary of the generated project.",
                            },
                            "main_file": {
                                "type": "string",
                                "description": "The main .tex file to compile.",
                            },
                        },
                        "required": ["summary"],
                    },
                },
            }
        )
        return tools


# ── 工具执行器（持有沙箱，无状态可重建） ─────────────────────────────────


class _ToolExecutor:
    """沙箱内工具执行器，封装路径校验、文件 I/O 与编译。"""

    def __init__(
        self,
        sandbox: Path,
        cfg: Any,
        logger: Any,
    ) -> None:
        self._sandbox = sandbox
        self._cfg = cfg
        self._logger = logger
        self._file_count = 0

    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """执行单个工具，返回 (回传 LLM 的文本, 前端事件 dict)。"""
        if tool_name == "write_file":
            return await self._write_file(args)
        if tool_name == "read_file":
            return self._read_file(args)
        if tool_name == "list_files":
            return self._list_files(args)
        if tool_name == "compile_latex":
            return await self._compile_latex(args)
        if tool_name == "finish":
            return self._finish(args)
        raise ValueError(f"未知工具：{tool_name}")

    # ── write_file ────────────────────────────────────────────────────

    async def _write_file(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        fcfg = self._cfg.features.template_gen
        if self._file_count >= fcfg.agent_max_files:
            msg = f"Maximum file count {fcfg.agent_max_files} reached; refusing to write {file_path}"
            return msg, {"tool": "write_file", "ok": False, "summary": msg}

        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, file_path
            )
        except ValueError as e:
            return str(e), {
                "tool": "write_file",
                "ok": False,
                "summary": f"Path validation failed: {e}",
            }

        max_bytes = fcfg.agent_max_file_size_kb * 1024
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            msg = (
                f"File {file_path} exceeds the maximum size "
                f"of {fcfg.agent_max_file_size_kb} KB"
            )
            return msg, {"tool": "write_file", "ok": False, "summary": msg}

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content_bytes)
        self._file_count += 1
        self._logger.info(f"write_file: {file_path} ({len(content_bytes)} bytes)")

        summary = f"Wrote {file_path} ({len(content_bytes)} bytes)"
        return summary, {
            "tool": "write_file",
            "ok": True,
            "summary": summary,
            "file_path": file_path,
            "size": len(content_bytes),
        }

    # ── read_file ─────────────────────────────────────────────────────

    def _read_file(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, file_path
            )
        except ValueError as e:
            return str(e), {
                "tool": "read_file",
                "ok": False,
                "summary": f"Path validation failed: {e}",
            }
        if not resolved.exists() or not resolved.is_file():
            msg = f"File not found: {file_path}"
            return msg, {"tool": "read_file", "ok": False, "summary": msg}
        content = resolved.read_text(encoding="utf-8")
        # 截断超长内容回传 LLM
        truncated = (
            content if len(content) <= 8000 else (content[:8000] + "\n...[truncated]")
        )
        event = {"tool": "read_file", "ok": True, "summary": file_path}
        return truncated, event

    # ── list_files ────────────────────────────────────────────────────

    def _list_files(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        files = self.list_files()
        if not files:
            return "(workspace is empty)", {
                "tool": "list_files",
                "ok": True,
                "summary": "Empty",
            }
        text = "Files in workspace:\n" + "\n".join(
            f"- {f['path']} ({f['size']} bytes)" for f in files
        )
        return text, {
            "tool": "list_files",
            "ok": True,
            "summary": f"{len(files)} files",
        }

    # ── compile_latex ─────────────────────────────────────────────────

    async def _compile_latex(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        main_file = args.get("main_file", "")
        fcfg = self._cfg.features.template_gen
        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, main_file
            )
        except ValueError as e:
            return str(e), {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": str(e),
                "summary": f"Path validation failed: {e}",
            }
        if not resolved.exists():
            msg = f"Main file not found: {main_file}"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        # latexmk 不能以 '-' 开头的路径作为参数
        if main_file.lstrip().startswith("-"):
            msg = "main_file cannot start with '-'"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        timeout = fcfg.agent_compile_timeout
        self._logger.info(f"agent compile | main={main_file} | cwd={self._sandbox}")

        try:
            process = await asyncio.create_subprocess_exec(
                "latexmk",
                "-xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                main_file,
                cwd=str(self._sandbox),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except FileNotFoundError:
            msg = "latexmk is not installed or is not on PATH"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            msg = f"Compilation timed out ({timeout}s)"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        full_log = stdout + "\n" + stderr
        success = process.returncode == 0

        trimmed = _trim_log(full_log, success)
        self._logger.info(f"agent compile done | success={success}")

        if success:
            # 给 LLM 回传的内容就足以表明成功，无需额外结构化
            llm_text = trimmed
            errors: list[dict[str, Any]] = []
            summary = "Compilation succeeded"
        else:
            # 失败时提取结构化错误列表，并构造聚焦错误摘要回传 LLM，
            # 帮助 LLM 走 ANALYZE→READ→FIX→RECOMPILE 循环
            errors = _extract_errors(full_log)
            analysis = _format_error_analysis(errors)
            llm_text = analysis + "\n\n--- Raw log (trimmed) ---\n" + trimmed
            error_count = len(errors)
            summary = (
                f"Compilation failed ({error_count} error(s)); analyze the error summary, "
                f"use read_file to inspect the relevant files, then use write_file to fix and compile again"
            )

        event = {
            "tool": "compile_latex",
            "ok": True,
            "success": success,
            "log": trimmed,
            "summary": summary,
        }
        if not success:
            event["errors"] = errors
        return llm_text, event

    # ── finish ────────────────────────────────────────────────────────

    def _finish(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = args.get("summary", "")
        main_file = args.get("main_file", "")
        files = self.list_files()
        text = (
            f"Project finished. Summary: {summary}\n"
            f"Main file: {main_file}\n"
            f"Files ({len(files)}): " + ", ".join(f["path"] for f in files)
        )
        return text, {
            "tool": "finish",
            "ok": True,
            "summary": "Finished",
            "main_file": main_file,
        }

    # ── 文件列表（公共） ──────────────────────────────────────────────

    def list_files(self) -> list[dict[str, Any]]:
        """列出沙箱内所有文件（相对路径 + 字节数），按路径排序。"""
        result: list[dict[str, Any]] = []
        root = self._sandbox.resolve()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            result.append({"path": rel, "size": p.stat().st_size})
        return result


# ── 辅助函数 ───────────────────────────────────────────────────────────


def _trim_log(log: str, success: bool) -> str:
    """截断编译日志，失败时优先保留错误行附近上下文。

    仅用于前端展示和 LLM 回传的原始日志片段；结构化错误信息由
    _extract_errors / _format_error_analysis 额外提供。
    """
    if success:
        # 成功时只取尾部简短信息
        tail = log[-1500:] if len(log) > 1500 else log
        return "Compilation succeeded.\n\n" + tail
    lines = log.splitlines()
    # 找到第一个明显错误标记
    err_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("!") or "Fatal error" in line or "Error:" in line:
            err_idx = i
            break
    if err_idx >= 0:
        start = max(0, err_idx - 10)
        end = min(len(lines), err_idx + 40)
        snippet = "\n".join(lines[start:end])
    else:
        snippet = log[-_LOG_TRUNCATE:] if len(log) > _LOG_TRUNCATE else log
    if len(snippet) > _LOG_TRUNCATE:
        snippet = snippet[:_LOG_TRUNCATE] + "\n...[log truncated]"
    return "Compilation FAILED.\n\n" + snippet


# 匹配 latexmk/xelatex 日志中的文件入栈标记，形如 `(./main.tex` 或
# `(chapters/intro.tex`。用于追踪错误发生在哪个源文件中。
_FILE_CTX_RE = re.compile(r"\(\s*((?:\.{1,2}/)?[\w./\-]+\.tex)\b")
# 匹配行号提示，形如 `l.123 ...`（不带转义因为 LaTeX 错误日志是纯文本）
_LINE_INFO_RE = re.compile(r"l\.(\d+)\s?(.*)")


def _extract_errors(log: str) -> list[dict[str, Any]]:
    """从 latexmk/xelatex 日志中提取结构化错误信息。

    识别以 `!` 开头的错误声明行，并：
      - 回溯最近的 `(xxx.tex` 上下文记录错误所在文件
      - 向后扫 15 行寻找 `l.NNN ...` 行号提示

    LaTeX 错误会级联（第一个错误往往引来一系列后续错误），所以只
    返回前若干个错误就足以让 LLM 聚焦根因。
    """
    errors: list[dict[str, Any]] = []
    lines = log.splitlines()
    last_file: str | None = None

    for i, line in enumerate(lines):
        # 更新当前文件上下文（粗略追踪，不处理栈深度，仅用最近出现的）
        for m in _FILE_CTX_RE.finditer(line):
            raw = m.group(1).lstrip("./")
            last_file = raw or last_file

        if not line.startswith("!"):
            continue

        err: dict[str, Any] = {
            "type": line[1:].strip(),
            "file": last_file,
            "line": None,
            "detail": "",
        }
        # 向后扫 15 行寻找 l.NNN
        for j in range(i + 1, min(len(lines), i + 15)):
            m_line = _LINE_INFO_RE.match(lines[j])
            if m_line:
                err["line"] = int(m_line.group(1))
                err["detail"] = m_line.group(2).strip()
                break
            if lines[j].startswith("!"):
                # 下一个错误已经开始，停止继续找行号
                break
        errors.append(err)

    # 限制最多 8 个错误，避免极端情况日志回传过长
    return errors[:8]


def _format_error_analysis(errors: list[dict[str, Any]]) -> str:
    """把结构化错误列表格式化为给 LLM 看的聚焦错误摘要。

    强调"修第一个错误"——LaTeX 错误级联是常态，后续错误通常是
    第一个的连锁反应。
    """
    if not errors:
        return (
            "Compilation FAILED. No '! ' error lines were detected in the "
            "log — inspect the raw log below for missing files, font issues, "
            "or other diagnostics, then fix and recompile."
        )
    parts = [
        f"Compilation FAILED. Detected {len(errors)} LaTeX error(s).",
        "Fix the FIRST error and recompile — later errors are usually "
        "consequences of the first.\n",
    ]
    for idx, e in enumerate(errors, 1):
        parts.append(f"Error {idx}:")
        parts.append(f"  type: {e['type']}")
        if e["file"]:
            parts.append(f"  file: {e['file']}")
        if e["line"] is not None:
            parts.append(f"  line: {e['line']}")
        if e["detail"]:
            parts.append(f"  detail: {e['detail']}")
        parts.append("")
    return "\n".join(parts)


def _summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """生成给前端展示的 args 摘要（write_file 的 content 截断）。"""
    if tool_name == "write_file":
        fp = args.get("file_path", "")
        content = args.get("content", "")
        return {
            "file_path": fp,
            "content_length": len(content),
            "preview": content[:300],
        }
    if tool_name == "compile_latex":
        return {"main_file": args.get("main_file", "")}
    if tool_name == "read_file":
        return {"file_path": args.get("file_path", "")}
    if tool_name == "finish":
        return {
            "summary": args.get("summary", ""),
            "main_file": args.get("main_file", ""),
        }
    return args


# ── 单例 ───────────────────────────────────────────────────────────────

_service: "TemplateAgentService | None" = None


def get_template_agent_service() -> TemplateAgentService:
    global _service
    if _service is None:
        _service = TemplateAgentService()
    return _service


def load_sandbox(output_dir: str, session_id: str) -> Path:
    """根据 session_id 解析沙箱目录（供 router 的文件读取端点使用）。

    安全约束：session_id 必须是纯十六进制/字母数字（生成时为 uuid hex），
    解析后必须严格位于 `output_dir/agent/` 子目录内，禁止 `..` 逃逸到
    其他模板或兄弟会话目录。
    """
    if not session_id or not session_id.strip():
        raise ValueError("session_id cannot be empty")
    if "\x00" in session_id:
        raise ValueError("session_id contains invalid characters")
    # 仅允许字母数字与下划线/短横线，杜绝 '..' / '/' / '\\' 注入
    if not all(c.isalnum() or c in "_-" for c in session_id):
        raise ValueError("session_id contains invalid characters")

    base = Path(output_dir)
    if not base.is_absolute():
        base = Path.cwd() / base
    agent_root = (base / "agent").resolve()
    sandbox = (agent_root / session_id).resolve()
    if not sandbox.is_relative_to(agent_root):
        raise ValueError("session_id resolves outside the sandbox root")
    return sandbox


def safe_read_sandbox_file(
    sandbox: Path, rel_path: str
) -> tuple[str | None, str | None]:
    """安全读取沙箱内文件，返回 (content, error)。"""
    try:
        resolved = TemplateAgentService._resolve_sandbox_path(sandbox, rel_path)
    except ValueError as e:
        return None, str(e)
    if not resolved.exists() or not resolved.is_file():
        return None, f"File not found: {rel_path}"
    return resolved.read_text(encoding="utf-8"), None

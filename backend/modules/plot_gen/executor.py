"""科研绘图沙箱执行器。"""

import ast
import asyncio
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.plot_gen.data_parser import ALLOWED_DATA_SUFFIXES
from backend.modules.plot_gen.models import (
    ArtifactInfo,
    ExecutionResult,
    StatsTestResult,
)
from backend.modules.plot_gen.sandbox_guard import render_guard_bootstrap
from backend.modules.plot_gen.styles import (
    list_styles,
    render_setup_code,
    validate_style_key,
)
from backend.modules.plot_gen.tikzgen import generate_figure_tex
from backend.utils.agent_file_tools import (
    AgentFileRead,
    AgentFileToolBehavior,
    AgentFileToolExecutor,
    AgentFileToolFailure,
    AgentFileWrite,
)
from backend.utils.path_guard import resolve_project_path
from backend.utils.sandbox import (
    ExecSpec,
    cached_backend_for,
    current_sandbox_user_id,
    get_sandbox_runner,
)
from backend.utils.tex_compile import (
    detect_engine_from_file,
    run_tex_compile,
    sanitize_output_paths,
)

WRITE_SUFFIXES = (".py", ".tex", ".txt", ".md", ".json", ".log", ".csv", ".tsv")
ARTIFACT_SUFFIXES = (
    ".pdf",
    ".svg",
    ".png",
    ".eps",
    ".tex",
    ".log",
    ".json",
    ".txt",
)
INTERNAL_DIRS = (".cache", "tmp")
PRECHECK_LARGE_COORD_THRESHOLD = 1e4


def _precheck_tikz(content: str) -> tuple[list[dict[str, str]], bool]:
    """编译前对 TikZ 源做轻量纯文本预检（不调用 LLM）。

    针对自动生成/手写 PGFPlots 的高频故障模式：
      - 内联 `y error=` / `+-` 误差棒语法（含 nan/inf 误差增量）;
      - 线性轴上绝对值 > 1e4 的坐标（Excel 日期序号触发 Dimension too large）;
      - 非 ASCII 字符（标签缺失字形，仅警告不拦截）。

    返回 (findings, blocked)：blocked=True 表示存在大概率必然编译失败的问题，
    调用方应跳过实际编译，直接把预检结果回传给 Agent 修正。
    """
    findings: list[dict[str, str]] = []
    blocked = False

    # 1) 内联误差棒语法：y error= 是无效 key（y error plus/minus 才是合法形式）
    if re.search(r"y\s*error\s*=(?!\s*(?:plus|minus))", content, re.IGNORECASE):
        findings.append(
            {
                "rule": "error_bar",
                "level": "error",
                "message": (
                    "Inline 'y error=' syntax detected. PGFPlots accepts "
                    "'y error plus'/'y error minus' only. Rewrite the plot by "
                    "hand in PGFPlots with explicit error columns, or drop "
                    "error bars."
                ),
            }
        )
        blocked = True

    for m in re.finditer(r"\+-\s*\(\s*([^)]*?)\s*,\s*([^)]*?)\s*\)", content):
        deltas = m.group(1) + "," + m.group(2)
        if re.search(r"nan|inf|none", deltas, re.IGNORECASE):
            # 自动导出器把缺失误差写成 (nan, nan)，PGFPlots 无法解析
            findings.append(
                {
                    "rule": "error_bar",
                    "level": "error",
                    "message": (
                        "Inline '+-' error bar deltas are not numeric "
                        f"(e.g. '{m.group(0).strip()[:40]}'). matplot2tikz "
                        "error bar output is unreliable. Rewrite the plot in "
                        "PGFPlots with explicit numeric error columns, or drop "
                        "error bars."
                    ),
                }
            )
            blocked = True
        else:
            findings.append(
                {
                    "rule": "error_bar",
                    "level": "warning",
                    "message": (
                        "Inline '+-' error bars detected. Prefer hand-written "
                        "PGFPlots with table-based 'y error plus'/'y error "
                        "minus' columns."
                    ),
                }
            )
        break

    # 2) 线性轴上绝对值 > 1e4 的坐标（日期序号等）。对数轴上的大数值合法，跳过。
    x_is_log = bool(re.search(r"xmode\s*=\s*log", content, re.IGNORECASE))
    y_is_log = bool(re.search(r"ymode\s*=\s*log", content, re.IGNORECASE))
    coord_pat = re.compile(
        r"\(\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*,\s*"
        r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)"
    )
    for m in coord_pat.finditer(content):
        xv = float(m.group(1))
        yv = float(m.group(2))
        if abs(xv) > PRECHECK_LARGE_COORD_THRESHOLD and not x_is_log:
            findings.append(
                {
                    "rule": "large_coordinate",
                    "level": "error",
                    "message": (
                        f"Coordinate x={xv:.6g} exceeds +/-"
                        f"{PRECHECK_LARGE_COORD_THRESHOLD:.0f} on a linear axis; "
                        "large values (e.g. Excel datetime serials) trigger "
                        "'Dimension too large' in PGFPlots. Convert datetime to "
                        "day offsets from the first date or use relative "
                        "coordinates."
                    ),
                }
            )
            blocked = True
            break
        if abs(yv) > PRECHECK_LARGE_COORD_THRESHOLD and not y_is_log:
            findings.append(
                {
                    "rule": "large_coordinate",
                    "level": "error",
                    "message": (
                        f"Coordinate y={yv:.6g} exceeds +/-"
                        f"{PRECHECK_LARGE_COORD_THRESHOLD:.0f} on a linear axis; "
                        "large values trigger 'Dimension too large' in "
                        "PGFPlots. Use relative/offset coordinates instead."
                    ),
                }
            )
            blocked = True
            break

    # 3) 非 ASCII 字符：缺失字形风险，警告但不拦截（xelatex 可正常渲染中文）
    non_ascii = sorted({ch for ch in content if ord(ch) > 0x7F})
    if non_ascii:
        sample = " ".join(repr(c) for c in non_ascii[:8])
        findings.append(
            {
                "rule": "non_ascii",
                "level": "warning",
                "message": (
                    "Non-ASCII characters detected in TikZ source "
                    f"({sample}). They render correctly only with xelatex and "
                    "a font that covers them (the backend switches engine "
                    "automatically). If the user did not request non-ASCII "
                    "labels, use ASCII equivalents (e.g. 'uM', 'degC', 'x'); "
                    "if a missing-glyph/font error appears, fall back to "
                    "ASCII."
                ),
            }
        )
    return findings, blocked


def _format_precheck(findings: list[dict[str, str]]) -> str:
    """把预检结果格式化为回传 LLM 的文本。"""
    if not findings:
        return ""
    lines = ["TIKZ PRECHECK:"]
    for f in findings:
        lines.append(f"- [{f['level']}] {f['message']}")
    return "\n".join(lines)


def _check_python_syntax(content: str) -> str | None:
    """对 .py 内容做 ast.parse 语法检查，返回可读错误描述或 None。"""
    try:
        ast.parse(content)
    except SyntaxError as e:
        line = e.lineno if e.lineno is not None else "?"
        col = e.offset if e.offset is not None else "?"
        return f"line {line}, col {col}: {e.msg}"
    return None


def is_internal_rel_path(rel_path: str) -> bool:
    """判断沙箱内相对路径是否位于内部运行目录（.cache / tmp）。"""
    parts = rel_path.split("/")
    return any(part in INTERNAL_DIRS for part in parts[:-1])


def _extract_tikz_issues(raw_output: str, limit: int = 8) -> list[dict[str, str]]:
    """从 latexmk 日志中提取错误与警告，用于给 Agent 质量信号。

    "Missing character" 提示图中部分文本/数字未渲染（如 nullfont），
    即使 latexmk 退出码为 0，也值得让 Agent 察觉。
    """
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("error", re.compile(r"! LaTeX Error")),
        ("error", re.compile(r"Emergency stop")),
        ("error", re.compile(r"Runaway argument")),
        ("error", re.compile(r"Undefined control sequence")),
        ("error", re.compile(r"! Package [^\n]*?Error")),
        ("error", re.compile(r"! Undefined")),
        ("error", re.compile(r"! Dimension too large")),
        ("error", re.compile(r"! Extra \\endcsname")),
        ("error", re.compile(r"! Missing control sequence inserted")),
        ("error", re.compile(r"! Missing \$ inserted")),
        ("error", re.compile(r"! Text line contains an invalid character")),
        ("error", re.compile(r"! I can't find file")),
        ("error", re.compile(r"! Package pgfplots Error")),
        ("error", re.compile(r"! Package pgfkeys Error")),
        ("warning", re.compile(r"Missing character")),
        ("warning", re.compile(r"LaTeX Warning")),
        ("warning", re.compile(r"Package [^\n]*?Warning")),
        ("warning", re.compile(r"LaTeX Font Warning")),
        ("warning", re.compile(r"Font [^\n]*?not found")),
        ("info", re.compile(r"Overfull \\hbox")),
        ("info", re.compile(r"Underfull \\hbox")),
    ]
    issues: list[dict[str, str]] = []
    seen: set[str] = set()
    for level, pat in patterns:
        for m in pat.finditer(raw_output):
            snippet = raw_output[max(0, m.start() - 120) : m.end() + 120]
            snippet = " ".join(snippet.split())
            key = snippet[:160]
            if key in seen:
                continue
            seen.add(key)
            issues.append({"level": level, "message": snippet})
            if len(issues) >= limit:
                return issues
    return issues


# 编译环境缺包识别：`File `xxx.cls' not found.` / kpathsea 的
# "I can't find file" 变体。缺 .cls/.sty 属环境问题（TeX 发行版未装该
# 宏包），编辑 TikZ 源无法修复，必须与源码错误区分对待、立即止损。
_TEX_MISSING_FILE_RE = re.compile(
    r"File [`'](?P<file>[^`'\s]+\.(?:cls|sty))['`] not found"
)
_TEX_CANT_FIND_RE = re.compile(
    r"I can't find file [`'](?P<file>[^`'\s]+\.(?:cls|sty))['`]"
)


def _detect_missing_tex_file(raw_output: str) -> str | None:
    """从编译日志中识别缺失的 .cls/.sty，返回文件名；未检测到返回 None。"""
    for pat in (_TEX_MISSING_FILE_RE, _TEX_CANT_FIND_RE):
        m = pat.search(raw_output)
        if m:
            return m.group("file")
    return None


class PlotExecutor(AgentFileToolExecutor):
    """持有单个 session 沙箱的工具执行器。"""

    _file_tool_behavior = AgentFileToolBehavior(list_header="Files in sandbox:")

    def __init__(
        self,
        sandbox: Path,
        cfg: Any | None = None,
        logger: Any | None = None,
        style_key: str | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._cfg = cfg or get_config()
        self._logger = logger or get_logger("plot_gen.executor")
        self._style_key = (
            validate_style_key(style_key)
            if style_key
            else self._cfg.features.plot_gen.default_style
        )
        self._file_count = 0
        self._written_paths: set[str] = set()
        # 编译沙箱缺失 TeX 宏包时记录文件名；后续 compile_tikz 直接短路，
        # 避免在环境问题上重复编译浪费 Agent 轮次（每次运行重建实例，无需持久化）。
        self._tikz_env_missing: str | None = None

    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """执行单个工具，返回 (回传 LLM 文本, 前端事件)。"""
        if tool_name in self._file_tool_names.values:
            return await self.execute_file_tool(tool_name, args)
        if tool_name == "python_sandbox":
            return await self._run_script(args)
        if tool_name == "compile_tikz":
            return await self._compile_tikz(args)
        if tool_name == "stats_test":
            # pandas 读取数据文件（上限 50MB）+ scipy 假设检验为 CPU 密集
            # 操作，与 generate_figure_tex 同待遇入线程池
            return await asyncio.to_thread(self._stats_test, args)
        if tool_name == "list_styles":
            return self._list_styles(args)
        if tool_name == "apply_style":
            return self._apply_style(args)
        if tool_name == "finish":
            return self._finish(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    @property
    def style_key(self) -> str:
        return self._style_key

    def _resolve(
        self,
        rel_path: str,
        *,
        allowed_suffixes: tuple[str, ...] = WRITE_SUFFIXES,
    ) -> Path:
        return resolve_project_path(
            self._sandbox,
            rel_path,
            allowed_suffixes=allowed_suffixes,
        )

    def _write_agent_file(
        self,
        file_path: str,
        content: str,
    ) -> AgentFileWrite | AgentFileToolFailure:
        fcfg = self._cfg.features.plot_gen
        if self._file_count >= fcfg.agent_max_files:
            msg = f"Maximum file count {fcfg.agent_max_files} reached"
            return AgentFileToolFailure(msg)

        try:
            resolved = self._resolve(file_path)
        except ValueError as e:
            return AgentFileToolFailure(
                str(e),
                summary=f"Path validation failed: {e}",
            )

        # 保护上传数据与控制文件：Agent 不得覆写 data/ 与 meta.json/trace.jsonl
        rel = resolved.relative_to(self._sandbox.resolve()).as_posix()
        if rel.startswith("data/") or rel in ("meta.json", "trace.jsonl"):
            msg = f"Cannot write to protected path: {file_path}"
            return AgentFileToolFailure(msg)

        max_bytes = fcfg.agent_max_file_size_kb * 1024
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            msg = f"File {file_path} exceeds {fcfg.agent_max_file_size_kb} KB"
            return AgentFileToolFailure(msg)

        # Python 静态校验：写入前做 ast.parse 语法检查，把语法错误提前到
        # 写入阶段暴露，避免白跑一轮 python_sandbox 子进程。
        if Path(file_path).suffix.lower() == ".py":
            syntax_error = _check_python_syntax(content)
            if syntax_error is not None:
                msg = (
                    f"Python syntax error in {file_path}: {syntax_error}. "
                    "Fix the syntax error before running python_sandbox."
                )
                return AgentFileToolFailure(
                    msg,
                    summary=f"Python syntax error: {syntax_error}",
                    event={
                        "file_path": file_path,
                        "syntax_error": syntax_error,
                    },
                )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content_bytes)
        if resolved.as_posix() not in self._written_paths:
            self._written_paths.add(resolved.as_posix())
            self._file_count += 1
        self._logger.info(f"Wrote {file_path} ({len(content_bytes)} bytes)")
        return AgentFileWrite(path=file_path, size=len(content_bytes))

    def _read_agent_file(
        self,
        file_path: str,
    ) -> AgentFileRead | AgentFileToolFailure:
        try:
            resolved = self._resolve(file_path)
        except ValueError as e:
            return AgentFileToolFailure(
                str(e),
                summary=f"Path validation failed: {e}",
            )
        if not resolved.exists() or not resolved.is_file():
            msg = f"File not found: {file_path}"
            return AgentFileToolFailure(msg)
        return AgentFileRead(
            path=file_path,
            content=resolved.read_text(encoding="utf-8", errors="replace"),
        )

    def _list_agent_files(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        return self.list_files()

    async def _run_script(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        script_name = args.get("script_name") or "scripts/plot.py"
        try:
            resolved = self._resolve(script_name, allowed_suffixes=(".py",))
        except ValueError as e:
            return str(e), {
                "tool": "python_sandbox",
                "ok": False,
                "success": False,
                "summary": f"Path validation failed: {e}",
            }
        if not resolved.exists():
            msg = f"Script not found: {script_name}"
            return msg, {
                "tool": "python_sandbox",
                "ok": False,
                "success": False,
                "summary": msg,
            }

        code = resolved.read_text(encoding="utf-8", errors="replace")
        run_path = self._sandbox / "scripts" / "_run_plot.py"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        # 防护引导代码先于风格注入与用户脚本执行：安装审计钩子拦截危险操作。
        run_path.write_text(
            render_guard_bootstrap(self._sandbox.resolve())
            + "\n\n"
            + render_setup_code(self._style_key)
            + "\n\n"
            + code,
            encoding="utf-8",
        )

        fcfg = self._cfg.features.plot_gen
        env: dict[str, str] = {}
        # MPLBACKEND 不属 PYTHON* 前缀，仍会被 matplotlib 读取（-I 不屏蔽）。
        env["MPLBACKEND"] = "Agg"
        # 临时目录与 matplotlib 缓存重定向进沙箱，避免正常绘图流程被防护误拦。
        cache_dir = self._sandbox / ".cache" / "matplotlib"
        tmp_dir = self._sandbox / "tmp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        env["MPLCONFIGDIR"] = str(cache_dir.resolve())
        env["TMPDIR"] = str(tmp_dir.resolve())
        env["TEMP"] = str(tmp_dir.resolve())
        env["TMP"] = str(tmp_dir.resolve())

        # sandbox docker 后端：容器自带 python3，宿主解释器配置不适用
        if cached_backend_for("plot") == "docker":
            command = ["python3", "-I", "-B", str(run_path.resolve())]
        else:
            python = fcfg.python_interpreter or sys.executable
            command = [python, "-I", "-B", str(run_path.resolve())]

        spec = ExecSpec(
            command=command,
            cwd=str(self._sandbox.resolve()),
            image_kind="plot",
            env=env,
            timeout_seconds=float(fcfg.sandbox_exec_timeout),
        )

        self._logger.info(
            "plot python_sandbox | script=%s | cwd=%s", script_name, self._sandbox
        )
        result_exec = await get_sandbox_runner().execute(
            spec, user_id=current_sandbox_user_id()
        )
        # 多用户模式：输出中的沙箱绝对路径统一脱敏，OOM 等异常分支的
        # 截断输出同样来自这里，保证出口一致；单用户保留原始输出
        if self._cfg.multi_user.enabled:
            stdout = sanitize_output_paths(result_exec.stdout, self._sandbox.resolve())
            stderr = sanitize_output_paths(result_exec.stderr, self._sandbox.resolve())
        else:
            stdout = result_exec.stdout
            stderr = result_exec.stderr
        returncode = result_exec.returncode

        if result_exec.timed_out:
            result = ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                traceback=f"Command timed out after {fcfg.sandbox_exec_timeout} seconds",
                timed_out=True,
                summary="Command timed out",
            )
            return self._format_execution_result(result)
        if result_exec.oom_killed:
            result = ExecutionResult(
                success=False,
                stdout=stdout[: fcfg.sandbox_max_stdout_chars],
                stderr=stderr[: fcfg.sandbox_max_stdout_chars],
                traceback=(
                    "Memory limit exceeded: the plotting process was killed by "
                    "the sandbox OOM guard. Reduce data size or figure DPI."
                ),
                timed_out=False,
                artifacts=self.scan_artifacts(),
                summary="Execution killed by memory limit",
            )
            return self._format_execution_result(result)

        artifacts = self.scan_artifacts()
        traceback = stderr[-2000:] if not returncode == 0 else ""
        result = ExecutionResult(
            success=returncode == 0,
            stdout=stdout[: fcfg.sandbox_max_stdout_chars],
            stderr=stderr[: fcfg.sandbox_max_stdout_chars],
            traceback=traceback,
            timed_out=False,
            artifacts=artifacts,
            summary=(
                "Execution succeeded"
                if returncode == 0
                else "Execution failed; analyze the traceback, fix the script, and rerun"
            ),
        )
        # 受控 TikZ 生成：脚本成功执行后，若写入了 tikz_spec.json +
        # 数据 CSV，则由后端确定性渲染 artifacts/figure.tex（替代
        # matplot2tikz，也避免 LLM 手写 PGFPlots 的语法/列名错误）。
        # generate_figure_tex 涉及 pandas 读 CSV 与文件写入等同步阻塞 I/O，
        # 必须放入线程池，避免阻塞事件循环。
        tikz_info: dict[str, Any] | None = None
        if returncode == 0:
            tikz_info = await asyncio.to_thread(generate_figure_tex, self._sandbox)
            if tikz_info.get("generated"):
                result.artifacts = self.scan_artifacts()
        return self._format_execution_result(result, tikz_info)

    async def _compile_tikz(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        fcfg = self._cfg.features.plot_gen
        if not fcfg.tikz_compile_enabled:
            msg = "TikZ compilation is disabled by config"
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        if self._tikz_env_missing:
            msg = (
                f"TikZ compilation BLOCKED: the compile sandbox is missing "
                f"`{self._tikz_env_missing}` (TeX package not installed). This "
                "is an environment problem and cannot be fixed by editing the "
                "TikZ source, rewriting tikz_spec.json, or rerunning "
                "python_sandbox. Do not call `compile_tikz` again; call "
                "`finish` with an explicit warning that the figure was "
                "generated but could not be compiled because a TeX package is "
                "missing (the administrator must rebuild the tex image; see "
                "docker/README.md)."
            )
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "environment_error": True,
                "missing_tex_file": self._tikz_env_missing,
                "log": msg,
                "summary": (
                    "TikZ compilation blocked: TeX package "
                    f"{self._tikz_env_missing} missing in the compile environment"
                ),
            }

        tikz_file = args.get("tikz_file") or "artifacts/figure.tex"
        try:
            tikz_path = self._resolve(tikz_file, allowed_suffixes=(".tex",))
        except ValueError as e:
            return str(e), {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "log": str(e),
                "summary": f"Path validation failed: {e}",
            }
        if not tikz_path.exists():
            msg = (
                f"TikZ file not found: {tikz_file}. The backend only generates "
                "figure.tex when `python_sandbox` succeeds: if you changed "
                "artifacts/tikz_spec.json or the data CSVs, rerun `python_sandbox` "
                "first; or hand-write the TikZ source and save it to "
                "artifacts/figure.tex, then call `compile_tikz` again."
            )
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        artifacts_dir = self._sandbox / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        content = tikz_path.read_text(encoding="utf-8", errors="replace")

        # 轻量预检：命中大概率必失败的规则时跳过实际编译，直接把修正建议
        # 回传 Agent，避免在故障 TikZ 输出上浪费编译轮次。
        precheck_findings, precheck_blocked = _precheck_tikz(content)
        precheck_text = _format_precheck(precheck_findings)
        if precheck_blocked:
            msg = (
                "TikZ PRECHECK FAILED (compilation skipped).\n\n"
                + precheck_text
                + "\n\nFix the TikZ source (rewrite the plot by hand in "
                "PGFPlots if needed) and call compile_tikz again."
            )
            summary = "TikZ PRECHECK failed; fix the issues above and recompile"
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": summary,
                "precheck": precheck_findings,
                "precheck_blocked": True,
                "issues": [],
                "warnings": [],
            }

        # 完整 standalone 文档（含 \documentclass）直接作为主文件编译，
        # 避免再 \input 包装造成重复 \documentclass；片段则包装进主文件。
        is_full_doc = bool(re.search(r"\\documentclass", content))
        if tikz_path.resolve().parent.resolve() != artifacts_dir.resolve():
            # 目标不在 artifacts/ 下时复制进去，保证相对编译目录可解析
            shutil.copy2(tikz_path, artifacts_dir / tikz_path.name)
            tikz_path = artifacts_dir / tikz_path.name
        main_path = artifacts_dir / "tikz_main.tex"
        if is_full_doc:
            main_path.write_text(content, encoding="utf-8")
        else:
            main_path.write_text(
                r"""\documentclass{standalone}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\begin{document}
\input{"""
                + tikz_path.name
                + r"""}
\end{document}
""",
                encoding="utf-8",
            )

        # 引擎检测基于真实图内容而非包装文件，避免中文标签被误判为 pdflatex；
        # 同步读盘检测入线程池
        engine = await asyncio.to_thread(detect_engine_from_file, tikz_path)
        try:
            tex_result = await run_tex_compile(
                "tikz_main.tex",
                artifacts_dir,
                fcfg.tikz_compile_timeout,
                engine=engine,
            )
        except FileNotFoundError:
            msg = "latexmk is not installed or is not on PATH"
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        log = tex_result.raw_output[-4000:]
        issues = _extract_tikz_issues(tex_result.raw_output)
        errors = [i for i in issues if i["level"] == "error"]
        # nullfont 的 "Missing character" 来自版本字符串（如 "This is pdfTeX..."），
        # 与实际图表标签无关，过滤掉可避免 Agent 对无害噪音做无效修复。
        warnings = [
            i
            for i in issues
            if i["level"] == "warning" and "nullfont" not in i["message"].lower()
        ]
        # latexmk 退出码为 0 但日志含真实错误时，仍视为编译失败
        success = tex_result.success and not errors
        # 环境缺包快速止损：缺失 .cls/.sty 无法通过修改 TikZ 源修复，
        # 记录后直接返回带环境错误标记的消息，避免 Agent 消耗重试预算。
        missing_tex_file = (
            _detect_missing_tex_file(tex_result.raw_output) if not success else None
        )
        if missing_tex_file is not None:
            self._tikz_env_missing = missing_tex_file
            self._logger.warning(
                "plot_gen compile_tikz | sandbox missing TeX package: %s",
                missing_tex_file,
            )
            msg = (
                f"TikZ compilation FAILED due to a MISSING TeX PACKAGE in the "
                f"compile environment: `{missing_tex_file}`.\n\nThis is an "
                "environment problem: it cannot be fixed by editing the TikZ "
                "source, rewriting tikz_spec.json, or rerunning "
                "python_sandbox. Do NOT retry `compile_tikz`.\n\nCall "
                "`finish` now with an explicit warning that the figure was "
                "generated but could not be compiled because a TeX package is "
                "missing from the compile sandbox (the administrator must add "
                "it to the tex image; see docker/README.md)."
            )
            return msg, {
                "tool": "compile_tikz",
                "ok": False,
                "success": False,
                "environment_error": True,
                "missing_tex_file": missing_tex_file,
                "log": log,
                "summary": (
                    f"TikZ compilation blocked by missing TeX package "
                    f"({missing_tex_file}); environment problem, not a source problem"
                ),
                "precheck": precheck_findings,
                "precheck_blocked": False,
                "warnings": [i["message"] for i in warnings][:8],
                "issues": issues[:8],
            }
        issue_text = ""
        if errors or warnings:
            lines = []
            for it in (errors + warnings)[:8]:
                lines.append(f"[{it['level']}] {it['message']}")
            issue_text = (
                "\n\nTIKZ ISSUES DETECTED IN LOG:\n"
                + "\n".join(lines)
                + "\n\nFix any errors. For 'Missing character' warnings, check "
                "that all text/labels in the figure are actually rendered; "
                "warnings mentioning 'nullfont' come from version strings and "
                "are harmless."
            )
        if success:
            summary = (
                "TikZ compilation succeeded with warnings"
                if (errors or warnings)
                else "TikZ compilation succeeded"
            )
            text = log
            if precheck_text:
                text += "\n\n" + precheck_text
            if issue_text:
                text += issue_text
        else:
            summary = "TikZ compilation failed; inspect the log and fix the TikZ source"
            text = "TikZ compilation FAILED.\n\n" + log
            if precheck_text:
                text += "\n\n" + precheck_text
            text += issue_text
        event = {
            "tool": "compile_tikz",
            "ok": success,
            "success": success,
            "environment_error": False,
            "log": log,
            "summary": summary,
            "precheck": precheck_findings,
            "precheck_blocked": False,
            "warnings": [i["message"] for i in warnings][:8],
            "issues": issues[:8],
        }
        return text, event

    def _stats_test(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            result = self._run_stats_test(args)
        except Exception as e:
            return str(e), {
                "tool": "stats_test",
                "ok": False,
                "summary": f"Stats test failed: {e}",
            }
        return result.summary, {
            "tool": "stats_test",
            "ok": True,
            "summary": result.summary,
            "result": result.model_dump(),
        }

    def _run_stats_test(self, args: dict[str, Any]) -> StatsTestResult:
        data_file = args.get("data_file", "")
        try:
            data_path = self._resolve(data_file, allowed_suffixes=ALLOWED_DATA_SUFFIXES)
        except ValueError as e:
            raise ValueError(f"Invalid data_file: {e}")
        suffix = data_path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(data_path)
        elif suffix == ".tsv":
            df = pd.read_csv(data_path, sep="\t")
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(data_path)
        elif suffix == ".json":
            df = pd.read_json(data_path)
        else:
            raise ValueError(f"Unsupported data file: {data_file}")

        test = args.get("test", "")
        x = args.get("x")
        y = args.get("y")
        group = args.get("group")
        alternative = args.get("alternative", "two-sided")
        self._validate_stats_inputs(df, data_file, test, x, y, group)
        from scipy import stats

        if test in ("t-test", "wilcoxon", "anova", "kruskal"):
            groups = [df.loc[df[group] == g, y].dropna() for g in df[group].unique()]
            if test == "t-test":
                if len(groups) != 2:
                    raise ValueError("t-test requires exactly two groups")
                stat, p = stats.ttest_ind(groups[0], groups[1], equal_var=False)
                return StatsTestResult(
                    test=test,
                    statistic=float(stat),
                    p_value=float(p),
                    assumptions=[
                        "两组近似正态或样本量足够，方差不齐时使用 Welch 检验。"
                    ],
                    summary=f"t-test: statistic={stat:.4f}, p={p:.4f}",
                )
            if test == "wilcoxon":
                if len(groups) != 2:
                    raise ValueError("wilcoxon requires exactly two groups")
                stat, p = stats.mannwhitneyu(
                    groups[0], groups[1], alternative=alternative
                )
                return StatsTestResult(
                    test=test,
                    statistic=float(stat),
                    p_value=float(p),
                    assumptions=["两组独立样本，秩和检验不要求正态分布。"],
                    summary=f"Mann-Whitney U: statistic={stat:.4f}, p={p:.4f}",
                )
            if test == "anova":
                stat, p = stats.f_oneway(*groups)
                return StatsTestResult(
                    test=test,
                    statistic=float(stat),
                    p_value=float(p),
                    df=len(groups) - 1,
                    assumptions=["各组近似正态且方差齐性。"],
                    summary=f"ANOVA: F={stat:.4f}, p={p:.4f}",
                )
            stat, p = stats.kruskal(*groups)
            return StatsTestResult(
                test=test,
                statistic=float(stat),
                p_value=float(p),
                assumptions=["非参数方差分析，不要求正态分布。"],
                summary=f"Kruskal-Wallis: H={stat:.4f}, p={p:.4f}",
            )

        if test in ("pearson", "spearman"):
            xv = pd.to_numeric(df[x], errors="coerce").dropna()
            yv = pd.to_numeric(df[y], errors="coerce").dropna()
            common = xv.index.intersection(yv.index)
            if test == "pearson":
                stat, p = stats.pearsonr(xv.loc[common], yv.loc[common])
                summary = f"Pearson r={stat:.4f}, p={p:.4f}"
            else:
                stat, p = stats.spearmanr(xv.loc[common], yv.loc[common])
                summary = f"Spearman rho={stat:.4f}, p={p:.4f}"
            return StatsTestResult(
                test=test,
                statistic=float(stat),
                p_value=float(p),
                assumptions=["相关性不代表因果。"],
                summary=summary,
            )

        if test == "chi2":
            table = pd.crosstab(df[x], df[y])
            stat, p, df_res, _ = stats.chi2_contingency(table)
            return StatsTestResult(
                test=test,
                statistic=float(stat),
                p_value=float(p),
                df=int(df_res),
                assumptions=["期望频数不宜过小。"],
                summary=f"Chi-square: chi2={stat:.4f}, p={p:.4f}",
            )

        raise ValueError(f"Unsupported test: {test}")

    @staticmethod
    def _validate_stats_inputs(
        df: pd.DataFrame,
        data_file: str,
        test: str,
        x: str | None,
        y: str | None,
        group: str | None,
    ) -> None:
        """按测试类型校验 stats_test 必填参数，缺失/类型错误时给出明确消息。

        此前 group 分支直接用缺失的 y 做 `df.loc[df[group] == g, y]`，
        会抛晦涩的 KeyError；此处把参数校验提前并指明缺失列与可用列。
        """

        def _missing(cols: list[tuple[str, str | None]]) -> list[str]:
            return [name for name, c in cols if not c or c not in df.columns]

        def _numeric_ok(col: str) -> bool:
            coerced = pd.to_numeric(df[col], errors="coerce")
            return int(coerced.notna().sum()) >= 2

        if test in ("t-test", "wilcoxon", "anova", "kruskal"):
            missing = _missing([("group", group), ("y", y)])
            if missing:
                raise ValueError(
                    f"stats_test '{test}' requires columns 'group' and 'y'; "
                    f"missing: {missing}. Available columns: {list(df.columns)}"
                )
            if not _numeric_ok(y):
                raise ValueError(
                    f"stats_test '{test}' requires numeric column 'y'; column "
                    f"'{y}' has no numeric values in {data_file}"
                )
        elif test in ("pearson", "spearman"):
            missing = _missing([("x", x), ("y", y)])
            if missing:
                raise ValueError(
                    f"stats_test '{test}' requires numeric columns 'x' and 'y'; "
                    f"missing: {missing}. Available columns: {list(df.columns)}"
                )
            for c in (x, y):
                if not _numeric_ok(c):
                    raise ValueError(
                        f"stats_test '{test}' requires numeric columns; column "
                        f"'{c}' has no numeric values in {data_file}"
                    )
        elif test == "chi2":
            missing = _missing([("x", x), ("y", y)])
            if missing:
                raise ValueError(
                    f"stats_test 'chi2' requires categorical columns 'x' and "
                    f"'y'; missing: {missing}. Available columns: {list(df.columns)}"
                )
            for c in (x, y):
                if pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique() > 20:
                    raise ValueError(
                        f"stats_test 'chi2' requires categorical columns; column "
                        f"'{c}' looks numeric ({df[c].nunique()} unique values) "
                        f"in {data_file}"
                    )
        else:
            raise ValueError(f"Unsupported test: {test}")

    def _list_styles(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        styles = list_styles()
        text = "\n".join(f"- {s['key']}: {s['label']}" for s in styles)
        return text, {
            "tool": "list_styles",
            "ok": True,
            "summary": f"{len(styles)} styles",
        }

    def _apply_style(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        style = args.get("style", "")
        if style not in {s["key"] for s in list_styles()}:
            msg = f"Unknown style: {style}"
            return msg, {"tool": "apply_style", "ok": False, "summary": msg}
        self._style_key = validate_style_key(style)
        msg = f"Style changed to {self._style_key}"
        return msg, {
            "tool": "apply_style",
            "ok": True,
            "summary": msg,
            "style": self._style_key,
        }

    def _finish(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = args.get("summary", "")
        files = self.list_files()
        artifacts = self.scan_artifacts()
        text = (
            f"Project finished. Summary: {summary}\n"
            f"Files ({len(files)}): " + ", ".join(f["path"] for f in files)
        )
        return text, {
            "tool": "finish",
            "ok": True,
            "summary": "Finished",
            "artifacts": [a.model_dump() for a in artifacts],
        }

    def list_files(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        root = self._sandbox.resolve()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if is_internal_rel_path(rel):
                continue
            result.append(
                {
                    "path": rel,
                    "size": p.stat().st_size,
                }
            )
        return result

    def scan_artifacts(self) -> list[ArtifactInfo]:
        root = self._sandbox.resolve()
        result: list[ArtifactInfo] = []
        for p in (
            sorted((self._sandbox / "artifacts").rglob("*"))
            if (self._sandbox / "artifacts").exists()
            else []
        ):
            if not p.is_file():
                continue
            if p.suffix.lower() not in ARTIFACT_SUFFIXES:
                continue
            result.append(
                ArtifactInfo(
                    path=p.relative_to(root).as_posix(),
                    size=p.stat().st_size,
                    kind=p.suffix.lstrip("."),
                )
            )
        return result

    def _format_execution_result(
        self,
        result: ExecutionResult,
        tikz_info: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if result.success:
            text = "Execution succeeded. Artifacts:\n" + "\n".join(
                f"- {a.path}" for a in result.artifacts
            )
        else:
            text = "Execution FAILED.\n" + (
                result.traceback or result.stderr or result.stdout
            )
        event = {
            "tool": "python_sandbox",
            "ok": result.success,
            "success": result.success,
            "summary": result.summary,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "traceback": result.traceback,
            "timed_out": result.timed_out,
            "artifacts": [a.model_dump() for a in result.artifacts],
        }
        if tikz_info is not None:
            text += "\n\n" + tikz_info.get("message", "")
            for w in tikz_info.get("warnings", []):
                text += f"\nTIKZ WARNING: {w}"
            event["tikz"] = {
                "generated": tikz_info.get("generated", False),
                "error": tikz_info.get("error"),
                "warnings": tikz_info.get("warnings", []),
            }
        return text, event

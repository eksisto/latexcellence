"""科研绘图沙箱执行器。"""

import asyncio
import os
import re
import shutil
import signal
import subprocess
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
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import detect_engine_from_file, run_tex_compile

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


def is_internal_rel_path(rel_path: str) -> bool:
    """判断沙箱内相对路径是否位于内部运行目录（.cache / tmp）。"""
    parts = rel_path.split("/")
    return any(part in INTERNAL_DIRS for part in parts[:-1])


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """终止子进程及其子孙进程，防止超时后残留孤儿进程。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception:
            pass
    try:
        # POSIX：子进程以 start_new_session=True 启动，进程组 ID == 子进程 PID
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, AttributeError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class PlotExecutor:
    """持有单个 session 沙箱的工具执行器。"""

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

    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """执行单个工具，返回 (回传 LLM 文本, 前端事件)。"""
        if tool_name == "write_file":
            return await self._write_file(args)
        if tool_name == "read_file":
            return self._read_file(args)
        if tool_name == "list_files":
            return self._list_files(args)
        if tool_name == "python_sandbox":
            return await self._run_script(args)
        if tool_name == "compile_tikz":
            return await self._compile_tikz(args)
        if tool_name == "stats_test":
            return self._stats_test(args)
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

    async def _write_file(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        fcfg = self._cfg.features.plot_gen
        if self._file_count >= fcfg.agent_max_files:
            msg = f"Maximum file count {fcfg.agent_max_files} reached"
            return msg, {"tool": "write_file", "ok": False, "summary": msg}

        try:
            resolved = self._resolve(file_path)
        except ValueError as e:
            return str(e), {
                "tool": "write_file",
                "ok": False,
                "summary": f"Path validation failed: {e}",
            }

        # 保护上传数据与控制文件：Agent 不得覆写 data/ 与 meta.json/trace.jsonl
        rel = resolved.relative_to(self._sandbox.resolve()).as_posix()
        if rel.startswith("data/") or rel in ("meta.json", "trace.jsonl"):
            msg = f"Cannot write to protected path: {file_path}"
            return msg, {"tool": "write_file", "ok": False, "summary": msg}

        max_bytes = fcfg.agent_max_file_size_kb * 1024
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            msg = f"File {file_path} exceeds {fcfg.agent_max_file_size_kb} KB"
            return msg, {"tool": "write_file", "ok": False, "summary": msg}

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content_bytes)
        if resolved.as_posix() not in self._written_paths:
            self._written_paths.add(resolved.as_posix())
            self._file_count += 1
        summary = f"Wrote {file_path} ({len(content_bytes)} bytes)"
        self._logger.info(summary)
        return summary, {
            "tool": "write_file",
            "ok": True,
            "summary": summary,
            "file_path": file_path,
            "size": len(content_bytes),
        }

    def _read_file(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        file_path = args.get("file_path", "")
        try:
            resolved = self._resolve(file_path)
        except ValueError as e:
            return str(e), {
                "tool": "read_file",
                "ok": False,
                "summary": f"Path validation failed: {e}",
            }
        if not resolved.exists() or not resolved.is_file():
            msg = f"File not found: {file_path}"
            return msg, {"tool": "read_file", "ok": False, "summary": msg}
        content = resolved.read_text(encoding="utf-8", errors="replace")
        truncated = content[:8000] + ("\n...[truncated]" if len(content) > 8000 else "")
        return truncated, {"tool": "read_file", "ok": True, "summary": file_path}

    def _list_files(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        files = self.list_files()
        if not files:
            return "(workspace is empty)", {
                "tool": "list_files",
                "ok": True,
                "summary": "Empty",
            }
        text = "Files in sandbox:\n" + "\n".join(
            f"- {f['path']} ({f['size']} bytes)" for f in files
        )
        return text, {
            "tool": "list_files",
            "ok": True,
            "summary": f"{len(files)} files",
        }

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
        python = fcfg.python_interpreter or sys.executable
        env = os.environ.copy()
        # -I 已隐含 -E/-s（忽略 PYTHON* 环境变量与 user site），无需再显式设置。
        # MPLBACKEND 不属 PYTHON* 前缀，仍会被 matplotlib 读取。
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

        self._logger.info(
            "plot python_sandbox | script=%s | cwd=%s", script_name, self._sandbox
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                python,
                "-I",
                "-B",
                str(run_path.resolve()),
                cwd=str(self._sandbox.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=fcfg.sandbox_exec_timeout
            )
        except asyncio.TimeoutError:
            _kill_process_tree(proc)
            stdout_b, stderr_b = await proc.communicate()
            result = ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                traceback=f"Command timed out after {fcfg.sandbox_exec_timeout} seconds",
                timed_out=True,
                summary="Command timed out",
            )
            return self._format_execution_result(result)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        artifacts = self.scan_artifacts()
        traceback = stderr[-2000:] if not proc.returncode == 0 else ""
        result = ExecutionResult(
            success=proc.returncode == 0,
            stdout=stdout[: fcfg.sandbox_max_stdout_chars],
            stderr=stderr[: fcfg.sandbox_max_stdout_chars],
            traceback=traceback,
            timed_out=False,
            artifacts=artifacts,
            summary=(
                "Execution succeeded"
                if proc.returncode == 0
                else "Execution failed; analyze the traceback, fix the script, and rerun"
            ),
        )
        return self._format_execution_result(result)

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
            msg = f"TikZ file not found: {tikz_file}"
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

        # 引擎检测基于真实图内容而非包装文件，避免中文标签被误判为 pdflatex
        engine = detect_engine_from_file(tikz_path)
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
        event = {
            "tool": "compile_tikz",
            "ok": True,
            "success": tex_result.success,
            "log": log,
            "summary": (
                "TikZ compilation succeeded"
                if tex_result.success
                else "TikZ compilation failed; inspect the log and fix the TikZ source"
            ),
        }
        text = log if tex_result.success else ("TikZ compilation FAILED.\n\n" + log)
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
        from scipy import stats

        if test in ("t-test", "wilcoxon", "anova", "kruskal"):
            if not group or group not in df.columns:
                raise ValueError("group column is required for this test")
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
            if not x or not y or x not in df.columns or y not in df.columns:
                raise ValueError("x and y numeric columns are required")
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
            if not x or not y or x not in df.columns or y not in df.columns:
                raise ValueError("x and y categorical columns are required")
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
        self, result: ExecutionResult
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
        return text, event

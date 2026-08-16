"""模板生成 Agent 私有工具执行器（沙箱文件 I/O 与编译）。"""

import asyncio
from pathlib import Path
from typing import Any

from backend.utils.agent_file_tools import (
    AgentFileRead,
    AgentFileToolExecutor,
    AgentFileToolFailure,
    AgentFileWrite,
    normalize_line_range,
)
from backend.utils.latex_json_guard import (
    check_latex_environments,
    check_latex_text,
    check_latex_text_lite,
)
from backend.utils.tex_compile import run_tex_compile

from .compile import (
    _attach_source_context,
    _extract_errors,
    _extract_quality_warnings,
    _extract_unresolved_reference_errors,
    _format_error_analysis,
    _format_quality_feedback,
    _read_log_text,
    _trim_log,
)
from .constants import (
    _LATEX_ENV_CHECK_SUFFIXES,
    _LATEX_SOURCE_SUFFIXES,
    _LOG_TRUNCATE,
)
from .sandbox import resolve_sandbox_path
from .validation import (
    _check_body_style_commands,
    _check_bst_output_escapes,
    _check_cjk_support,
    _check_class_option_order,
    _check_provides_date,
    _check_public_body_commands,
    _check_setter_accessors,
)


class _ToolExecutor(AgentFileToolExecutor):
    """沙箱内工具执行器，封装路径校验、文件 I/O 与编译。"""

    _supports_line_range = True

    def __init__(
        self,
        sandbox: Path,
        cfg: Any,
        logger: Any,
    ) -> None:
        self._sandbox = sandbox
        self._cfg = cfg
        self._logger = logger
        # 已写入的唯一文件路径（相对路径，正斜杠）。预算按"不同文件数"
        # 计算：重写已有文件不消耗预算，防止 Agent 把轮次耗在反复修复
        # 同一批文件上时被预算卡死。
        self._written_paths: set[str] = set()
        # 记录最近一次失败编译对应的源码快照。若源码完全未变，重复编译
        # 只会产生相同结果，直接返回可操作的干预信息，避免浪费超时预算。
        self._last_failed_source_hash: str | None = None
        # 记录最近一次成功编译对应的源码快照。源码未变时重复"成功编译"
        # 同样是空转：项目已经验证过，应该 finish 或先改源码。
        self._last_success_source_hash: str | None = None

    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """执行单个工具，返回 (回传 LLM 的文本, 前端事件 dict)。"""
        if tool_name in self._file_tool_names.values:
            return await self.execute_file_tool(tool_name, args)
        if tool_name == "compile_latex":
            return await self._compile_latex(args)
        if tool_name == "finish":
            return self._finish(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    # ── write_file ────────────────────────────────────────────────────

    def _write_agent_file(
        self,
        file_path: str,
        content: str,
    ) -> AgentFileWrite | AgentFileToolFailure:
        fcfg = self._cfg.features.template_gen

        try:
            resolved = resolve_sandbox_path(self._sandbox, file_path)
        except ValueError as e:
            return AgentFileToolFailure(
                str(e),
                summary=f"Path validation failed: {e}",
            )

        rel_path = resolved.relative_to(self._sandbox).as_posix()
        if (
            rel_path not in self._written_paths
            and len(self._written_paths) >= fcfg.agent_max_files
        ):
            msg = (
                f"Maximum distinct-file count {fcfg.agent_max_files} reached; "
                f"refusing to create a NEW file {file_path}. You may still "
                "REWRITE (overwrite) existing files to fix them, but do not "
                "create any additional files."
            )
            return AgentFileToolFailure(msg)

        max_bytes = fcfg.agent_max_file_size_kb * 1024
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            msg = (
                f"File {file_path} exceeds the maximum size "
                f"of {fcfg.agent_max_file_size_kb} KB"
            )
            return AgentFileToolFailure(msg)

        # 写盘前内容校验：LaTeX/BibTeX 走各自的结构规则，README 等文档只做
        # 传输完整性检查。不能把普通 Markdown 的花括号/$ 当成 LaTeX 语法，
        # 也不能把 BibTeX URL 中的百分号当作 LaTeX 注释。
        suffix = resolved.suffix.lower()
        if suffix == ".bib":
            latex_error = check_latex_text(content, bibtex=True)
        elif suffix in _LATEX_SOURCE_SUFFIXES - {".bib", ".bst"}:
            latex_error = check_latex_text(content)
        elif suffix == ".bst":
            latex_error = check_latex_text(content, bibtex=True)
        else:
            latex_error = check_latex_text_lite(content)
        if latex_error:
            self._logger.warning(
                f"write_file rejected corrupt LaTeX content for "
                f"{file_path}: {latex_error}"
            )
            msg = f"File {file_path} was rejected: {latex_error}."
            if suffix in _LATEX_SOURCE_SUFFIXES:
                msg += (
                    " In JSON tool arguments, each LaTeX backslash must be "
                    "escaped as two backslash characters (\\\\); resend the "
                    "complete file with intact LaTeX."
                )
            else:
                msg += " Resend the complete file with intact text content."
            return AgentFileToolFailure(msg)

        if resolved.suffix.lower() in {".cls", ".sty"}:
            provides_error = _check_provides_date(content)
            if provides_error:
                self._logger.warning(
                    f"write_file rejected invalid Provides date for "
                    f"{file_path}: {provides_error}"
                )
                msg = f"File {file_path} was rejected: {provides_error}"
                return AgentFileToolFailure(msg)
            if resolved.suffix.lower() == ".cls":
                option_order_error = _check_class_option_order(content)
                if option_order_error:
                    self._logger.warning(
                        f"write_file rejected class option order for "
                        f"{file_path}: {option_order_error}"
                    )
                    msg = f"File {file_path} was rejected: {option_order_error}"
                    return AgentFileToolFailure(msg)
            setter_accessor_error = _check_setter_accessors(content)
            if setter_accessor_error:
                self._logger.warning(
                    f"write_file rejected undefined setter accessor for "
                    f"{file_path}: {setter_accessor_error}"
                )
                msg = f"File {file_path} was rejected: {setter_accessor_error}"
                return AgentFileToolFailure(msg)

        if resolved.suffix.lower() == ".bst":
            bst_error = _check_bst_output_escapes(content)
            if bst_error:
                self._logger.warning(
                    f"write_file rejected invalid .bst escapes for "
                    f"{file_path}: {bst_error}"
                )
                msg = f"File {file_path} was rejected: {bst_error}"
                return AgentFileToolFailure(msg)

        if resolved.suffix.lower() in _LATEX_ENV_CHECK_SUFFIXES:
            cjk_error = _check_cjk_support(
                content,
                project_root=self._sandbox,
                current_path=resolved,
            )
            if cjk_error:
                self._logger.warning(
                    f"write_file rejected missing CJK support for "
                    f"{file_path}: {cjk_error}"
                )
                msg = f"File {file_path} was rejected: {cjk_error}"
                return AgentFileToolFailure(msg)
            body_style_error = _check_body_style_commands(content)
            if body_style_error:
                self._logger.warning(
                    f"write_file rejected style definition in body for "
                    f"{file_path}: {body_style_error}"
                )
                msg = f"File {file_path} was rejected: {body_style_error}"
                return AgentFileToolFailure(msg)

        # 环境配对错误在 TeX 编译时通常会级联成难以定位的错误。对源文件
        # 做一次轻量静态检查，把明确的结构错误在写盘阶段反馈给 Agent；
        # 不检查 README/BibTeX，因为它们不遵循 LaTeX 环境语法。
        if resolved.suffix.lower() in _LATEX_ENV_CHECK_SUFFIXES:
            environment_error = check_latex_environments(content)
            if environment_error:
                self._logger.warning(
                    f"write_file rejected mismatched LaTeX environments for "
                    f"{file_path}: {environment_error}"
                )
                msg = (
                    f"File {file_path} was rejected: {environment_error}. "
                    "Fix the begin/end environment pair and resend the complete "
                    "file."
                )
                return AgentFileToolFailure(msg)

            public_api_error = _check_public_body_commands(content)
            if public_api_error:
                self._logger.warning(
                    f"write_file rejected private body command for "
                    f"{file_path}: {public_api_error}"
                )
                msg = (
                    f"File {file_path} was rejected: {public_api_error} "
                    "Keep internal @ macros inside the .cls/.sty implementation "
                    "and resend the complete body file using its public API."
                )
                return AgentFileToolFailure(msg)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content_bytes)
        self._written_paths.add(rel_path)
        # 源码已变化：允许（并要求）重新编译，清空成功快照防呆。
        self._last_success_source_hash = None
        self._logger.info(f"write_file: {file_path} ({len(content_bytes)} bytes)")

        return AgentFileWrite(path=file_path, size=len(content_bytes))

    # ── read_file ─────────────────────────────────────────────────────

    def _read_agent_file(
        self,
        file_path: str,
    ) -> AgentFileRead | AgentFileToolFailure:
        try:
            resolved = resolve_sandbox_path(self._sandbox, file_path, for_read=True)
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
            content=resolved.read_text(encoding="utf-8"),
        )

    def _prepare_agent_line_range(
        self,
        content: str,
        value: Any,
    ) -> tuple[int, int] | None:
        return normalize_line_range(value, coerce=True)

    # ── list_files ────────────────────────────────────────────────────

    def _list_agent_files(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        return self.list_files()

    # ── compile_latex ─────────────────────────────────────────────────

    @staticmethod
    def _clean_generated_files(root: Path) -> None:
        """编译前删除上一轮遗留的辅助文件，避免陈旧 .bbl/.aux 干扰。

        latexmk 的 ``-gg`` 在某些版本/配置下不会可靠地重建已被修改
        .bst/.bib 对应的 .bbl；显式清理辅助产物是跨发行版通用的做法。
        保留 .log 以便测试/调试路径在 fake 编译时仍能读取上一份日志，
        真实编译会重新生成 .log。
        """
        generated_suffixes = {
            ".aux",
            ".bbl",
            ".bcf",
            ".fdb_latexmk",
            ".fls",
            ".lof",
            ".lot",
            ".nav",
            ".out",
            ".run.xml",
            ".snm",
            ".synctex.gz",
            ".toc",
            ".vrb",
            ".xdv",
        }
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in generated_suffixes:
                try:
                    path.unlink()
                except OSError:
                    continue

    async def _compile_latex(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        main_file = args.get("main_file", "")
        fcfg = self._cfg.features.template_gen
        try:
            resolved = resolve_sandbox_path(self._sandbox, main_file)
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

        source_hash = await asyncio.to_thread(self._source_hash)
        if self._last_success_source_hash == source_hash:
            msg = (
                "Compilation skipped: project source is unchanged since the "
                "previous SUCCESSFUL compile, so the project is already "
                "verified. Do not recompile without a meaningful source "
                "change. Either call finish (with main_file and summary) "
                "now, or edit a source file with write_file first."
            )
            return msg, {
                "tool": "compile_latex",
                "ok": True,
                "success": True,
                "already_verified": True,
                "log": msg,
                "summary": msg,
            }
        if self._last_failed_source_hash == source_hash:
            msg = (
                "Compilation skipped: project source is unchanged since the "
                "previous failed compile. Read the source excerpt and log "
                "context from that failure, make a meaningful source edit, "
                "then call compile_latex again."
            )
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "compile_skipped": True,
                "log": msg,
                "summary": msg,
            }

        # 只有确认要真实编译时才清理辅助文件（already_verified /
        # compile_skipped 分支已先返回）。这样能避免 latexmk 复用旧的
        # .bbl/.aux，导致 .bst/.bib/class 修改后仍用上一轮产物编译。
        await asyncio.to_thread(self._clean_generated_files, self._sandbox)

        timeout = fcfg.agent_compile_timeout
        self._logger.info(f"agent compile | main={main_file} | cwd={self._sandbox}")

        try:
            tex_result = await run_tex_compile(
                main_file,
                self._sandbox,
                timeout,
                # 每次源码发生变化都从干净的辅助文件状态开始。模板 Agent
                # 可能在修复过程中切换 biblatex/BibTeX、class 或 engine；
                # latexmk 默认复用旧 .aux/.bcf 会把上一轮格式的命令带入本轮，
                # 产生与当前源码无关的假错误。
                extra_args=["-halt-on-error", "-gg"],
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

        if tex_result.timed_out:
            msg = f"Compilation timed out ({timeout}s)"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        full_log = tex_result.raw_output
        success = tex_result.success

        trimmed = _trim_log(full_log, success)
        self._logger.info(f"agent compile done | success={success}")

        # latexmk/texfot 的摘要可能只保留外层 main.tex 的错误行，而真正的
        # `!` 错误和输入栈位于完整的 .log 中。无论成功还是失败都读取主日志：
        # 成功时用于缺字/未解析引用质量门禁，失败时用于给 Agent 正确的
        # .cls/.sty/.tex 文件上下文，避免它根据错误的外层行号盲修。
        log_text = await asyncio.to_thread(_read_log_text, resolved.with_suffix(".log"))
        diagnostic_log = f"{log_text}\n{full_log}" if log_text else full_log
        # latexmk 的标准输出可能包含同一次构建中早期 TeX pass 的警告，
        # 而主 .log 在 latexmk 完成后代表最后一次 pass。质量门禁（缺字、
        # 未解析引用、版面警告）只能检查最后一次 pass；否则已经由 BibTeX
        # / 后续 pass 修复的首轮 warning 会被错误地再次升级成失败。
        quality_log = log_text or full_log
        errors = _extract_errors(
            diagnostic_log,
            project_root=self._sandbox,
            fallback_file=Path(main_file).as_posix(),
        )
        quality_errors = [
            error
            for error in _extract_errors(
                quality_log,
                project_root=self._sandbox,
                fallback_file=Path(main_file).as_posix(),
            )
            if str(error.get("type", "")).startswith("Missing character")
        ]
        quality_errors.extend(
            _extract_unresolved_reference_errors(
                quality_log,
                fallback_file=Path(main_file).as_posix(),
            )
        )
        quality_warnings = _extract_quality_warnings(quality_log)
        build_success = success and not quality_errors

        if build_success:
            # 保留成功状态，但把可修复的版面警告明确交给模型；它们不是
            # 编译失败，不应让 Agent 为了消除每个 overfull box 无限重写。
            # 成功时不要把 latexmk stdout 中早期 pass 的原始 warning 再次
            # 回传给模型；质量 warning 已从最后一次主 .log 重新提取，成功
            # 摘要只需保留这一份权威结果。
            llm_text = _format_quality_feedback(
                quality_warnings, "Compilation succeeded."
            )
            summary = "Compilation succeeded"
        else:
            if success:
                # 质量门禁失败时仍使用统一的结构化错误回路；此时 PDF 可能
                # 已生成，但不能让 finish 把缺字/断引用伪装成合格产物。
                errors = quality_errors
            log_file = resolved.with_suffix(".log")
            log_note = (
                f"[note: complete {log_file.name} was included when extracting "
                "the structured errors and source context]"
                if log_text
                else ""
            )
            # latexmk 的 stdout/stderr 可能不含 `!` 错误行（错误只写进了
            # <main>.log）。此时直接从沙箱读取日志文件兜底提取，避免
            # Agent 因"看不到错误"而盲目重试。
            if not errors:
                log_text = await asyncio.to_thread(_read_log_text, log_file)
                if log_text:
                    log_errors = _extract_errors(
                        log_text,
                        project_root=self._sandbox,
                        fallback_file=Path(main_file).as_posix(),
                    )
                    if log_errors:
                        errors = log_errors
                        log_note = (
                            f"[note: no error lines in latexmk output; errors "
                            f"below were extracted from {log_file.name}]"
                        )
                    else:
                        log_note = (
                            f"[note: no '!' error lines in latexmk output or "
                            f"{log_file.name}; a tail of {log_file.name} is "
                            f"included below]"
                        )
                        log_tail = (
                            log_text[-_LOG_TRUNCATE:]
                            if len(log_text) > _LOG_TRUNCATE
                            else log_text
                        )
                        trimmed = (
                            trimmed + f"\n\n--- {log_file.name} (tail) ---\n" + log_tail
                        )
                    # 参考文献失败时真实错误在 .blg 里（如 .bst 不存在、
                    # bib 条目缺字段），同样兜底追加
                    blg_file = resolved.with_suffix(".blg")
                    blg_text = await asyncio.to_thread(_read_log_text, blg_file)
                    if blg_text and (
                        "bibtex" in log_text.lower()
                        or "bbl" in log_text.lower()
                        or "blg" in log_text.lower()
                    ):
                        log_note = (
                            log_note + " [bibtex note: if the .blg tail shows "
                            "'couldn't open style file', the "
                            "\\bibliographystyle name does not exist — use "
                            "an installed style (e.g. gbt7714-numerical / "
                            "gbt7714-2005-numerical) or a standard one; if "
                            "it shows 'missing field for entry', fix that "
                            "entry's type or add the missing field (e.g. "
                            "@inproceedings needs booktitle).]"
                        )
                        blg_tail = (
                            blg_text[-_LOG_TRUNCATE:]
                            if len(blg_text) > _LOG_TRUNCATE
                            else blg_text
                        )
                        trimmed = (
                            trimmed + f"\n\n--- {blg_file.name} (tail) ---\n" + blg_tail
                        )
            errors = _attach_source_context(
                errors,
                self._sandbox,
                fallback_file=Path(main_file).as_posix(),
            )
            analysis = _format_error_analysis(errors)
            if log_note:
                analysis = log_note + "\n\n" + analysis
            llm_text = analysis + "\n\n--- Raw log (trimmed) ---\n" + trimmed
            error_count = len(errors)
            summary = (
                f"Compilation failed or quality gate rejected the build "
                f"({error_count} error(s)); analyze the error summary, "
                f"use read_file to inspect the relevant files (main.log is readable), "
                f"then use write_file to fix and compile again"
            )

        event = {
            "tool": "compile_latex",
            "ok": True,
            "success": build_success,
            "log": trimmed,
            "summary": summary,
        }
        if quality_warnings:
            event["quality_warnings"] = quality_warnings
        if not build_success:
            self._last_failed_source_hash = source_hash
            self._last_success_source_hash = None
            event["errors"] = errors
        else:
            self._last_failed_source_hash = None
            self._last_success_source_hash = source_hash
        return llm_text, event

    # ── finish ────────────────────────────────────────────────────────

    def _finish(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = args.get("summary", "")
        main_file = args.get("main_file", "")
        files = self.list_files()
        # 若 Agent 在最后一次编译后又改过源文件（源码哈希既不等于上次成功
        # 编译、也不等于上次失败编译），当前 PDF 与磁盘源码不一致，不允许
        # 直接 finish。必须再编译一次验证最新源码。
        current_hash = self._source_hash()
        if (
            self._last_success_source_hash is not None
            or self._last_failed_source_hash is not None
        ) and current_hash not in (
            self._last_success_source_hash,
            self._last_failed_source_hash,
        ):
            msg = (
                "Finish rejected: the project source has changed since the "
                "last compile_latex call. Call compile_latex first to verify "
                "the current files. If compilation fails, fix the reported "
                "root cause, then call finish only after a successful compile "
                "of the latest source."
            )
            return msg, {
                "tool": "finish",
                "ok": False,
                "summary": "Finish rejected: source changed since last compile.",
                "main_file": main_file,
            }
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

    def _source_hash(self) -> str:
        """计算当前项目源码快照，用于阻止未改源码的重复编译。"""
        import hashlib

        digest = hashlib.sha256()
        root = self._sandbox.resolve()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _LATEX_SOURCE_SUFFIXES:
                continue
            try:
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
            except OSError:
                continue
        return digest.hexdigest()

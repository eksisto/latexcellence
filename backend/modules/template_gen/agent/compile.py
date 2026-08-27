"""模板生成 Agent 编译日志解析与错误分析。"""

import re
from pathlib import Path
from typing import Any

from .constants import _LOG_TRUNCATE


def _read_log_text(log_path: Path, max_chars: int = 262144) -> str:
    """读取编译日志文件文本（供 _compile_latex 兜底解析），失败返回空串。"""
    try:
        if not log_path.exists() or not log_path.is_file():
            return ""
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= max_chars else text[:max_chars]
    except OSError:
        return ""


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
# `(chapters/intro.tex`，也覆盖 `(./thesis.cls` / `(./theme.sty`。
# 用于追踪错误发生在哪个源文件中（错误常出现在 .cls/.sty 的宏定义里）。
_FILE_CTX_RE = re.compile(r"\(\s*((?:\.{1,2}/)?[\w./\-]+\.(?:tex|cls|sty))\b")
# 匹配行号提示，形如 `l.123 ...`（不带转义因为 LaTeX 错误日志是纯文本）
_LINE_INFO_RE = re.compile(r"l\.(\d+)\s?(.*)")


def _normalize_log_file(
    raw_file: str, project_root: Path | None
) -> tuple[str | None, str | None]:
    """把日志文件上下文限制在项目沙箱内，避免误导 Agent 修改 TeX 安装文件。"""
    raw = raw_file.strip()
    if project_root is None:
        return raw[2:] if raw.startswith("./") else raw, None

    root = project_root.resolve()
    raw_path = Path(raw)
    if raw_path.is_absolute():
        try:
            return raw_path.resolve().relative_to(root).as_posix(), None
        except ValueError:
            return None, raw

    relative = raw[2:] if raw.startswith("./") else raw
    candidate = (root / relative).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate.relative_to(root).as_posix(), None
    return None, raw


def _extract_errors(
    log: str,
    *,
    project_root: Path | None = None,
    fallback_file: str | None = None,
) -> list[dict[str, Any]]:
    """从 latexmk/xelatex 日志中提取结构化错误信息。

    识别以 `!` 开头的错误声明行，并：
      - 回溯最近的 `(xxx.tex` 上下文记录错误所在文件
      - 向后扫 15 行寻找 `l.NNN ...` 行号提示

    传入 project_root 时，TeX 发行版内部文件会被标记为
    ``external_file``，不会伪装成项目文件。若错误只显示外部展开栈，使用
    fallback_file 作为需要 Agent 检查的项目入口。

    LaTeX 错误会级联（第一个错误往往引来一系列后续错误），所以只
    返回前若干个错误就足以让 LLM 聚焦根因。

    另外识别"缺字"类诊断（如中文内容未配 CJK 字体时 xelatex 报
    `Missing character: There is no ...`）：这类问题不产生 `!` 行，
    但会导致 latexmk 退出码非 0 且 PDF 缺字。缺字诊断会逐字符刷屏，
    同一段文本可能产生成百上千条，因此只保留第一条并合并计数。
    """
    errors: list[dict[str, Any]] = []
    lines = log.splitlines()
    last_file: str | None = None
    last_external_file: str | None = None
    # 只保留第一条缺字诊断（记录首条所在文件/字体），后续只累计数量
    missing_char: dict[str, Any] | None = None
    missing_char_count = 0

    for i, line in enumerate(lines):
        # 更新当前文件上下文（粗略追踪，不处理栈深度，仅用最近出现的）
        for m in _FILE_CTX_RE.finditer(line):
            normalized, external = _normalize_log_file(m.group(1), project_root)
            if normalized:
                last_file = normalized
                last_external_file = None
            elif external:
                last_external_file = external

        # 缺字诊断：`Missing character: There is no X (U+XXXX) in font Y`
        if "Missing character" in line or "missing character" in line:
            missing_char_count += 1
            if missing_char is None:
                # 尝试提取字体名（in font <字体>，可能含空格，字体参数以 / 或 ; 分隔）
                font = None
                m_font = re.search(r"in font ([^/;!]+)", line)
                if m_font:
                    font = m_font.group(1).strip()
                missing_char = {
                    "type": "Missing character (CJK font not configured?)",
                    "file": last_file,
                    "line": None,
                    "detail": line.strip()[:200],
                }
                if last_external_file:
                    missing_char["external_file"] = last_external_file
                if font:
                    missing_char["font"] = font
                # 按日志顺序插入首条缺字诊断，后续刷屏行不再产生新条目
                errors.append(missing_char)
            continue

        if not line.startswith("!"):
            continue

        err: dict[str, Any] = {
            "type": line[1:].strip(),
            "file": last_file,
            "line": None,
            "detail": "",
        }
        if last_external_file:
            err["external_file"] = last_external_file
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

    # 合并计数：多条缺字诊断合并为一条，避免挤占错误列表上限
    if missing_char is not None and missing_char_count > 1:
        missing_char["detail"] = (
            f"{missing_char['detail']} "
            f"(+{missing_char_count - 1} more missing-character lines)"
        )

    # 限制最多 8 个错误，避免极端情况日志回传过长
    limited = errors[:8]
    if project_root and fallback_file:
        for error in limited:
            if error.get("external_file") or error.get("file") is None:
                error["file"] = fallback_file
    return limited


def _extract_unresolved_reference_errors(
    log: str, *, fallback_file: str
) -> list[dict[str, Any]]:
    """把成功构建但仍未解析的交叉引用/文献引用升级为质量错误。

    latexmk 在某些情况下会返回 0 并生成 PDF，即使日志仍包含 undefined
    reference/citation。对模板交付而言，这与编译失败同样不可接受；这里仅
    提取明确的 unresolved 诊断，不把普通 rerun 提示误判为错误。
    """
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = log.splitlines()
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        is_unresolved = (
            "undefined references" in lowered
            or "undefined citations" in lowered
            or ("reference `" in lowered and "undefined" in lowered)
            or ("citation `" in lowered and "undefined" in lowered)
        )
        if not is_unresolved:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        errors.append(
            {
                "type": "Unresolved cross-reference or citation.",
                "file": fallback_file,
                "line": None,
                "detail": stripped[:240],
            }
        )
        if len(errors) >= 8:
            break
    return errors


def _extract_quality_warnings(log: str, *, limit: int = 12) -> list[str]:
    """提取不阻断 PDF 生成、但值得 Agent 在交付前处理的排版警告。"""
    warnings: list[str] = []
    seen: set[str] = set()
    patterns = (
        "overfull \\hbox",
        "underfull \\hbox",
        "font warning",
        "label(s) may have changed",
        "rerun to get cross-references",
    )
    for line in log.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped or not any(pattern in lowered for pattern in patterns):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        warnings.append(stripped[:240])
        if len(warnings) >= limit:
            break
    return warnings


def _format_quality_feedback(warnings: list[str], trimmed_log: str) -> str:
    """把非致命质量警告放在成功编译结果的最前面。"""
    if not warnings:
        return trimmed_log
    lines = [
        "Compilation succeeded, but review these non-fatal quality warnings "
        "before finish:",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append(trimmed_log)
    return "\n".join(lines)


def _attach_source_context(
    errors: list[dict[str, Any]],
    project_root: Path,
    *,
    fallback_file: str,
    radius: int = 3,
) -> list[dict[str, Any]]:
    """为结构化错误附加有限行号上下文，减少 LLM 盲读整份大文件。"""
    root = project_root.resolve()
    for error in errors:
        file_name = error.get("file") or fallback_file
        line_number = error.get("line")
        if error.get("external_file"):
            error["scope_note"] = (
                "The log stack entered an external TeX distribution file; "
                "do not edit that file. Inspect the project source instead."
            )
            file_name = fallback_file
        if not isinstance(line_number, int) or line_number < 1:
            continue
        try:
            source_path = (root / str(file_name)).resolve()
            if not source_path.is_relative_to(root) or not source_path.is_file():
                continue
            source_lines = source_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            continue
        start = max(1, line_number - radius)
        end = min(len(source_lines), line_number + radius)
        error["source_context"] = "\n".join(
            f"{index}: {source_lines[index - 1]}" for index in range(start, end + 1)
        )
    return errors


def _error_hints(errors: list[dict[str, Any]]) -> list[str]:
    """根据已知的通用 LaTeX 失败签名生成针对性修复提示（与具体模板无关）。"""
    hints: list[str] = []
    for e in errors:
        combined = f"{e.get('type', '')} {e.get('detail', '')}".lower()
        if "undefined control sequence" in combined:
            hint = (
                "Public API check: determine whether the undefined command is "
                "project-defined or package-provided. Read the loaded .cls/.sty "
                "and all body callers, then define one stable public command with "
                "the exact argument count or replace the caller; do not create a "
                "second accessor with a similar name. If the undefined command "
                "appears while expanding a public setter, check whether that "
                "setter performs `\\def`/`\\gdef` of a name containing `@` — "
                "in a body file `@` is not a letter, so store the value in a "
                "public accessor directly or wrap the assignment in "
                "\\makeatletter ... \\makeatother. If it is package-provided, "
                "load that package before the first use."
            )
            if hint not in hints:
                hints.append(hint)
        if "illegal unit of measure" in combined or "missing number" in combined:
            hint = (
                "Dimension parsing issue: inspect custom macros and key-value "
                "options in the project source for a width, height, skip, or "
                "ratio that lacks an explicit unit or expands to text. If the "
                "reported line is `\\begin{document}` or a third-party package, "
                "the root cause is usually an earlier project definition; do "
                "not remove packages merely because the stack trace entered one. "
                "Simplify the custom dimension macro and use a literal dimension "
                "with a unit first."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing = inserted for \\ifnum" in combined or (
            "ifnum" in combined and "missing =" in combined
        ):
            hint = (
                "Template/class arithmetic issue: inspect the project .sty/.cls "
                "loaded at the reported `\\usetheme` or `\\documentclass` line, "
                "not the theme name itself. A TeX `\\ifnum` must compare two "
                "integer counts with `<`, `=`, or `>`; it cannot compare a "
                "decimal or an expanded dimension. Also check that every "
                "`\\ProvidesClass`/`\\ProvidesPackage` optional argument starts "
                "with a YYYY/MM/DD date (e.g. [2024/01/01 Template]); an "
                "incomplete date such as `2024/01` makes the next word be "
                "parsed as an integer. And `\\setbeamertemplate{blocks}` accepts "
                "a predefined template name such as `[default]` or `[rounded]`, "
                "not a key-value option like `[rounded=false]`. Remove unsafe "
                "custom template arithmetic and use a simple literal `\\rule` "
                "progress bar until the base theme compiles."
            )
            if hint not in hints:
                hints.append(hint)
        if "tex capacity exceeded" in combined or "input stack size" in combined:
            hint = (
                "TeX capacity / recursion issue: the document is likely "
                "redefining a beamer template or environment in a recursive "
                "way (for example setting the same `\\setbeamertemplate` "
                "twice, or an item template that calls itself). Inspect every "
                "custom template definition in the .sty files, remove "
                "duplicate/recursive `\\setbeamertemplate` calls, and keep "
                "one built-in template name per element until the base "
                "compiles."
            )
            if hint not in hints:
                hints.append(hint)
        if "iffontexiststf" in combined:
            hint = (
                "Font feature issue: `\\IfFontExistsTF` is provided by "
                "fontspec, so it must not be used before fontspec is loaded. "
                "Use it only for an explicitly requested Latin font; for CJK "
                "text, load ctex/xeCJK and rely on its default fontset instead."
            )
            if hint not in hints:
                hints.append(hint)
        if "spacefactor" in combined:
            hint = (
                "Metadata trap: this error is caused by a `\\@...` internal "
                "macro (or a bare `\\@`) typeset in a body .tex file, where "
                "`@` is not a letter. Define PUBLIC metadata commands in the "
                ".cls/.sty (e.g. `\\thesistitlevalue` accessors) or a "
                "`\\maketitlepage` command, and use only those public "
                "commands in body files."
            )
            if hint not in hints:
                hints.append(hint)
        if (
            "cannot be found" in combined
            or "fontspec" in combined
            or ("font" in combined and "missing" in combined)
        ):
            hint = (
                "Font issue: the sandbox only provides the fonts in the "
                "planning guidance's FONT WHITELIST — Windows/Office fonts "
                "are NOT available. Map the requested font onto that "
                "whitelist; do NOT manually set CJK fonts (\\setCJKmainfont / "
                "\\setCJKsansfont / \\setCJKfamilyfont) or probe fonts with "
                "\\IfFontExistsTF on CJK family names (unreliable for CJK). "
                "Delete the explicit CJK font commands and let ctex's "
                "automatic platform fontset pick a working stack; for Latin "
                "fonts fall back to a whitelist font, Latin Modern, or the "
                "class default. Document any substitution in the README."
            )
            if hint not in hints:
                hints.append(hint)
        if "already defined" in combined:
            hint = (
                "Command collision: the command already exists in the loaded "
                "class/package. Use \\renewcommand when the base definition "
                "must change, or define a NEW name; never \\newcommand over "
                "an existing command. For theorem/abstract/list infrastructure, "
                "prefer the inherited environment or a project-specific name "
                "instead of replacing the class interface. If the error names "
                "a counter (\\c@...), do not keep guessing counter names: define "
                "one project-prefixed counter exactly once in the style file, "
                "make all related environments share it, and remove duplicate "
                "definitions from the body and other style files. Read the "
                "current style and all callers before changing its public API."
            )
            if hint not in hints:
                hints.append(hint)
        if "unresolved cross-reference" in combined or (
            "undefined" in combined
            and ("reference" in combined or "citation" in combined)
        ):
            hint = (
                "Reference integrity issue: every `\\ref`/`\\eqref`/citation "
                "must point to a label or BibTeX key that exists in the project. "
                "Read the cited source and references.bib, then either correct "
                "the key or remove the unused reference and recompile until the "
                "undefined-reference warning is gone."
            )
            if hint not in hints:
                hints.append(hint)
        if "macro parameter character" in combined:
            hint = (
                "A literal `#` appears in text or in a command argument. In "
                "LaTeX text, write `\\#` for the hash symbol; for HTML/RGB "
                "colors, write `\\definecolor{...}{HTML}{1F4E79}` WITHOUT "
                "the `#` prefix. Never place a bare `#` outside a macro "
                "definition."
            )
            if hint not in hints:
                hints.append(hint)
        if "capacity exceeded" in combined or "input stack size" in combined:
            hint = (
                "Infinite recursion or runaway expansion: a macro is calling "
                "itself (e.g. a command redefines its own name, or "
                "\\labelenumi/\\theenumi is redefined circularly). Remove or "
                "simplify the recursive redefinition; if a custom theme "
                "redefines list counters, delete those lines and let the "
                "class/beamer defaults handle them."
            )
            if hint not in hints:
                hints.append(hint)
        if "a node must have" in combined:
            hint = (
                "Every tikz \\node needs a (possibly empty) label text: "
                "`\\node[options] (name) {text};` — the braces are "
                "mandatory, e.g. `\\node[fill=blue] (box) {};`."
            )
            if hint not in hints:
                hints.append(hint)
        if "no counter" in combined:
            hint = (
                "The referenced counter is not defined (or not yet defined "
                "at that point). Use counters provided by the class "
                "(e.g. beamer defines 'frame'/'framenumber'; LaTeX defines "
                "'figure'/'table'/'equation'), or define your own with "
                "\\newcounter before using it. Do not \\newcounter over "
                "names the class already provides."
            )
            if hint not in hints:
                hints.append(hint)
        if "not found" in combined and "file" in combined:
            hint = (
                "A file/package/theme is missing. If it is a file YOU "
                "intended to provide (e.g. a custom .sty or "
                "beamerthemeX.sty), create it with write_file; if it is a "
                "third-party package, use a standard equivalent already "
                "available in TeX distributions or remove the "
                "\\usepackage/\\usetheme line."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing \\begin{document}" in combined:
            hint = (
                "In a .cls/.sty file, `@` is already a letter, so a stray "
                "`\\makeatother` turns every later `\\@...` command name into "
                "`\\@` + letters, and the letters get typeset in the "
                "preamble — which raises 'Missing \\begin{document}'. Remove "
                "`\\makeatother` from class/style files (`\\makeatletter` is "
                "unnecessary there). Also verify no body text appears in "
                "main.tex before \\begin{document}."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing \\endcsname" in combined:
            hint = (
                "Missing \\endcsname: a command used inside a key-value or "
                "\\csname context is undefined or malformed. Chinese size "
                "names like `\\wuhao` are NOT commands — use `\\zihao{5}` "
                "(or `font=small` in \\captionsetup). Check every control "
                "sequence in the reported line and the key it belongs to."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing number, treated as zero" in combined and (
            "begin{document}" in combined or "spacefactor" in combined
        ):
            hint = (
                "Early document-start number/spacefactor error: inspect the "
                "complete preamble and the first class/style expansion instead "
                "of patching the body. Remove explicit CJK font selection and "
                "font-probing commands, let ctex/xeCJK use its automatic fontset, "
                "and replace malformed custom size expressions with standard "
                "LaTeX size commands. Read the reported source and log before "
                "changing the public metadata API."
            )
            if hint not in hints:
                hints.append(hint)
        if "extra alignment tab" in combined:
            hint = (
                "A tabular row has more `&` separators than the column spec "
                "allows, OR a `p{}`-cell declaration like \\centering/"
                "\\raggedright swallowed the row break (\\centering "
                "redefines `\\\\` inside the cell). Fix: end rows with "
                "\\tabularnewline when a cell uses \\centering, or use "
                "`>{\\centering\\arraybackslash}p{...}` column specs; also "
                "count `&` against the column specification."
            )
            if hint not in hints:
                hints.append(hint)
        if "there's no line here to end" in combined:
            hint = (
                "A `There's no line here to end` error at l.1 of the "
                "bibliography usually means the .bbl starts with doubled "
                "backslashes (`\\\\begin{thebibliography}`). This is caused "
                "by writing `.bst` string literals with two backslashes "
                '(`"\\\\begin..."`) instead of one (`"\\begin..."`). '
                "Edit the .bst file so every LaTeX command in its string "
                "literals uses a single backslash, then recompile from a "
                "clean state so BibTeX regenerates the .bbl. If the error is "
                "not in a .bbl, remove a stray `\\\\` at the beginning of a "
                "line in the reported file."
            )
            if hint not in hints:
                hints.append(hint)
        if "environment" in combined and "undefined" in combined:
            hint = (
                "Environment undefined: the environment's defining package "
                "is not loaded (e.g. `algorithm` needs \\usepackage{algorithm}"
                "+\\usepackage{algorithmic} or algorithm2e; `tikzpicture` "
                "needs \\usepackage{tikz}), or the name is misspelled. Add "
                "the package or fix the name."
            )
            if hint not in hints:
                hints.append(hint)
        if "undefined color" in combined or (
            "xcolor" in combined and "undefined" in combined
        ):
            hint = (
                "Color mismatch: a color name used in the document or theme "
                "is not defined. Define every custom color with "
                "\\definecolor{<name>}{<model>}{<spec>} in the color theme "
                "file BEFORE its first use, or use a color name the loaded "
                "class/theme already provides. Check spelling and case: "
                "color names are case-sensitive, and a theme does not "
                "magically provide a color unless you define it."
            )
            if hint not in hints:
                hints.append(hint)
        if "bibliography not compatible" in combined or (
            "natbib" in combined and "bbl" in combined
        ):
            hint = (
                "Bibliography style mismatch: natbib's citation mode and the "
                ".bbl format disagree (author-year vs numeric). latexmk -gg "
                "already regenerates .bbl from the current .bst, so do NOT "
                "try to delete or hand-edit .bbl. Align the two sides in "
                "source: either load natbib in numeric mode (e.g. [numbers] "
                "or [sort&compress]) with a numeric .bst (plain / unsrt / "
                "gbt7714-numerical), or switch to author-year mode with an "
                "author-year .bst (plainnat / gbt7714-author-year). Pick ONE "
                "mode and make \\bibliographystyle, natbib options and "
                "citation commands consistent."
            )
            if hint not in hints:
                hints.append(hint)
    return hints


def _format_error_analysis(errors: list[dict[str, Any]]) -> str:
    """把结构化错误列表格式化为给 LLM 看的聚焦错误摘要。

    强调"修第一个错误"——LaTeX 错误级联是常态，后续错误通常是
    第一个的连锁反应。
    """
    if not errors:
        return (
            "Compilation FAILED. No '! ' error lines were detected in the "
            "build output or the generated .log file. Read the full log "
            "with read_file (e.g. 'read_file main.log') and inspect it for "
            "missing files, font issues, or other diagnostics, then fix and "
            "recompile."
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
        if e.get("scope_note"):
            parts.append(f"  scope: {e['scope_note']}")
        if e.get("source_context"):
            parts.append("  source excerpt:")
            parts.extend(f"    {line}" for line in e["source_context"].splitlines())
        if e.get("font"):
            parts.append(f"  font: {e['font']}")
        if e["type"].startswith("Missing character"):
            parts.append(
                "  hint: Chinese/CJK text is typeset in a font that lacks "
                "those glyphs — load xeCJK/ctex and use its default CJK "
                "fontset for CJK text; do not manually set CJK fonts."
            )
        parts.append("")
    hints = _error_hints(errors)
    if hints:
        parts.append("HINTS (apply before the next compile):")
        parts.extend(f"- {h}" for h in hints)
        parts.append("")
    return "\n".join(parts)

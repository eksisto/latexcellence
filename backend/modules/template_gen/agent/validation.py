"""模板生成 Agent 写盘静态校验。"""

import re
from pathlib import Path

from backend.utils.latex_literals import blank_literal_spans

from .constants import (
    _BODY_STYLE_COMMAND_RE,
    _CJK_DEPENDENCY_RE,
    _CJK_PACKAGE_RE,
    _CJK_RE,
    _LATEX_SOURCE_SUFFIXES,
    _PRIVATE_BODY_COMMAND_RE,
    _PROVIDES_STATEMENT_RE,
    _SETTER_ACCESSOR_RE,
    _VALID_PROVIDES_DATE_RE,
)


def _strip_latex_comments(text: str) -> list[str]:
    """返回去掉未转义注释后的行，保留行号以便给 Agent 可操作反馈。"""
    result: list[str] = []
    for line in text.splitlines():
        comment_at: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        result.append(line if comment_at is None else line[:comment_at])
    return result


def _check_public_body_commands(content: str) -> str | None:
    """拒绝正文使用类/样式内部的 `@` 控制序列。"""
    for line_number, line in enumerate(_strip_latex_comments(content), 1):
        match = _PRIVATE_BODY_COMMAND_RE.search(line)
        if match:
            return (
                f"LaTeX body uses private control sequence "
                f"'{match.group(0)}' at line {line_number}. Commands containing "
                "@ are implementation details for .cls/.sty files; define a "
                "public accessor or public rendering command in the class/style "
                "and use that command from the .tex file."
            )
    return None


def _check_provides_date(content: str) -> str | None:
    """校验 .cls/.sty 的 \\ProvidesClass/\\ProvidesPackage 日期参数。"""
    match = _PROVIDES_STATEMENT_RE.search(content)
    if not match:
        return None
    optional = match.group(1)
    if _VALID_PROVIDES_DATE_RE.match(optional):
        return None
    return (
        "The optional argument of \\ProvidesClass/\\ProvidesPackage must "
        "start with a YYYY/MM/DD date (e.g. [2024/01/01 Template]); the "
        f"current value '{optional.strip()}' makes LaTeX parse the following "
        "text as an integer and fail with 'Missing = inserted for \\ifnum'. "
        "Use a full date such as [2024/01/01] or omit the optional argument."
    )


def _check_bst_output_escapes(content: str) -> str | None:
    """拒绝 .bst 字符串里把 LaTeX 命令写成双反斜杠的错误。

    BibTeX 风格文件中一个反斜杠会被原样写入 .bbl；如果写成两个反斜杠
    （``"\\\\begin{thebibliography}"``），生成的 .bbl 会是
    ``\\\\begin{thebibliography}``，LaTeX 在 l.1 报
    ``There's no line here to end``。这是 JSON 转义记忆导致的典型错误，
    应在写盘阶段直接拦下。
    """
    for bad in (
        "\\\\begin{thebibliography}",
        "\\\\bibitem{",
        "\\\\end{thebibliography}",
    ):
        if bad in content:
            return (
                "BibTeX .bst string literals must use ONE backslash for LaTeX "
                'commands (e.g. "\\begin{thebibliography}{99}" and '
                '"\\bibitem{"), not two. A doubled backslash in a .bst '
                "string is written literally into the .bbl and breaks "
                "LaTeX with 'There's no line here to end'. Fix every "
                "LaTeX command in the .bst string literals to use a single "
                "backslash."
            )
    return None


def _check_body_style_commands(content: str) -> str | None:
    """正文 .tex 不允许定义/重定义样式命令（格式应封装在 .cls/.sty）。"""
    for line_number, line in enumerate(_strip_latex_comments(content), 1):
        match = _BODY_STYLE_COMMAND_RE.search(line)
        if match:
            return (
                f"Body file defines or redefines the style command "
                f"'{match.group(0)}' at line {line_number}. All styling must "
                "live in the .cls/.sty file; body files should only call "
                "semantic commands. Move this definition into the class/style "
                "and call a public command from the body."
            )
    return None


def _cjk_dependency_candidates(
    name: str, command: str, current_dir: Path, root: Path
) -> list[Path]:
    r"""把 `\usepackage` / `\usetheme` / `\input` 等引用解析成本地候选文件。"""
    name = name.strip()
    if not name or name.startswith(("/", "\\")):
        return []

    relative = Path(name)
    bases = [current_dir / relative, root / relative]
    candidates: list[Path] = []

    if command in {"input", "include", "subfile"}:
        for base in bases:
            if base.suffix.lower() in _LATEX_SOURCE_SUFFIXES:
                candidates.append(base)
            else:
                candidates.append(base.with_suffix(".tex"))
    elif command in {"usepackage", "RequirePackage"}:
        for base in bases:
            candidates.append(Path(str(base) + ".sty"))
            candidates.append(Path(str(base) + ".cls"))
    elif command == "usetheme":
        for base in bases:
            candidates.append(base.parent / f"beamertheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    elif command == "usecolortheme":
        for base in bases:
            candidates.append(base.parent / f"beamercolortheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    elif command == "usefonttheme":
        for base in bases:
            candidates.append(base.parent / f"beamerfonttheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    else:  # LoadClass
        for base in bases:
            candidates.append(Path(str(base) + ".cls"))

    return candidates


def _local_dependencies_provide_cjk(
    content: str, project_root: Path, current_path: Path
) -> bool:
    """递归检查当前文件引用的本地依赖是否实际加载 ctex/xeCJK。"""
    root = project_root.resolve()
    current_dir = current_path.resolve().parent
    queue: list[Path] = []
    code = "\n".join(_strip_latex_comments(blank_literal_spans(content)))
    for match in _CJK_DEPENDENCY_RE.finditer(code):
        for raw_name in match.group("name").split(","):
            queue.extend(
                _cjk_dependency_candidates(
                    raw_name, match.group("cmd"), current_dir, root
                )
            )

    seen: set[Path] = set()
    while queue:
        candidate = queue.pop(0)
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if (
            candidate in seen
            or not candidate.is_file()
            or not candidate.is_relative_to(root)
        ):
            continue
        seen.add(candidate)
        try:
            file_text = candidate.read_text(encoding="utf-8", errors="replace")
            dep_code = "\n".join(_strip_latex_comments(blank_literal_spans(file_text)))
        except OSError:
            continue
        if _CJK_PACKAGE_RE.search(dep_code):
            return True
        for match in _CJK_DEPENDENCY_RE.finditer(dep_code):
            for raw_name in match.group("name").split(","):
                queue.extend(
                    _cjk_dependency_candidates(
                        raw_name,
                        match.group("cmd"),
                        candidate.parent,
                        root,
                    )
                )
    return False


def _check_cjk_support(
    content: str,
    *,
    project_root: Path | None = None,
    current_path: Path | None = None,
) -> str | None:
    r"""beamer 主文件含中文但未加载 ctex/xeCJK 时拒绝，避免字形静默缺失。

    普通 article/custom class 的 CJK 支持可能由本地 .sty/.cls 提供，不能
    对每个分章文件做全文件检查，否则会误伤合法工程。检查只针对真正会被
    typeset 的内容：先剥离 verbatim/URL 与注释，避免中文注释或字面量误报；
    若正文引用了本地样式/主题/输入文件，则递归检查这些文件是否确实加载
    ctex/xeCJK；不能因为存在任意 `\usepackage` 就默认 CJK 已有人负责。
    """
    code = "\n".join(_strip_latex_comments(blank_literal_spans(content)))
    if not _CJK_RE.search(code):
        return None
    if _CJK_PACKAGE_RE.search(code):
        return None
    if not re.search(r"\\documentclass(?:\[[^\]]*\])?\s*\{[^}]*beamer", code):
        return None
    if project_root is not None and current_path is not None:
        if _local_dependencies_provide_cjk(content, project_root, current_path):
            return None
    return (
        "This file contains Chinese/CJK text but does not load ctex or "
        "xeCJK, so every CJK glyph will be missing from the PDF. Add "
        "\\usepackage{ctex} (or \\usepackage{xeCJK}) to the preamble/class "
        "before any Chinese content and rely on its default fontset."
    )


def _check_class_option_order(content: str) -> str | None:
    """\\DeclareOption/\\ExecuteOptions 必须出现在 \\ProcessOptions 之前。"""
    code = "\n".join(_strip_latex_comments(content))
    process_pos = code.find("\\ProcessOptions")
    if process_pos < 0:
        return None
    for keyword in ("\\DeclareOption", "\\ExecuteOptions"):
        pos = code.find(keyword)
        while pos >= 0:
            if pos > process_pos:
                return (
                    f"{keyword} appears after \\ProcessOptions in the class "
                    "options section. LaTeX only honors class options declared "
                    "before \\ProcessOptions; move every \\DeclareOption and "
                    "\\ExecuteOptions above it, then \\LoadClass, then packages."
                )
            pos = code.find(keyword, pos + 1)
    return None


def _check_setter_accessors(content: str) -> str | None:
    """公开 setter 的 \\renewcommand 目标必须已在类/样式中定义。"""
    code = "\n".join(_strip_latex_comments(content))
    for match in _SETTER_ACCESSOR_RE.finditer(code):
        setter_name, accessor_name = match.group(1), match.group(2)
        if re.search(rf"\\newcommand\{{\\{accessor_name}\}}", code) or re.search(
            rf"\\providecommand\{{\\{accessor_name}\}}", code
        ):
            continue
        return (
            f"Public setter \\{setter_name} uses "
            f"\\renewcommand{{\\{accessor_name}}}, but \\{accessor_name} is "
            "never defined in this file. Define a public accessor default "
            "with \\newcommand (for example \\newcommand{\\universityvalue}{}) "
            "before or after the setter, otherwise calling the setter from "
            "main.tex fails with 'Command undefined'."
        )
    return None

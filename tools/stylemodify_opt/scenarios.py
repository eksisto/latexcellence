# -*- coding: utf-8 -*-
"""style_modify Agent 优化闭环的评测场景。

每个场景是一个小型 LaTeX 项目 + 一条模拟用户口吻的样式需求。项目内埋入
必须保留的正文标记（label / 公式 / 表格数据），用于客观检测 Agent 是否
“只改样式、不破坏内容”。

场景定义与检查点全部可复现，运行器在评测前从定义重新生成项目。
"""

import re
from pathlib import Path
from typing import Any

S1_MAIN = r"""\documentclass{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{xcolor}
\usepackage{amsmath}
\usepackage{mystyle}

\begin{document}
\section{Introduction}
\label{sec:intro}
This is \themetext{an example sentence} that must stay unchanged.

\begin{equation}
\label{eq:energy}
E = mc^2
\end{equation}

\themetext{Emphasized conclusion.}
\end{document}
"""

S1_STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{mystyle}

\definecolor{themecolor}{HTML}{333333}
\newcommand{\themetext}[1]{{\color{themecolor}#1}}

\endinput
"""

S2_MAIN = r"""\documentclass{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{xcolor}
\usepackage{titlesec}
\usepackage{mystyle}

\begin{document}
\section{Introduction}
\label{sec:method}
The method section describes the experiment setup.

\section{Results}
\label{sec:results}
We report the measured values in Table~\ref{tab:results}.
\end{document}
"""

S2_STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{mystyle}

\definecolor{themecolor}{HTML}{222222}
\titleformat{\section}{\Large\bfseries\color{themecolor}}{\thesection}{1em}{}

\endinput
"""

S3_MAIN = r"""\documentclass{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{array}
\usepackage{xcolor}

\begin{document}
\section{Experiment Results}
\label{sec:experiment}

\begin{table}[ht]
\centering
\caption{Measured outcomes by group.}
\label{tab:results}
\begin{tabular}{ccc}
\hline
Group & Mean & p-value \\
\hline
Control & 1.23 & 0.01 \\
Low & 1.45 & 0.02 \\
High & 1.98 & 0.003 \\
\hline
\end{tabular}
\end{table}

The p-value for the High group is 0.003.
\end{document}
"""

S4_MAIN = r"""\documentclass{beamer}
\usetheme{mystyle}

\begin{document}
\begin{frame}{Overview}
\label{frame:overview}
This frame introduces the problem.
\end{frame}

\begin{frame}{Experiments}
\label{frame:experiments}
We compare accuracy across three models.
\end{frame}
\end{document}
"""

S4_STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{beamerthememystyle}

\mode<presentation>

\definecolor{accent}{RGB}{200,0,0}
\setbeamercolor{frametitle}{fg=accent}
\setbeamercolor{title}{fg=accent}

\mode<all>
\endinput
"""

S5_CLS = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{mythesis}

\LoadClass[11pt]{article}
\RequirePackage[margin=2.5cm]{geometry}
\RequirePackage{xcolor}
\definecolor{headingcolor}{HTML}{222222}

\endinput
"""

S5_MAIN = r"""\documentclass{mythesis}

\begin{document}
\section{Introduction}
\label{sec:thesis}
This is the main body of the thesis.
\end{document}
"""


SCENARIOS: dict[str, dict[str, Any]] = {
    "s1_theme_color": {
        "label": "主题色替换",
        "requirement": (
            "把全项目主题色从 #333333 换成学术深蓝 #1F4E79，正文强调文字"
            "和章节标题继续使用主题色。只改样式相关代码，不要删除或改写"
            "正文句子、公式和 label。"
        ),
        "files": {
            "main.tex": S1_MAIN,
            "mystyle.sty": S1_STY,
        },
        "allowed_targets": {"main.tex", "mystyle.sty"},
        "preserve_markers": [
            r"\label{sec:intro}",
            r"\label{eq:energy}",
            r"E = mc^2",
            r"an example sentence",
        ],
        "checks": [
            ("新主题色 #1F4E79 已写入", "any", [r"1F4E79"]),
            ("旧主题色 #333333 已移除", "none", [r"333333"]),
            ("样式文件被修改", "staged", [r"\.sty$"]),
            ("main.tex 未被整篇重写", "any", [r"\\label\{sec:intro\}"]),
        ],
    },
    "s2_section_format": {
        "label": "章节标题格式",
        "requirement": (
            "把章节标题改成深蓝 #1F4E79、加粗，并在标题前加一个深蓝色方块"
            "标记；正文内容、label 和引用全部保持原样。"
        ),
        "files": {
            "main.tex": S2_MAIN,
            "mystyle.sty": S2_STY,
        },
        "allowed_targets": {"main.tex", "mystyle.sty"},
        "preserve_markers": [
            r"\label{sec:method}",
            r"\label{sec:results}",
            r"\ref{tab:results}",
            r"the experiment setup",
        ],
        "checks": [
            ("深蓝 #1F4E79 已写入", "any", [r"1F4E79"]),
            ("标题格式化使用 titleformat", "any", [r"\\titleformat"]),
            ("标题前有方块装饰", "any", [r"\\rule|\\colorbox|\\fbox"]),
            ("样式文件被修改", "staged", [r"\.sty$"]),
            ("旧灰 #222222 已移除", "none", [r"222222"]),
        ],
    },
    "s3_table_style": {
        "label": "三线表样式",
        "requirement": (
            "把实验表格改成 booktabs 三线表：表头加粗、数值列左对齐，"
            "保留全部数据行、caption、label 和正文句子。"
        ),
        "files": {
            "main.tex": S3_MAIN,
        },
        "allowed_targets": {"main.tex"},
        "preserve_markers": [
            r"\label{tab:results}",
            r"\caption{Measured outcomes by group.}",
            r"1.23",
            r"1.98",
            r"The p-value for the High group is 0.003.",
        ],
        "checks": [
            ("使用 booktabs 宏包", "any", [r"\\usepackage\{booktabs\}"]),
            ("三线表命令", "any", [r"\\toprule", r"\\midrule", r"\\bottomrule"]),
            ("表头加粗", "any", [r"\\bfseries|\\textbf"]),
            ("数值列左对齐", "any", [r"\\begin\{tabular\}\{[^}]*l"]),
            ("旧横线已移除", "none", [r"\\hline"]),
        ],
    },
    "s4_beamer_color": {
        "label": "Beamer 主题色",
        "requirement": (
            "把 beamer 主题色改成深蓝 RGB(0,83,159)，强调色改成橙色 "
            "RGB(245,166,35)，保持 frame 版式和正文内容完全不变。"
        ),
        "files": {
            "main.tex": S4_MAIN,
            "beamerthememystyle.sty": S4_STY,
        },
        "allowed_targets": {"main.tex", "beamerthememystyle.sty"},
        "preserve_markers": [
            r"\label{frame:overview}",
            r"\label{frame:experiments}",
            r"This frame introduces the problem.",
        ],
        "checks": [
            ("深蓝 RGB(0,83,159) 已写入", "any", [r"0,\s*83,\s*159|00539F"]),
            ("橙色 RGB(245,166,35) 已写入", "any", [r"245,\s*166,\s*35|F5A623"]),
            ("旧红色 RGB(200,0,0) 已移除", "none", [r"200,\s*0,\s*0"]),
            ("主题文件被修改", "staged", [r"\.sty$"]),
        ],
    },
    "s5_class_options": {
        "label": "文档类选项",
        "requirement": (
            "把 mythesis 文档类的默认字号从 11pt 改成 12pt，纸张使用 A4，"
            "页边距改为 2cm；其他样式、正文和 label 保持不动。"
        ),
        "files": {
            "main.tex": S5_MAIN,
            "mythesis.cls": S5_CLS,
        },
        "allowed_targets": {"main.tex", "mythesis.cls"},
        "preserve_markers": [
            r"\label{sec:thesis}",
            r"This is the main body of the thesis.",
        ],
        "checks": [
            ("12pt 已设置", "any", [r"12pt"]),
            ("A4 纸张已设置", "any", [r"a4paper"]),
            ("边距改为 2cm", "any", [r"margin\s*=\s*2cm"]),
            ("文档类文件被修改", "staged", [r"\.cls$"]),
            ("旧 11pt 已移除", "none", [r"11pt"]),
            ("旧边距 2.5cm 已移除", "none", [r"margin\s*=\s*2\.5cm"]),
        ],
    },
}

# holdout 场景：调优期间默认不运行、只看回归结果。
# s5_class_options 涉及自定义 .cls 类选项，与"背模板答案"的过拟合风险最相关。
HOLDOUT_SCENARIOS: set[str] = {"s5_class_options"}


def build_scenario_projects(data_root: Path) -> dict[str, Path]:
    """从场景定义生成项目目录，返回 key -> 项目根。"""
    projects_root = data_root / "projects"
    if projects_root.exists():
        import shutil

        shutil.rmtree(projects_root)
    projects_root.mkdir(parents=True, exist_ok=True)
    roots: dict[str, Path] = {}
    for key, spec in SCENARIOS.items():
        root = projects_root / key
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in spec["files"].items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        roots[key] = root
    return roots


def check_requirements(
    key: str,
    proposal_files: list[dict[str, Any]],
    project_root: Path,
    overlay_root: Path,
) -> dict[str, Any]:
    """按场景检查点核验 proposal 内容（纯文本启发式）。"""
    spec = SCENARIOS[key]
    changed_paths = {f["path"] for f in proposal_files}
    texts: list[str] = []
    for p in project_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".tex", ".sty", ".cls", ".bib"):
            rel = p.relative_to(project_root).as_posix()
            overlay = overlay_root / rel
            source = overlay if overlay.exists() else p
            if source.exists():
                texts.append(source.read_text(encoding="utf-8", errors="replace"))
    combined = "\n".join(texts)

    results: dict[str, Any] = {}
    for label, mode, patterns in spec["checks"]:
        if mode == "staged":
            matched = any(
                re.search(p, path, re.IGNORECASE)
                for p in patterns
                for path in changed_paths
            )
        elif mode == "none":
            matched = not any(re.search(p, combined, re.IGNORECASE) for p in patterns)
        else:
            matched = any(re.search(p, combined, re.IGNORECASE) for p in patterns)
        results[label] = {"matched": matched, "mode": mode, "patterns": patterns}
    passed = sum(1 for r in results.values() if r["matched"])
    return {
        "passed": passed,
        "total": len(results),
        "ratio": passed / len(results) if results else 0.0,
        "detail": results,
    }


def check_allowed_targets(
    key: str,
    proposal_files: list[dict[str, Any]],
) -> dict[str, Any]:
    """确认只改动了允许的样式相关文件。"""
    spec = SCENARIOS[key]
    changed = {f["path"] for f in proposal_files}
    unexpected = sorted(changed - spec["allowed_targets"])
    return {
        "ok": not unexpected,
        "allowed": sorted(spec["allowed_targets"]),
        "changed": sorted(changed),
        "unexpected": unexpected,
    }


def check_preservation(
    key: str,
    project_root: Path,
    overlay_root: Path,
) -> dict[str, Any]:
    """检查每个保留标记在候选项目中仍然存在。"""
    spec = SCENARIOS[key]
    candidates: dict[str, str] = {}
    for p in project_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".tex", ".sty", ".cls", ".bib"):
            rel = p.relative_to(project_root).as_posix()
            overlay = overlay_root / rel
            source = overlay if overlay.exists() else p
            candidates[rel] = source.read_text(encoding="utf-8", errors="replace")

    results: dict[str, Any] = {}
    for marker in spec["preserve_markers"]:
        results[marker] = {
            "found": any(marker in text for text in candidates.values()),
            "in": [rel for rel, text in candidates.items() if marker in text],
        }
    passed = sum(1 for r in results.values() if r["found"])
    return {
        "passed": passed,
        "total": len(results),
        "ratio": passed / len(results) if results else 0.0,
        "detail": results,
    }

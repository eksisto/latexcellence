"""Git 差异高亮服务常量与配置。"""

import re
import tempfile
from pathlib import Path

WORKTREE_REF = "__WORKTREE__"
PDF_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "latexcellence_git_diff"
_MAX_KEPT_JOB_DIRS = 20

_INPUT_PATTERN = re.compile(
    r"\\(?P<cmd>input|include|subfile)\s*\{(?P<path>[^{}\n]+)\}"
)
# Git revision 白名单格式：完整/短提交哈希，或普通分支/标签名。
# 分支/标签名禁止 `..`、`@`、`~`、`^`、`:`、`\`、`//` 等会改变
# revision 解析语义或注入选项的字符（git 本身也禁止这些字符入名）。
_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REF_FORBIDDEN_SUBSTRINGS = ("..", "@", "~", "^", ":", "\\", "//", "/.", "./")
_BEGIN_DOCUMENT = r"\begin{document}"
_END_DOCUMENT = r"\end{document}"

# 浮动体环境：LaTeX 的 table/figure 等环境要求 outer par mode，
# 不能出现在 LXDiffAdded / LXDiffDeleted（framed 颜色框）内部，
# 否则编译会报 "Not in outer par mode"。
# 生成差异时这些环境整体不包裹高亮，只高亮其周围的普通文本。
_FLOAT_ENV_NAMES = frozenset(
    {
        "table",
        "table*",
        "figure",
        "figure*",
        "algorithm",
        "algorithm*",
        "wrapfigure",
        "wraptable",
        "sidewaystable",
        "sidewaysfigure",
    }
)

# 行内 \begin{...} / \end{...} 环境边界，用于把浮动体从高亮包裹中拆出。
# 必须带捕获组：re.split 只有在存在捕获组时才会把匹配到的 token 一并返回。
_ENV_TOKEN_RE = re.compile(r"(\\(?:begin|end)\{[^{}\n]+\})")

# latexdiff 支持的标记类型
LATEXDIFF_TYPES = [
    "UNDERLINE",
    "CTRADITIONAL",
    "CFONT",
    "CHANGEBAR",
    "CCHANGEBAR",
    "CULINECHBAR",
    "FONTSTRIKE",
    "INVISIBLE",
]

# latexdiff 支持的数学公式标记粒度
LATEXDIFF_MATH_MARKUPS = ["fine", "coarse", "off", "whole"]

_DIFF_PREAMBLE_TEMPLATE = r"""
% LaTeXcellence Git diff highlight support.
\usepackage{{xcolor}}
\usepackage{{framed}}
\definecolor{{LXDiffAddBg}}{{HTML}}{{{color_add}}}
\definecolor{{LXDiffDelBg}}{{HTML}}{{{color_del}}}
\newenvironment{{LXDiffAdded}}{{
  \def\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffAddBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{
  \endMakeFramed%
}}
\newenvironment{{LXDiffDeleted}}{{
  \def\FrameCommand{{\setlength{{\fboxsep}}{{3pt}}\colorbox{{LXDiffDelBg}}}}%
  \MakeFramed{{\advance\hsize-\width\FrameRestore}}%
}}{{
  \endMakeFramed%
}}
"""

_DEFAULT_COLOR_ADD_HEX = "D7E8B1"
_DEFAULT_COLOR_DEL_HEX = "FFA3A3"

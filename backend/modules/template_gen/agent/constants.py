"""模板生成 Agent 共享常量与扩展名/正则配置。"""

import re

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

# read_file / 文件读取端点额外允许的生成文件扩展名（只读诊断用，
# 例如编译失败后读 main.log 定位根因；写入仍受 _ALLOWED_SUFFIXES 限制）
_ALLOWED_READ_SUFFIXES = _ALLOWED_SUFFIXES + (
    ".log",
    ".aux",
    ".toc",
    ".lof",
    ".lot",
    ".out",
    ".blg",
    ".bbl",
    ".bcf",
    ".run.xml",
    ".fls",
    ".fdb_latexmk",
    ".xdv",
    ".nav",
    ".snm",
    ".vrb",
    ".synctex.gz",
)

# 参与项目源码快照与静态环境检查的扩展名。生成的诊断文件不应使
# "源码是否变化"判断失真。
_LATEX_SOURCE_SUFFIXES = frozenset(
    {
        ".tex",
        ".sty",
        ".cls",
        ".bib",
        ".cfg",
        ".bst",
        ".def",
        ".ltx",
        ".latex",
    }
)
# 只对正文源文件做整文件环境配对检查。`.sty`/`.cls` 经常把环境的打开
# 与关闭分别放在 beamer/template hook 或 begin/end 宏中，按文件顺序扫描会
# 将这种合法的跨宏配对误判为错误；包代码仍由实际编译验证。
_LATEX_ENV_CHECK_SUFFIXES = frozenset({".tex", ".ltx", ".latex"})

# 正文文件在 TeX 中读取时 `@` 不是字母。把类/样式内部的实现宏泄漏到
# `.tex` 会将 `\@private@name` 拆成 `\@` + 普通文本，常见结果是
# `\spacefactor in vertical mode` 或一串看似无关的 undefined control
# sequence。这里仅拦截控制序列形式，不拦截普通正文中的邮箱地址等字面 `@`。
_PRIVATE_BODY_COMMAND_RE = re.compile(r"\\@(?:[A-Za-z@]+)")
# `\ProvidesClass` / `\ProvidesPackage` 的可选参数必须以 LaTeX 内核要求的
# YYYY/MM/DD 日期开头；写 `[2024/01 Master Thesis Template]` 这类值会把
# 后面的 "Master" 当作 `\ifnum` 的数字解析，在 `\documentclass` 处产生
# 非常难定位的 "Missing = inserted for \ifnum"。
_PROVIDES_STATEMENT_RE = re.compile(
    r"\\(?:ProvidesClass|ProvidesPackage)\s*\{[^{}]*\}\s*\[([^\]]*)\]"
)
_VALID_PROVIDES_DATE_RE = re.compile(r"^\s*\d{4}/\d{1,2}/\d{1,2}\b")
# 正文文件出现样式/命令定义时，说明格式没有全部收敛到 .cls/.sty。
_BODY_STYLE_COMMAND_RE = re.compile(
    r"\\(?:newcommand|renewcommand|newenvironment|renewenvironment|"
    r"g?def|setlength|addtolength|titleformat|titlespacing|geometry|"
    r"setmainfont|setsansfont|setCJKmainfont|setCJKsansfont|pagestyle|"
    r"hypersetup|ctexset)"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_PACKAGE_RE = re.compile(
    r"\\(?:usepackage|documentclass|RequirePackage)"
    r"(?:\[[^\]]*\])?\s*\{[^}]*\b(?:ctex[a-z]*|xecjk)\b",
    re.IGNORECASE,
)
# 本地样式/主题/输入文件可能提供 CJK 支持，需要实际解析这些依赖后再判断；
# 不能因为出现了 `\usepackage{graphicx}` 就默认“有人会负责加载 ctex”。
_CJK_DEPENDENCY_RE = re.compile(
    r"\\(?P<cmd>usepackage|RequirePackage|input|include|subfile|LoadClass|"
    r"usetheme|usecolortheme|usefonttheme)"
    r"(?:\s*\[[^\]]*\])?\s*\{(?P<name>[^{}]+)\}"
)
_SETTER_ACCESSOR_RE = re.compile(
    r"\\newcommand\{\\([A-Za-z@]+)\}\s*(?:\[[^\]]*\])?\s*"
    r"\{\\renewcommand\{\\([A-Za-z@]+)\}\{#1\}"
)


# 编译日志回传给 LLM 时的截断长度（字符）
_LOG_TRUNCATE = 4000

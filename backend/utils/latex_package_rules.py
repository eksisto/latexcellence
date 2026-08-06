"""
LaTeX 宏包规则表（确定性检测的唯一事实来源）。

本模块只存放"命令/环境/列说明符 → 宏包"的映射数据，不包含扫描逻辑。
新增规则时在此追加即可，无需修改 latex_package_analyzer 的扫描代码。

设计原则（避免误报）：
    - 只收录"必须加载该宏包才能编译通过"的命令/环境；
    - LaTeX 内核自带的内容（tabular / equation / \\frac / \\sum / \\mathcal /
      \\includegraphics 等之外的基础命令）一律不收录；
    - 同一命令被多个宏包提供时，优先选择最常用、覆盖面最广的提供者
      （如 \\textcolor → xcolor，而非 color）；
    - 环境名区分大小写，与 LaTeX 行为一致（如 {NiceTabular} 与 {nicetabular}
      是不同的环境）。
"""

# ── 命令 → 宏包 ──────────────────────────────────────────────────────────────
# key 为去掉反斜杠的命令名（如 "toprule" 对应 \\toprule）。
COMMAND_PACKAGES: dict[str, str] = {
    # 表格排版
    "toprule": "booktabs",
    "midrule": "booktabs",
    "bottomrule": "booktabs",
    "cmidrule": "booktabs",
    "addlinespace": "booktabs",
    "morecmidrules": "booktabs",
    "multirow": "multirow",
    "makecell": "makecell",
    "thead": "makecell",
    "makecellbox": "makecell",
    "makegapedcells": "makecell",
    "diagbox": "diagbox",
    "backslashbox": "slashbox",
    "hhline": "hhline",
    "cellcolor": "colortbl",
    "rowcolor": "colortbl",
    "columncolor": "colortbl",
    "tnote": "threeparttable",
    "tablenotes": "threeparttable",
    "ding": "pifont",
    "Pifont": "pifont",
    "Block": "nicematrix",
    "CodeBefore": "nicematrix",
    "RowStyle": "nicematrix",
    "ColumnStyle": "nicematrix",
    "SetRow": "tabularray",
    "SetCell": "tabularray",
    "SetHline": "tabularray",
    "SetVLine": "tabularray",
    "SetHlines": "tabularray",
    "SetVLines": "tabularray",
    "SetColumnWidth": "tabularray",
    "arraybackslash": "array",
    "newcolumntype": "array",
    "extrarowheight": "array",
    "firsthline": "array",
    "lasthline": "array",
    "endfirsthead": "longtable",
    "endhead": "longtable",
    "endfoot": "longtable",
    "endlastfoot": "longtable",
    "setlongtables": "longtable",
    # 图形与颜色
    "includegraphics": "graphicx",
    "resizebox": "graphicx",
    "scalebox": "graphicx",
    "rotatebox": "graphicx",
    "reflectbox": "graphicx",
    "graphicspath": "graphicx",
    "color": "xcolor",
    "textcolor": "xcolor",
    "colorbox": "xcolor",
    "fcolorbox": "xcolor",
    "definecolor": "xcolor",
    "pagecolor": "xcolor",
    "captionsetup": "caption",
    "captionof": "caption",
    "subcaption": "subcaption",
    "subref": "subcaption",
    "subfloat": "subfig",
    # 数学（amsmath 系列）
    "dfrac": "amsmath",
    "tfrac": "amsmath",
    "dbinom": "amsmath",
    "tbinom": "amsmath",
    "binom": "amsmath",
    "overset": "amsmath",
    "underset": "amsmath",
    "substack": "amsmath",
    "text": "amsmath",
    "intertext": "amsmath",
    "eqref": "amsmath",
    "tag": "amsmath",
    "notag": "amsmath",
    "DeclareMathOperator": "amsmath",
    "xrightarrow": "amsmath",
    "xleftarrow": "amsmath",
    "boxed": "amsmath",
    "pmb": "amsmath",
    "boldsymbol": "amsmath",
    "numberwithin": "amsmath",
    "operatorname": "amsmath",
    "genfrac": "amsmath",
    "overleftrightarrow": "amsmath",
    "underleftrightarrow": "amsmath",
    # 数学（符号与字体）
    "mathbb": "amssymb",
    "mathfrak": "amssymb",
    "bm": "bm",
    "mathscr": "mathrsfs",
    "mathds": "dsfont",
    "mathbbm": "bbm",
    "coloneqq": "mathtools",
    "eqqcolon": "mathtools",
    "vcentcolon": "mathtools",
    "shortintertext": "mathtools",
    "shoveleft": "mathtools",
    "shoveright": "mathtools",
    "DeclarePairedDelimiter": "mathtools",
    "mathclap": "mathtools",
    "mathllap": "mathtools",
    "mathrlap": "mathtools",
    "cancel": "cancel",
    "bcancel": "cancel",
    "xcancel": "cancel",
    "uline": "ulem",
    "uuline": "ulem",
    "uwave": "ulem",
    "sout": "ulem",
    "xout": "ulem",
    "nicefrac": "nicefrac",
    # 数值与单位
    "num": "siunitx",
    "SI": "siunitx",
    "si": "siunitx",
    "tablenum": "siunitx",
    "qty": "siunitx",
    "unit": "siunitx",
    "ang": "siunitx",
    "numrange": "siunitx",
    "numlist": "siunitx",
    "complexnum": "siunitx",
    "numprint": "numprint",
    # 化学
    "ce": "mhchem",
    "ch": "chemformula",
    # 其它常用命令
    "url": "url",
    "href": "hyperref",
    "hyperref": "hyperref",
    "autoref": "hyperref",
    "nameref": "hyperref",
    "hypersetup": "hyperref",
    "geometry": "geometry",
    "newgeometry": "geometry",
    "restoregeometry": "geometry",
    "fancyhead": "fancyhdr",
    "fancyfoot": "fancyhdr",
    "fancyhf": "fancyhdr",
    "fancypagestyle": "fancyhdr",
    "setlist": "enumitem",
    "setitemize": "enumitem",
    "setenumerate": "enumitem",
    "setdescription": "enumitem",
    "doublespacing": "setspace",
    "onehalfspacing": "setspace",
    "singlespacing": "setspace",
    "setstretch": "setspace",
    "lstset": "listings",
    "lstinline": "listings",
    "Verb": "fancyvrb",
    "mintinline": "minted",
    "addplot": "pgfplots",
    "pgfplotsset": "pgfplots",
    "tikzset": "tikz",
    "tikzstyle": "tikz",
    "systeme": "systeme",
}

# ── 环境 → 宏包 ──────────────────────────────────────────────────────────────
# key 为 \\begin{...} 中的环境名（区分大小写）。
# 注意：{tabular} / {array} / {equation} / {figure} / {table} / {minipage} /
# {verbatim} / {itemize} 等为 LaTeX 内核环境，刻意不收录。
ENV_PACKAGES: dict[str, str] = {
    # 数学环境
    "align": "amsmath",
    "align*": "amsmath",
    "aligned": "amsmath",
    "alignedat": "amsmath",
    "gather": "amsmath",
    "gather*": "amsmath",
    "multline": "amsmath",
    "multline*": "amsmath",
    "split": "amsmath",
    "cases": "amsmath",
    "cases*": "amsmath",
    "matrix": "amsmath",
    "pmatrix": "amsmath",
    "bmatrix": "amsmath",
    "Bmatrix": "amsmath",
    "vmatrix": "amsmath",
    "Vmatrix": "amsmath",
    "smallmatrix": "amsmath",
    "equation*": "amsmath",
    "subequations": "amsmath",
    "multlined": "mathtools",
    # 表格环境
    "tabularx": "tabularx",
    "tabulary": "tabulary",
    "longtable": "longtable",
    "tblr": "tabularray",
    "tabularray": "tabularray",
    "NiceTabular": "nicematrix",
    "NiceTabularX": "nicematrix",
    "NiceMatrix": "nicematrix",
    "NiceArray": "nicematrix",
    "NiceMatrixBlock": "nicematrix",
    "threeparttable": "threeparttable",
    "tablenotes": "threeparttable",
    "subfigure": "subcaption",
    "subtable": "subcaption",
    # 代码与框
    "lstlisting": "listings",
    "minted": "minted",
    "framed": "framed",
    "shaded": "framed",
    "leftbar": "framed",
    "mdframed": "mdframed",
    "tcolorbox": "tcolorbox",
    # 绘图
    "tikzpicture": "tikz",
    "axis": "pgfplots",
    "loglogaxis": "pgfplots",
    "semilogxaxis": "pgfplots",
    "semilogyaxis": "pgfplots",
    # 页面排版
    "multicols": "multicol",
    "landscape": "lscape",
    "sidewaystable": "rotating",
    "sidewaysfigure": "rotating",
    "spacing": "setspace",
    "empheq": "empheq",
}

# ── 列说明符 → 宏包 ──────────────────────────────────────────────────────────
# 在表格环境的列说明符（如 {>{\\bfseries}l S X}）中出现的字符/前缀。
COLUMN_LETTER_PACKAGES: dict[str, str] = {
    "X": "tabularx",
    "S": "siunitx",
    "D": "dcolumn",
}

# 列说明符前缀 → 宏包（p{} 为内核能力，刻意不收录）。
COLUMN_PREFIX_PACKAGES: tuple[str, ...] = ("m{", "b{", "w{", "W{", ">{", "<{")

# 在这些环境中 X 列类型为宏包原生提供（无需 tabularx）。
X_NATIVE_ENVS: frozenset[str] = frozenset(
    {"tblr", "tabularray", "NiceTabular", "NiceTabularX", "NiceArray"}
)

# 这些环境的第一个花括号参数是宽度（tabularx 语法：\\begin{tabularx}{宽度}{列说明符}；
# NiceTabularX 与 tabularx 同构，宽度在前、列说明符在后）。
WIDTH_FIRST_ENVS: frozenset[str] = frozenset({"tabularx", "tabulary", "NiceTabularX"})

# 需要扫描列说明符的表格类环境（含内核环境，用于识别其中的 S/X/D 等列类型）。
COLSPEC_ENVS: frozenset[str] = frozenset(
    {
        "tabular",
        "tabularx",
        "tabulary",
        "longtable",
        "array",
        "tblr",
        "tabularray",
        "NiceTabular",
        "NiceTabularX",
        "NiceArray",
    }
)

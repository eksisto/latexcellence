"""
LaTeX 宏包规则表（确定性检测的唯一事实来源）。

本模块只存放"命令/环境/列说明符 → 宏包"的映射数据，不包含扫描逻辑。
新增规则时在此追加即可，无需修改 latex_package_analyzer 的扫描代码。

设计原则（避免误报）：
    - 只收录"必须加载该宏包才能编译通过"的命令/环境；
    - LaTeX 内核自带的内容（tabular / equation / \\frac / \\sum / \\mathcal /
      \\includegraphics 等之外的基础命令）一律不收录，KERNEL_COMMANDS /
      KERNEL_ENVS 白名单用于测试守卫；
    - 同一命令被多个宏包提供时，优先选择最常用、覆盖面最广的提供者
      （如 \\textcolor → xcolor，而非 color），取舍记录在 AMBIGUITY_DECISIONS；
    - 环境名区分大小写，与 LaTeX 行为一致（如 {NiceTabular} 与 {nicetabular}
      是不同的环境）。

新增规则模板：
    - 命令/环境名 + 目标宏包 + 证据（CTAN 文档或编译验证）
    - 测试片段（加入 tests/fixtures/latex_packages/ 语料）
    - 是否内核能力（若在内核白名单内则拒绝收录）
    - 歧义说明（多个提供者时记录取舍理由）

变更日志：
    - 2026-08-06
      - 新增：引用/交叉引用（natbib/biblatex/cleveref/varioref）、算法环境
        （algorithm/algorithmicx）、定理证明（amsthm）、附录（appendix）、
        PDF 与图形（pdfpages/adjustbox/wrapfig）、中文与字体
        （fontspec/xeCJK/cjk/textgreek）、表格补充（tabularray/supertabular/
        xtab/xcolor/colortbl）、页面布局（titlesec/placeins/afterpage/
        todonotes/marginnote/multicol/changepage）、代码补充
        （minted/listings/alltt/fancyvrb）、术语缩略词（glossaries/acronym/
        epigraph）、数学补充（slashed/leftidx/tensor/esint）。
      - 编译验证修正三处计划条目（依据 TeX Live 2026 离线编译）：
        1. `\\textgreek` → textgreek 不成立（textgreek.sty 未定义该命令，
           验证编译报 Undefined control sequence）；实际用户命令为 `\\TextGreek`，
           改收录 `\\TextGreek` → textgreek。
        2. `{ltablex}` → ltablex 不成立（ltablex 包未定义同名环境，仅改造
           tabularx 支持跨页），不收录。
        3. `{algpseudocode}` → algpseudocode 不成立（algpseudocode 包未定义
           同名环境，环境仍为 algorithmic，由 algorithmicx 提供），不收录；
           `{algorithmic}` → algorithmicx 经编译验证成立，收录。
      - `\\rowcolors` → xcolor 收录但注明需 `[table]` 选项（xcolor 仅在
        table 选项下定义 `\\rowcolors`；选项推断不在本功能范围）。
      - 新增内核白名单 KERNEL_COMMANDS / KERNEL_ENVS、领域排除表
        DOMAIN_EXCLUDED_PACKAGES、歧义决策记录 AMBIGUITY_DECISIONS。
      - 包名完整性说明：xeCJK 为大小写敏感包名（文件名 xeCJK.sty），
        规则表保留原样，数据完整性测试对其单独豁免"全小写"要求。
      - 语料基线（2026-08-06，片段数量以语料测试输出为准）：
        查准率 1.000 / 查全率 1.000 / LLM 兜底触发率 0.000；
        离线编译验证全部非跳过片段通过，对抗样本跳过原因见 manifest。
"""

# ── LaTeX 内核白名单（守卫：禁止把内核能力收录进规则表）────────────────────────
# 仅收录明确由 LaTeX 内核提供的命令/环境，用于测试断言规则表与白名单无交集。
KERNEL_COMMANDS: frozenset[str] = frozenset(
    {
        # 文本与段落
        "begin",
        "end",
        "documentclass",
        "usepackage",
        "RequirePackage",
        "input",
        "include",
        "part",
        "chapter",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
        "appendix",
        "label",
        "ref",
        "pageref",
        "cite",
        "footnote",
        "footnotemark",
        "footnotetext",
        "caption",
        "item",
        "enumerate",
        "emph",
        "textbf",
        "textit",
        "textrm",
        "textsf",
        "texttt",
        "textsc",
        "mbox",
        "makebox",
        "rule",
        "hspace",
        "vspace",
        "hfill",
        "vfill",
        "hrule",
        "centering",
        "raggedright",
        "raggedleft",
        "newpage",
        "clearpage",
        "cleardoublepage",
        "pagebreak",
        "linebreak",
        "newline",
        "par",
        "noindent",
        "indent",
        "author",
        "title",
        "date",
        "maketitle",
        "tableofcontents",
        "listoffigures",
        "listoftables",
        "bibliography",
        "bibliographystyle",
        "today",
        "thanks",
        "newcommand",
        "renewcommand",
        "providecommand",
        "newenvironment",
        "renewenvironment",
        "newtheorem",
        "newlength",
        "setlength",
        "addtolength",
        # 数学内核
        "frac",
        "sqrt",
        "sum",
        "prod",
        "int",
        "lim",
        "log",
        "ln",
        "exp",
        "sin",
        "cos",
        "tan",
        "partial",
        "infty",
        "pi",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "theta",
        "lambda",
        "mu",
        "sigma",
        "omega",
        "phi",
        "Delta",
        "Gamma",
        "Theta",
        "Lambda",
        "Sigma",
        "Omega",
        "Phi",
        "rightarrow",
        "leftarrow",
        "cdot",
        "times",
        "pm",
        "leq",
        "geq",
        "neq",
        "approx",
        "in",
        "subset",
        "subseteq",
        "cup",
        "cap",
        "forall",
        "exists",
        "nabla",
        "mathcal",
        "mathrm",
        "mathbf",
        "mathit",
        "mathsf",
        "mathtt",
        "overline",
        "underline",
        "hat",
        "bar",
        "tilde",
        "vec",
        "dot",
        "ddot",
        "prime",
        "circ",
        "div",
        "mod",
        "pmod",
        "bmod",
        "qquad",
        "quad",
        "left",
        "right",
        "big",
        "Big",
        "bigl",
        "bigr",
        "Bigl",
        "Bigr",
        "textstyle",
        "displaystyle",
        "scriptstyle",
        "scriptscriptstyle",
        "overbrace",
        "underbrace",
        "stackrel",
        "buildrel",
        "not",
        "dots",
        "ldots",
        "cdots",
        "vdots",
        "ddots",
        "hline",
        "vline",
        "multicolumn",
    }
)

# 注意：equation* 不是内核环境（需 amsmath），故不列入。
KERNEL_ENVS: frozenset[str] = frozenset(
    {
        "document",
        "tabular",
        "array",
        "equation",
        "eqnarray",
        "eqnarray*",
        "figure",
        "figure*",
        "table",
        "table*",
        "minipage",
        "verbatim",
        "verbatim*",
        "itemize",
        "enumerate",
        "description",
        "center",
        "flushleft",
        "flushright",
        "quote",
        "quotation",
        "abstract",
        "titlepage",
        "picture",
        "tabbing",
        "thebibliography",
        "theindex",
        "math",
        "displaymath",
        "list",
        "trivlist",
    }
)

# ── 命令 → 宏包 ──────────────────────────────────────────────────────────────
# key 为去掉反斜杠的命令名（如 "toprule" 对应 \\toprule）。
# 歧义取舍见 AMBIGUITY_DECISIONS。
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
    "arrayrulecolor": "colortbl",
    "rowcolors": "xcolor",  # 需 \usepackage[table]{xcolor}；选项推断不在范围内
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
    # 引用与参考文献
    "citep": "natbib",
    "citet": "natbib",
    "citealp": "natbib",
    "citealt": "natbib",
    "citeauthor": "natbib",  # biblatex 亦提供，取 natbib（见 AMBIGUITY_DECISIONS）
    "citeyear": "natbib",
    "parencite": "biblatex",
    "textcite": "biblatex",
    "autocite": "biblatex",
    "addbibresource": "biblatex",
    "printbibliography": "biblatex",
    # 交叉引用
    "cref": "cleveref",
    "Cref": "cleveref",
    "labelcref": "cleveref",
    "vref": "varioref",
    "Vref": "varioref",
    "vpageref": "varioref",
    "footref": "hyperref",
    # 定理与证明
    "qed": "amsthm",
    "qedhere": "amsthm",
    # 插入 PDF 与图形调整
    "includepdf": "pdfpages",
    "adjustbox": "adjustbox",
    # 中文与字体
    "setmainfont": "fontspec",
    "setsansfont": "fontspec",
    "setmonofont": "fontspec",
    "setCJKmainfont": "xeCJK",
    "setCJKsansfont": "xeCJK",
    "setCJKmonofont": "xeCJK",
    "setCJKfamilyfont": "xeCJK",
    "TextGreek": "textgreek",  # textgreek 提供 \TextGreek；\textgreek 未定义（编译验证）
    # 页面布局
    "titleformat": "titlesec",
    "FloatBarrier": "placeins",
    "afterpage": "afterpage",
    "todo": "todonotes",
    "listoftodos": "todonotes",
    "marginnote": "marginnote",
    "columnbreak": "multicol",
    # 代码补充
    "inputminted": "minted",
    "lstinputlisting": "listings",
    # 术语与缩略词
    "gls": "glossaries",
    "glsadd": "glossaries",
    "printglossary": "glossaries",
    "ac": "acronym",
    "acs": "acronym",
    "acf": "acronym",
    "epigraph": "epigraph",
    # 数学补充
    "slashed": "slashed",
    "leftidx": "leftidx",
    "tensor": "tensor",
    "oiint": "esint",
    "varoiint": "esint",
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
# {verbatim} / {itemize} 等为 LaTeX 内核环境，刻意不收录（见 KERNEL_ENVS）。
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
    "talltblr": "tabularray",
    "longtblr": "tabularray",
    "ltblr": "tabularray",
    "supertabular": "supertabular",
    "xtabular": "xtab",
    "NiceTabular": "nicematrix",
    "NiceTabularX": "nicematrix",
    "NiceMatrix": "nicematrix",
    "NiceArray": "nicematrix",
    "NiceMatrixBlock": "nicematrix",
    "threeparttable": "threeparttable",
    "tablenotes": "threeparttable",
    "subfigure": "subcaption",
    "subtable": "subcaption",
    # 算法伪代码（\If 等命令在多个宏包中都存在，只映射环境）
    "algorithm": "algorithm",
    "algorithmic": "algorithmicx",
    # 定理与证明
    "proof": "amsthm",
    # 附录
    "appendices": "appendix",
    # 图形与浮动
    "wrapfigure": "wrapfig",
    "wraptable": "wrapfig",
    # 中文环境
    "CJK": "cjk",
    "CJK*": "cjk",
    # 页面布局
    "adjustwidth": "changepage",
    # 代码与框
    "lstlisting": "listings",
    "minted": "minted",
    "alltt": "alltt",
    "Verbatim": "fancyvrb",
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
    {
        "tblr",
        "tabularray",
        "talltblr",
        "longtblr",
        "ltblr",
        "NiceTabular",
        "NiceTabularX",
        "NiceArray",
    }
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
        "supertabular",
        "xtabular",
        "array",
        "tblr",
        "tabularray",
        "talltblr",
        "longtblr",
        "ltblr",
        "NiceTabular",
        "NiceTabularX",
        "NiceArray",
    }
)

# ── 领域参数 → 排除的宏包 ────────────────────────────────────────────────────
# 粗粒度按包名排除（计划阶段二方向 2.1）。`float` 由分析器的 [H] 浮动识别隐式产出，
# 虽不在映射表中，仍按领域排除。后续如需更细粒度再改为逐条打标签。
DOMAIN_EXCLUDED_PACKAGES: dict[str, frozenset[str]] = {
    "general": frozenset(),
    "math": frozenset(
        {
            # 表格排版（公式片段不应带出）
            "booktabs",
            "multirow",
            "makecell",
            "diagbox",
            "slashbox",
            "hhline",
            "colortbl",
            "threeparttable",
            "longtable",
            "tabularray",
            "nicematrix",
            "tabularx",
            "tabulary",
            "dcolumn",
            "supertabular",
            "xtab",
            "pifont",
            # 浮动与图形排版
            "float",
            "wrapfig",
            "caption",
            "subcaption",
            "subfig",
            "pdfpages",
            "adjustbox",
            # 页面布局
            "geometry",
            "fancyhdr",
            "setspace",
            "enumitem",
            "multicol",
            "lscape",
            "rotating",
            "titlesec",
            "placeins",
            "afterpage",
            "todonotes",
            "marginnote",
            "changepage",
            "appendix",
            # 引用与术语
            "natbib",
            "biblatex",
            "cleveref",
            "varioref",
            "hyperref",
            "url",
            "glossaries",
            "acronym",
            "epigraph",
            # 字体与中文
            "fontspec",
            "xeCJK",
            "cjk",
            "textgreek",
            # 代码与框
            "listings",
            "fancyvrb",
            "minted",
            "alltt",
            "framed",
            "mdframed",
            "tcolorbox",
            # 算法与定理
            "algorithm",
            "algorithmicx",
            "amsthm",
            # 绘图
            "tikz",
            "pgfplots",
        }
    ),
    "table": frozenset(
        {
            # 引用与术语（表格片段不会包含）
            "natbib",
            "biblatex",
            "cleveref",
            "varioref",
            "hyperref",
            "url",
            "glossaries",
            "acronym",
            "epigraph",
            # 字体与中文
            "fontspec",
            "xeCJK",
            "cjk",
            "textgreek",
            # 页面布局（sidewaystable/landscape 属于表格可用，保留）
            "geometry",
            "fancyhdr",
            "setspace",
            "enumitem",
            "multicol",
            "titlesec",
            "placeins",
            "afterpage",
            "todonotes",
            "marginnote",
            "changepage",
            "appendix",
            # 算法与定理
            "algorithm",
            "algorithmicx",
            "amsthm",
            # 代码与框
            "listings",
            "fancyvrb",
            "minted",
            "alltt",
            "framed",
            "mdframed",
            "tcolorbox",
            # PDF 插入
            "pdfpages",
        }
    ),
}

# ── 歧义决策记录 ─────────────────────────────────────────────────────────────
# 元组结构：(命令/环境名, 类型, 选择的宏包, 取舍理由)。
# 新增"一个命令多个提供者"的映射前，先在此查重并记录理由。
AMBIGUITY_DECISIONS: tuple[tuple[str, str, str, str], ...] = (
    ("textcolor", "command", "xcolor", "xcolor 是事实标准且为 color 的超集"),
    ("color", "command", "xcolor", "xcolor 是事实标准且为 color 的超集"),
    (
        "boldsymbol",
        "command",
        "amsmath",
        "amsmath 更常用；bm 提供 \\bm 而非 \\boldsymbol",
    ),
    ("ce", "command", "mhchem", "mhchem 比 chemformula 更常用"),
    (
        "citeauthor",
        "command",
        "natbib",
        "biblatex 亦提供；natbib 为经典提供者，按计划取舍",
    ),
    (
        "citeyear",
        "command",
        "natbib",
        "biblatex 亦提供；natbib 为经典提供者，按计划取舍",
    ),
    (
        "ac",
        "command",
        "acronym",
        "glossaries-extra 亦可提供；acronym 为经典提供者，按计划取舍",
    ),
    ("acs", "command", "acronym", "同上"),
    ("acf", "command", "acronym", "同上"),
    (
        "gls",
        "command",
        "glossaries",
        "glossaries-extra 亦提供；glossaries 为经典提供者，按计划取舍",
    ),
    (
        "rowcolors",
        "command",
        "xcolor",
        "需 \\usepackage[table]{xcolor}（加载 colortbl）；选项推断不在范围内",
    ),
    (
        "arrayrulecolor",
        "command",
        "colortbl",
        "xcolor 的 table 选项会加载 colortbl；直接指向 colortbl",
    ),
    (
        "algorithmic",
        "environment",
        "algorithmicx",
        "algorithmicx 定义通用 algorithmic 环境；旧 algorithm 包亦提供，取维护活跃者",
    ),
    ("qed", "command", "amsthm", "amsthm 为标准提供者"),
    ("footref", "command", "hyperref", "hyperref 为标准提供者"),
)

# 数据完整性测试豁免的大小写敏感包名（文件名即如此，不能小写化）。
MIXED_CASE_PACKAGE_NAMES: frozenset[str] = frozenset({"xeCJK"})

# 分析器隐式产出、但不出现在映射表中的包名（[H] 浮动识别）。
IMPLICIT_PACKAGES: frozenset[str] = frozenset({"float"})

# 全部已知包名（映射表 + 隐式产出），供数据完整性测试使用。
ALL_KNOWN_PACKAGES: frozenset[str] = frozenset(
    set(COMMAND_PACKAGES.values())
    | set(ENV_PACKAGES.values())
    | set(COLUMN_LETTER_PACKAGES.values())
    | IMPLICIT_PACKAGES
)

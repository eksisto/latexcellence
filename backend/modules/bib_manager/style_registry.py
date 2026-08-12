"""
参考文献格式 → LaTeX 样式映射注册表。

内置常见学术引用格式到 LaTeX .bst / biblatex 样式的对应关系，
供 format_analysis 结果自动推荐 LaTeX 排版样式使用。
"""

from typing import Any


class LatexStyleInfo:
    """单条 LaTeX 样式映射"""

    __slots__ = (
        "backend",
        "package",
        "bibliographystyle",
        "preamble",
        "citation_command",
        "print_command",
        "description",
        "aliases",
    )

    def __init__(
        self,
        *,
        backend: str = "bibtex",
        package: str = "",
        bibliographystyle: str | None = None,
        preamble: str = "",
        citation_command: str = "\\cite",
        print_command: str = "\\bibliography",
        description: str = "",
        aliases: list[str] | None = None,
    ):
        self.backend = backend
        self.package = package
        self.bibliographystyle = bibliographystyle
        self.preamble = preamble
        self.citation_command = citation_command
        self.print_command = print_command
        self.description = description
        self.aliases = aliases or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "package": self.package,
            "bibliographystyle": self.bibliographystyle,
            "preamble": self.preamble,
            "citation_command": self.citation_command,
            "print_command": self.print_command,
            "description": self.description,
        }


# ── 内置样式注册表 ──────────────────────────────────────────────────────────
# key = 标准格式名称（与 ReferenceFormatResult.format_name 对齐）
# value = LatexStyleInfo 实例

_REGISTRY: dict[str, LatexStyleInfo] = {
    "GB/T 7714-2015": LatexStyleInfo(
        backend="bibtex",
        package="gbt7714",
        bibliographystyle="gbt7714-numerical",
        preamble=r"\usepackage{gbt7714}",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="中国国家标准 GB/T 7714-2015（顺序编码制）",
        aliases=["gbt7714", "gb/t 7714", "7714", "国标"],
    ),
    "APA 7th": LatexStyleInfo(
        backend="biblatex",
        package="biblatex",
        bibliographystyle=None,
        preamble=r"\usepackage[backend=biber,style=apa]{biblatex}"
        "\n" + r"\addbibresource{references.bib}",
        citation_command=r"\cite",
        print_command=r"\printbibliography",
        description="APA 第7版（作者-年份制）",
        aliases=["apa", "apa7", "apa 7th edition"],
    ),
    "IEEE": LatexStyleInfo(
        backend="bibtex",
        package="IEEEtran",
        bibliographystyle="IEEEtran",
        preamble="",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="IEEE 期刊/会议格式",
        aliases=["ieee", "ieeetran"],
    ),
    "MLA 9th": LatexStyleInfo(
        backend="bibtex",
        package="mla13",
        bibliographystyle="mla",
        preamble=r"\usepackage{mla13}",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="MLA 第9版",
        aliases=["mla", "mla9", "mla 9th edition"],
    ),
    "Chicago": LatexStyleInfo(
        backend="bibtex",
        package="chicago",
        bibliographystyle="chicago",
        preamble="",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="芝加哥手册格式",
        aliases=["chicago", "chicagomanual"],
    ),
    "Vancouver": LatexStyleInfo(
        backend="bibtex",
        package="",
        bibliographystyle="vancouver",
        preamble="",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="温哥华格式（医学期刊）",
        aliases=["vancouver", "温哥华"],
    ),
    "Harvard": LatexStyleInfo(
        backend="bibtex",
        package="natbib",
        bibliographystyle="plainnat",
        preamble=r"\usepackage{natbib}",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="哈佛体系（著者-出版年，natbib + plainnat）",
        aliases=["harvard", "哈佛"],
    ),
    "Nature": LatexStyleInfo(
        backend="bibtex",
        package="natbib",
        bibliographystyle="naturemag",
        preamble=r"\usepackage[numbers]{natbib}",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="Nature 期刊（上标数字制；需 nature 宏包提供的 naturemag.bst）",
        aliases=["nature", "自然"],
    ),
    "AMA": LatexStyleInfo(
        backend="bibtex",
        package="",
        bibliographystyle="ama",
        preamble="",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="美国医学会 AMA 格式（需手动安装 ama.bst，CTAN 提供）",
        aliases=["ama", "american medical association", "美国医学会"],
    ),
    "ACS": LatexStyleInfo(
        backend="biblatex",
        package="biblatex",
        bibliographystyle=None,
        preamble=r"\usepackage[backend=biber,style=chem-acs]{biblatex}"
        "\n" + r"\addbibresource{references.bib}",
        citation_command=r"\cite",
        print_command=r"\printbibliography",
        description="美国化学会 ACS 格式（biblatex-chem 的 chem-acs 样式）",
        aliases=["acs", "american chemical society", "美国化学会"],
    ),
    "OSCOLA": LatexStyleInfo(
        backend="biblatex",
        package="biblatex",
        bibliographystyle=None,
        preamble=r"\usepackage[backend=biber,style=oscola]{biblatex}"
        "\n" + r"\addbibresource{references.bib}",
        citation_command=r"\autocite",
        print_command=r"\printbibliography",
        description="牛津法律引注标准 OSCOLA（biblatex-oscola）",
        aliases=["oscola", "牛津法律"],
    ),
    "Turabian": LatexStyleInfo(
        backend="biblatex",
        package="biblatex-chicago",
        bibliographystyle=None,
        preamble=r"\usepackage[notes]{biblatex-chicago}"
        "\n" + r"\addbibresource{references.bib}",
        citation_command=r"\autocite",
        print_command=r"\printbibliography",
        description="Turabian（芝加哥体系的脚注-参考文献版，biblatex-chicago）",
        aliases=["turabian", "杜拉宾"],
    ),
    "ACM": LatexStyleInfo(
        backend="bibtex",
        package="natbib",
        bibliographystyle="ACM-Reference-Format",
        preamble=r"\usepackage[numbers,sort&compress]{natbib}",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="美国计算机协会 ACM 格式（需 acmart 提供的 ACM-Reference-Format.bst）",
        aliases=["acm", "american computer society", "美国计算机协会"],
    ),
    "APS": LatexStyleInfo(
        backend="bibtex",
        package="",
        bibliographystyle="apsrev4-2",
        preamble="",
        citation_command=r"\cite",
        print_command=r"\bibliography",
        description="美国物理学会 APS 格式（需 REVTeX 4.2 提供的 apsrev4-2.bst）",
        aliases=["aps", "american physical society", "美国物理学会"],
    ),
}


def get_style_for_format(format_name: str) -> LatexStyleInfo | None:
    """根据格式名称查询对应的 LaTeX 样式。

    先精确匹配 key，再遍历 aliases 做包含匹配。
    """
    if not format_name:
        return None

    normalized = format_name.strip()

    # 精确匹配
    if normalized in _REGISTRY:
        return _REGISTRY[normalized]

    # 别名模糊匹配（格式名包含别名关键词）
    lower = normalized.lower()
    for style_info in _REGISTRY.values():
        for alias in style_info.aliases:
            if alias in lower:
                return style_info

    return None


def list_all_styles() -> dict[str, dict[str, Any]]:
    """列出所有内置样式映射。"""
    return {name: info.to_dict() for name, info in _REGISTRY.items()}

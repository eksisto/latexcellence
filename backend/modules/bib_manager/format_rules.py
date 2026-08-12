"""
参考文献格式确定性识别规则（第一道闸）。

基于可枚举的版式特征（文献类型标识、期刊缩写、DOI 前缀、字段顺序/标点）直接
判定格式，LLM 仅作为规则未命中时的兜底。规则必须保持可测试：新增/修改格式时
同步在 tests/test_utils/test_bib_manager_format_rules.py 维护语料。
"""

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormatRule:
    """单条格式判定规则：任一 pattern 命中即判定为该格式。"""

    format_name: str
    patterns: tuple[str, ...]
    required_fields: tuple[str, ...] = ("author", "title", "journal", "year")
    optional_fields: tuple[str, ...] = ("volume", "issue", "pages", "doi", "url")
    description: str = ""
    entry_type: str = "article"


_GB_T_ENTRY_TYPES = {
    "J": "article",
    "M": "book",
    "C": "inproceedings",
    "D": "phdthesis",
    "R": "techreport",
    "EB/OL": "misc",
}


# 顺序敏感：更具体的规则（期刊缩写/DOI 前缀）必须排在更泛化的规则之前，
# 例如 AMA 要先于 Vancouver，Nature 要先于 APA。
_RULES: list[FormatRule] = [
    FormatRule(
        "OSCOLA",
        patterns=(r"'[^']{3,120}' \(\d{4}\)\s+\d+(?:\(\d+\))?\s+[A-Z]",),
        required_fields=("author", "title", "year", "journal", "pages"),
        optional_fields=("volume", "issue", "url"),
        description="OSCOLA 法律引注：作者 + '文章标题' + (年份) 卷(期) 期刊 起始页",
    ),
    FormatRule(
        "APS",
        patterns=(r"Phys\.\s*Rev\.",),
        description="APS 物理评论系列（Phys. Rev. Lett. / Phys. Rev. B 等）",
    ),
    FormatRule(
        "ACS",
        patterns=(
            r"J\.\s*Am\.\s*Chem\.\s*Soc\.",
            r"\bACS\s+(?:Nano|Applied|Energy|Sustainable|Catalysis|Central Science)\b",
        ),
        optional_fields=("volume", "issue", "pages", "doi", "url"),
        description="ACS 美国化学会格式（分号分隔作者 + 期刊缩写）",
    ),
    FormatRule(
        "AMA",
        patterns=(r"\bJAMA\b", r"doi:\s*10\.1001/"),
        description="AMA 美国医学会格式（JAMA 等，上标数字制）",
    ),
    FormatRule(
        "ACM",
        patterns=(r"\bJ\.\s*ACM\b", r"10\.1145/", r"\bACM\b.*Article \d+"),
        optional_fields=("volume", "issue", "pages", "doi", "url"),
        description="ACM 格式（ACM-Reference-Format.bst）",
    ),
    FormatRule(
        "Nature",
        patterns=(r"\bNature\s+\d+,\s*\d+", r"10\.1038/"),
        description="Nature 期刊格式（上标数字制）",
    ),
    FormatRule(
        "IEEE",
        patterns=(
            r"vol\.\s*\d+,\s*no\.\s*\d+,\s*pp\.",
            r"IEEE\s+(?:Trans|Transactions|Access|Journal of)",
        ),
        description="IEEE 格式（作者名首字母 + 引号标题 + 卷/期/页码）",
    ),
    FormatRule(
        "MLA 9th",
        patterns=(r"vol\.\s*\d+,\s*no\.\s*\d+,\s*\d{4},\s*pp\.",),
        description="MLA 第 9 版（作者. “题名.” 刊名, 卷, 期, 年, 页码.）",
    ),
    FormatRule(
        "Harvard",
        patterns=(
            r"[A-Z][a-z]+, [A-Z]\.(?:, [A-Z][a-z]+, [A-Z]\.)*"
            r" and [A-Z][a-z]+, [A-Z]\., \d{4}\.",
        ),
        description="哈佛体系（著者-出版年：Last, I. and Last, I., YYYY.）",
    ),
    FormatRule(
        "Vancouver",
        patterns=(r"\.\s*\d{4};\d+(?:\(\d+\))?:\d+",),
        description="温哥华格式（缩写刊名. 年;卷(期):页码.）",
    ),
    FormatRule(
        "Chicago",
        patterns=(r"\.\s*\d{4}\.\s+\"",),
        description="芝加哥著者-出版年（作者. 年份. “题名.” 刊名 卷, 期: 页码.）",
    ),
    FormatRule(
        "Turabian",
        patterns=(
            r"\"[^\"]{3,120}\"\s+[A-Za-z\s-]+ \d+, no\. \d+ \(\d{4}\): \d+",
            r"\bTurabian\b",
            r"\bA Manual for Writers\b",
        ),
        description=(
            "Turabian（芝加哥体系脚注-参考文献版；"
            "纯文献表条目与 Chicago notes 形式相同）"
        ),
    ),
    FormatRule(
        "APA 7th",
        patterns=(r"\([12]\d{3}\)\.\s+[A-Z]",),
        description="APA 第 7 版（作者姓, 名首字母. (年份). 题名. 刊名, 卷(期), 页码.）",
    ),
    FormatRule(
        "GB/T 7714-2015",
        patterns=(r"\[[A-Za-z]{1,4}(?:/[A-Z]{1,4})?\]",),
        description="GB/T 7714-2015（含文献类型标识 [J]/[M]/[C] 等）",
    ),
]


def _observed_fields(text: str) -> list[str]:
    """规则命中时尽力从原文中提取“实际出现的字段”，供缺字段检测使用。"""
    fields: list[str] = []
    if re.search(r"[A-Z][a-z]+(?:[,\s]+[A-Z][a-z]+)*[,\s]", text) or re.search(
        r"[A-Z][a-z]+ and [A-Z][a-z]+", text
    ):
        fields.append("author")
    if re.search(r"[\"'“”‘’]", text):
        fields.append("title")
    if re.search(r"\d{4}", text):
        fields.append("year")
    if re.search(r"vol\.|\b\d+\(", text):
        fields.append("volume")
    if re.search(r"no\.|\b\d+\(\d+\)", text):
        fields.append("issue")
    if re.search(r"pp\.|:\s*\d+", text):
        fields.append("pages")
    if re.search(r"https?://|doi[:/]", text, re.IGNORECASE):
        fields.append("doi")
    if "year" in fields:
        fields.append("journal")
    return fields


def _gbt_marker(text: str) -> str | None:
    match = re.search(r"\[([A-Za-z]{1,4}(?:/[A-Z]{1,4})?)\]", text)
    return match.group(1) if match else None


def detect_format(example: str) -> dict[str, Any] | None:
    """确定性识别参考文献格式；未命中任何规则时返回 None（交给 LLM 兜底）。"""
    text = (example or "").strip()
    if not text:
        return None

    for rule in _RULES:
        if not any(
            re.search(pattern, text, re.IGNORECASE) for pattern in rule.patterns
        ):
            continue
        entry_type = rule.entry_type
        marker = None
        if rule.format_name == "GB/T 7714-2015":
            marker = _gbt_marker(text)
            if marker:
                entry_type = _GB_T_ENTRY_TYPES.get(marker, rule.entry_type)
        return {
            "format_name": rule.format_name,
            "confidence": "high",
            "entry_type": entry_type,
            "document_type_marker": marker,
            "required_fields": list(rule.required_fields),
            "optional_fields": list(rule.optional_fields),
            "field_descriptions": {},
            "format_description": rule.description,
            "raw_observed_fields": _observed_fields(text),
        }
    return None

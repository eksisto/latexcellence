"""
LaTeX 逐字/字面内容识别（parser、guard、分隔符协议提取器共享）。

背景：
    LaTeX 里存在一类"字面内容"——其中的 `%`、`$`、花括号、反斜杠都是普通
    字符而不是结构：
        - 内联逐字命令：\\verb / \\Verb / \\lstinline / \\mintinline
        - 逐字环境：verbatim / Verbatim / lstlisting / minted / comment / alltt
        - URL 参数：\\url / \\path / \\href 的第一个参数（含 %20 等字面百分号）

    此前 latex_parser 与 latex_json_guard 各自维护一套正则，规则分叉导致：
        - parser 的 \\verb 正则排除数字定界符（数字是合法定界符）
        - parser 不支持 \\lstinline 的可选参数与 fancyvrb 的 \\Verb
        - parser 先处理注释再处理 URL，URL 里的 %20 被当作注释吞掉
    本模块提供统一的字面区间识别（find_literal_spans），供：
        - latex_parser：逐字内容整体替换为 marker
        - latex_json_guard：剥离字面内容后再做配平/控制字符扫描
        - 分隔符协议提取器：跳过逐字内容中的 <<<END>>> 协议标记

设计说明：
    - 逐字命令识别支持数字等任意非字母、非花括号、非空白定界符；
      \\lstinline 支持可选参数 [options] 与花括号形式（含嵌套花括号）；
      fancyvrb \\Verb 允许内容跨行。
    - URL 参数按字面剥离：其中的 $、{、}、% 不参与配平与注释判断。
    - find_matching_brace 跳过 \\{ / \\}，与既有 guard 行为保持一致。
"""

import re
from dataclasses import dataclass

# ── 内联逐字命令 ──────────────────────────────────────────────────────────

# \\verb / \\lstinline（可带 * 与 [options]），分隔符形式：内容不能跨行
# （\\verb / \\lstinline 的逐字内容不允许包含换行）。分隔符为命令后第一个
# 非字母、非 `*`、非花括号、非空白的字符，数字是合法定界符（\\verb1...1）。
_INLINE_VERB_DELIM_RE = re.compile(
    r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?"
    r"(?P<delim>[^A-Za-z*{}\s])(?P<body>[^\r\n]*?)(?P=delim)"
)

# fancyvrb \\Verb / \\Verb*：与 \\verb 类似但内容允许跨行（DOTALL）。
_VERB_FANCY_RE = re.compile(
    r"\\Verb\*?(?:\[[^\]]*\])?" r"(?P<delim>[^A-Za-z*{}\s])(?P<body>.*?)(?P=delim)",
    re.DOTALL,
)

# \\lstinline{...} 花括号形式（可带 * 与 [options]）：参数可含嵌套花括号，
# 由 find_literal_spans 用括号计数找到完整参数。
_LSTINLINE_BRACED_RE = re.compile(r"\\lstinline\*?(?:\[[^\]]*\])?\{")

# \\mintinline{lang}{code}：code 可含嵌套花括号，用括号计数匹配。
_MINTINLINE_RE = re.compile(r"\\mintinline\*?(?:\[[^\]]*\])?\{[^{}]*\}\{")

# ── 逐字环境 ──────────────────────────────────────────────────────────────

_VERBATIM_ENV_RE = re.compile(
    r"\\begin\{(verbatim\*?|Verbatim|lstlisting|minted|comment|alltt)\}"
    r".*?\\end\{\1\}",
    re.DOTALL,
)

# ── URL 参数 ──────────────────────────────────────────────────────────────

# \\url / \\path / \\href（可带 [options]），要求紧跟 { 以排除 \\urlstyle
# 等更长的命令名；URL 参数内的 % / $ / 花括号按字面处理。
URL_CMD_START_RE = re.compile(r"\\(?:url|path|href)(?![\w@*])\s*(?:\[[^\]]*\])?\{")


@dataclass(frozen=True)
class LiteralSpan:
    """一段字面内容（逐字命令 / 逐字环境 / URL 参数）在原文中的区间。"""

    start: int
    end: int
    kind: str  # "verb" | "verb_fancy" | "mintinline" | "env" | "url"


def find_matching_brace(value: str, start: int) -> int | None:
    """从 start（'{' 的位置）开始找匹配的 '}'，返回其下标；未闭合返回 None。

    转义的 \\{ / \\} 不参与配平（URL 参数中可能含字面花括号）。
    """
    depth = 0
    i = start
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            i += 2  # 跳过转义字符（\\{ / \\} 等）
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def find_literal_spans(text: str, kinds: set[str] | None = None) -> list[LiteralSpan]:
    """返回文本中所有字面区间的列表（按下标排序、重叠区间已合并）。

    kinds 为空集表示不返回任何区间；省略时返回全部类型
    （verb / verb_fancy / mintinline / env / url）。
    """
    if kinds is None:
        kinds = {"verb", "verb_fancy", "mintinline", "env", "url"}

    spans: list[LiteralSpan] = []
    if "verb" in kinds:
        spans.extend(
            LiteralSpan(m.start(), m.end(), "verb")
            for m in _INLINE_VERB_DELIM_RE.finditer(text)
        )
        for m in _LSTINLINE_BRACED_RE.finditer(text):
            close = find_matching_brace(text, m.end() - 1)
            if close is not None:
                spans.append(LiteralSpan(m.start(), close + 1, "verb"))
    if "verb_fancy" in kinds:
        spans.extend(
            LiteralSpan(m.start(), m.end(), "verb_fancy")
            for m in _VERB_FANCY_RE.finditer(text)
        )
    if "mintinline" in kinds:
        for m in _MINTINLINE_RE.finditer(text):
            close = find_matching_brace(text, m.end() - 1)
            if close is not None:
                spans.append(LiteralSpan(m.start(), close + 1, "mintinline"))
    if "env" in kinds:
        spans.extend(
            LiteralSpan(m.start(), m.end(), "env")
            for m in _VERBATIM_ENV_RE.finditer(text)
        )
    if "url" in kinds:
        for m in URL_CMD_START_RE.finditer(text):
            close = find_matching_brace(text, m.end() - 1)
            if close is not None:
                spans.append(LiteralSpan(m.start(), close + 1, "url"))

    spans.sort(key=lambda s: (s.start, s.end))
    merged: list[LiteralSpan] = []
    for s in spans:
        if merged and s.start <= merged[-1].end:
            prev = merged[-1]
            if s.end > prev.end:
                merged[-1] = LiteralSpan(prev.start, s.end, prev.kind)
        else:
            merged.append(s)
    return merged


def blank_literal_spans(text: str, kinds: set[str] | None = None) -> str:
    """剔除字面内容（替换为空字符串），保留其余文本。"""
    spans = find_literal_spans(text, kinds)
    if not spans:
        return text
    result: list[str] = []
    pos = 0
    for s in spans:
        result.append(text[pos : s.start])
        pos = s.end
    result.append(text[pos:])
    return "".join(result)


def _inside_literal(pos: int, spans: list[LiteralSpan]) -> bool:
    """判断下标是否落在某个字面区间内（区间已排序，二分查找）。"""
    lo, hi = 0, len(spans)
    while lo < hi:
        mid = (lo + hi) // 2
        s = spans[mid]
        if pos < s.start:
            hi = mid
        elif pos >= s.end:
            lo = mid + 1
        else:
            return True
    return False


def _find_delimited_range(
    text: str, open_marker: str, end_marker: str
) -> tuple[int, int] | None:
    """查找分隔符协议块的 (open 结束下标, end 起始下标)。

    跳过字面内容（\\verb / \\Verb / \\lstinline / \\mintinline / 逐字环境 /
    URL 参数）中出现的协议标记：`<<<LATEX>>>\\verb|literal <<<END>>>|<<<END>>>`
    里正文的 <<<END>>> 不是协议结束。大小写不敏感。

    用 re.IGNORECASE 正则直接在原文上搜索（而不是先 lower() 整段再切片），
    返回的下标始终对准原文。lower() 对个别字符（如土耳其字母 İ，U+0130）
    会改变字符串长度，若用 lower 版的下标切原文会整体错位、静默截断。
    """
    open_re = re.compile(re.escape(open_marker), re.IGNORECASE)
    end_re = re.compile(re.escape(end_marker), re.IGNORECASE)
    spans = find_literal_spans(text)

    search_from = 0
    while True:
        open_m = open_re.search(text, search_from)
        if open_m is None:
            return None
        if not _inside_literal(open_m.start(), spans):
            break
        search_from = open_m.end()

    search_from = open_m.end()
    while True:
        end_m = end_re.search(text, search_from)
        if end_m is None:
            return None
        if not _inside_literal(end_m.start(), spans):
            return open_m.end(), end_m.start()
        search_from = end_m.end()


def extract_delimited_block(text: str, open_marker: str, end_marker: str) -> str | None:
    """提取分隔符协议块内容；缺失 open/end 标记返回 None。"""
    found = _find_delimited_range(text, open_marker, end_marker)
    if found is None:
        return None
    content_start, end_idx = found
    return text[content_start:end_idx]


def remove_delimited_block(text: str, open_marker: str, end_marker: str) -> str:
    """移除第一个分隔符协议块（含标记），返回剩余文本；无块时原样返回。"""
    found = _find_delimited_range(text, open_marker, end_marker)
    if found is None:
        return text
    content_start, end_idx = found
    open_idx = content_start - len(open_marker)
    return text[:open_idx] + text[end_idx + len(end_marker) :]

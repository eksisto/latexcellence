"""
LaTeX × JSON 转义损坏防护工具。

背景：
    OpenAI 兼容 API 的 response_format={"type": "json_object"} 只保证输出是合法
    JSON，不保证字符串内容语义正确。LaTeX 命令以反斜杠开头，而 JSON 字符串里
    反斜杠是转义符，两者冲突：

        LLM 写进 JSON 的原文     JSON 解析后的实际内容    结果
        \\rho                    回车 + "ho"              静默损坏（\\r 是合法转义）
        \\frac                    换页 + "rac"             静默损坏
        \\begin                    退格 + "egin"            静默损坏
        \\sigma / \\midrule       Invalid \\escape         解析失败 → 重试

    最危险的是静默损坏：JSON 解析成功、Pydantic 校验也通过，损坏内容被原样
    返回甚至写入文件。本模块提供内容校验器，把"静默损坏"变成"可重试/可拦截"。

    校验项（与 formula_ocr / table_ocr 的既有实现一致）：
        - 控制字符（除 \\n 外）：\\r \\t \\f \\b 等都是 JSON 转义损坏特征
        - '[object Object]' 类垃圾占位符
        - 花括号配平（忽略 \\{ / \\}，并跳过注释、verbatim 与 \\url 内容）
        - $ 配平（忽略 \\$，并跳过注释、verbatim 与 \\url 内容）
        - 可选：\\begin{X} / \\end{X} 环境栈式配平（同样跳过 verbatim）

    字面内容（不参与配平 / 控制字符扫描）：
        - \\verb / \\verb* / fancyvrb \\Verb / \\lstinline（定界符形式）
        - \\mintinline{lang}{code}（code 含嵌套花括号）
        - verbatim / Verbatim / lstlisting / minted / comment / alltt 环境
        - \\url / \\path / \\href 的 URL 参数（LaTeX 与 BibTeX 模式均按字面
          剥离，括号计数支持 URL 内嵌套花括号；\\href 第二参数保留校验）

    校验模式：
        - 默认（LaTeX）：% 是注释起点（\\% 除外），注释内容不参与配平；
          \\url / \\href / \\path 的 URL 参数按字面处理。
        - bibtex=True：% 是字面字符（BibTeX 字段值中的 % 不是注释），不剥离，
          避免 percent-encoding URL（如 https://x/y%20z）被误判为花括号不配平。
        - relaxed=True：非 JSON 传输路径，放行 \\t / \\r 合法 LaTeX 空白。

    错误归因分诊：按错误类型给出不同修复提示（控制字符 / 配平 / 占位符），
    不再一律"把反斜杠双写"。
"""

import re
from typing import Annotated

from pydantic import AfterValidator

# 控制字符：除换行（\\x0a）外的 C0 控制符 + DEL。\\r \\t \\f \\b 等
# 都是 JSON 未转义反斜杠时静默损坏的特征（\\rho → 回车 + ho）。
_BAD_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
# 宽松模式（分隔符协议 / 非 JSON 传输）：\t（U+0009）与 \r（U+000D）是合法
# LaTeX 空白，放行；仍拦截 \b（0x08）、\f（0x0C）等真正的静默损坏特征。
_BAD_CONTROL_RELAXED_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 轻量校验（marker 模块输出字段）：排除 \t / \n（合法空白），\r 在 JSON 路径
# 仍拦截（未转义反斜杠的静默损坏特征），即 [\x00-\x08\x0b-\x1f\x7f]。
_BAD_CONTROL_LITE_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# LLM 把对象序列化成字符串时产生的垃圾占位符
_GARBAGE_RE = re.compile(r"\[object [A-Za-z]+\]")
# 环境声明：\\begin{X} / \\end{X}
_ENV_RE = re.compile(r"\\(begin|end)\{([^}]*)\}")
# 注释：偶数个反斜杠前的 % 才是注释起点（\\% 不是；\\\\% 中 \\ 换行后
# % 开始注释）。注释内容（可能含孤立花括号/$）不参与配平校验。
_COMMENT_RE = re.compile(r"(?<!\\)(?:\\\\)*%.*")
# URL 命令起始：\\url / \\path 单参数、\\href 第一个参数是 URL。内容按字面
# 处理（其中的 % 不是注释、$ / 花括号不计入配平）；支持 URL 内嵌套花括号
#（由 _strip_url_commands 用括号计数找到完整参数）。
_URL_CMD_START_RE = re.compile(r"(\\(?:url|path|href))\s*(?:\[[^\]]*\])?\{")
# verbatim 类环境（含 alltt）：内容按字面处理，不参与配平校验
_VERBATIM_ENV_RE = re.compile(
    r"\\begin\{(verbatim\*?|Verbatim|lstlisting|minted|comment|alltt)\}.*?\\end\{\1\}",
    re.DOTALL,
)
# \\verb / \\lstinline 内联命令：定界符（可为任意非字母非空白字符）之间的
# 内容按字面处理，不参与配平校验；\\verb 不能跨行，故用 [^\\r\\n] 限定。
_VERB_CMD_RE = re.compile(
    r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?([^a-zA-Z\s])[^\r\n]*?\1"
)
# fancyvrb \\Verb：与 \\verb 类似但允许内容跨行，故用 DOTALL。
_VERB_FANCY_RE = re.compile(
    r"\\Verb\*?(?:\[[^\]]*\])?([^a-zA-Z\s]).*?\1",
    re.DOTALL,
)
# \\mintinline{lang}{code}（可带 [options] 与 *）：code 内容按字面处理，
# 可能含嵌套花括号，由 _strip_mintinline 用括号计数移除。
_MINTINLINE_START_RE = re.compile(r"\\mintinline\*?(?:\[[^\]]*\])?\{[^{}]*\}\{")


def _find_matching_brace(value: str, start: int) -> int | None:
    """从 start（'{' 的位置）开始找匹配的 '}'，返回其下标；未闭合返回 None。

    转义的 \\{ / \\} 不参与配平。
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


def _strip_url_commands(value: str) -> str:
    """将 \\url / \\path / \\href 的 URL 参数替换为空（保留命令名）。

    URL 中的 $、{、} 是字面字符，剥离后再统计配平才不会误报；
    \\href 的第二个参数（显示文本）保留，仍参与正常校验。
    """
    result: list[str] = []
    pos = 0
    for m in _URL_CMD_START_RE.finditer(value):
        result.append(value[pos : m.start()])
        close = _find_matching_brace(value, m.end() - 1)
        if close is None:
            # URL 参数未闭合：原样保留，交给配平检查处理
            result.append(value[m.start() :])
            pos = len(value)
            break
        result.append(m.group(1) + "{}")
        pos = close + 1
    result.append(value[pos:])
    return "".join(result)


def _strip_mintinline(value: str) -> str:
    """移除 \\mintinline{lang}{code} 的整个参数块（含嵌套花括号的 code）。"""
    result: list[str] = []
    pos = 0
    for m in _MINTINLINE_START_RE.finditer(value):
        result.append(value[pos : m.start()])
        close = _find_matching_brace(value, m.end() - 1)
        if close is None:
            result.append(value[m.start() :])
            pos = len(value)
            break
        pos = close + 1
    result.append(value[pos:])
    return "".join(result)


def _strip_verbatim(value: str, *, bibtex: bool = False) -> str:
    """剔除 verbatim、内联逐字命令与 URL 内容，避免其中的字面量干扰校验。

    \\verb|{|、\\lstinline|}|、\\mintinline{python}{x={1}}、
    \\url{...%20...} 里的内容都是字面量，全文本扫描会误判为不配平或命中
    控制字符，因此先移除。URL 参数在 LaTeX 与 BibTeX 模式均按字面剥离
    （URL 中的 $、{ 是字面字符）。
    """
    value = _strip_url_commands(value)
    value = _VERBATIM_ENV_RE.sub("", value)
    value = _VERB_CMD_RE.sub("", value)
    value = _VERB_FANCY_RE.sub("", value)
    value = _strip_mintinline(value)
    return value


def check_latex_text(
    value: str, *, bibtex: bool = False, relaxed: bool = False
) -> str | None:
    """校验一段 LaTeX/BibTeX 文本，通过返回 None，失败返回错误消息。

    校验项：
        1. 控制字符（除换行外）—— JSON 未转义反斜杠的静默损坏特征
        2. '[object Object]' 类垃圾占位符
        3. 花括号配平（忽略 \\{ / \\}，跳过注释、verbatim 与 URL 内容）
        4. $ 配平（忽略 \\$，同样跳过）

    bibtex=True 时按 BibTeX 语义处理：% 是字面字符，不当作注释起点。
    relaxed=True 用于分隔符协议（非 JSON）传输路径：反斜杠原样传输，不存在
    JSON 转义风险，因此放行 \\t / \\r 这类合法 LaTeX 空白，且错误归因不再
    提示"把反斜杠双写"。
    """
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"

    # 控制字符 / 垃圾占位符：先剔除 verbatim / URL 内容再扫描，
    # 避免 \\verb|a<TAB>b| 等合法字面内容被误判。
    probe = _strip_verbatim(value, bibtex=bibtex)
    bad_re = _BAD_CONTROL_RELAXED_RE if relaxed else _BAD_CONTROL_RE
    bad = bad_re.search(probe)
    if bad:
        if relaxed:
            return (
                f"LaTeX text contains an illegal control character "
                f"(U+{ord(bad.group()):04X} at position {bad.start()}); "
                "the output appears corrupted — check for raw control "
                "characters in the produced text"
            )
        return (
            f"LaTeX text contains a control character (U+{ord(bad.group()):04X} "
            f"at position {bad.start()}); possible JSON escaping corruption — "
            "in JSON strings, every LaTeX backslash must be written as two "
            "backslash characters"
        )
    if _GARBAGE_RE.search(probe):
        return (
            "LaTeX text contains a garbage placeholder like '[object Object]'; "
            "do not serialize objects into strings — output the actual LaTeX "
            "text content"
        )

    # 配平前剔除注释（BibTeX 的 % 是字面字符，不剥离），避免 % { 等
    # 合法用法被误判为花括号/$ 不配平。
    code = probe
    if not bibtex:
        code = _COMMENT_RE.sub("", code)
    braces = re.sub(r"\\[{}]", "", code)
    if braces.count("{") != braces.count("}"):
        return (
            "LaTeX text has unbalanced braces; check for unmatched { or } — "
            "literal braces should be written as \\{ and \\}"
        )

    dollars = re.sub(r"\\\$", "", code)
    if dollars.count("$") % 2 != 0:
        return (
            "LaTeX text has unbalanced dollar signs; check for unmatched $ — "
            "literal dollar signs should be written as \\$"
        )

    return None


def check_latex_environments(value: str) -> str | None:
    """栈式校验 \\begin{X} 与 \\end{X} 配对，通过返回 None，失败返回错误消息。

    校验前剔除注释、verbatim 与 URL 内容，避免其中的字面 \\end{X} 干扰配对。
    """
    stripped = _COMMENT_RE.sub("", _strip_verbatim(value))
    stack: list[str] = []
    for match in _ENV_RE.finditer(stripped):
        env = match.group(2)
        if match.group(1) == "begin":
            stack.append(env)
        else:
            if not stack or stack[-1] != env:
                return (
                    f"LaTeX text has mismatched \\begin/\\end environment: "
                    f"'\\end{{{env}}}' has no matching '\\begin'"
                )
            stack.pop()
    if stack:
        return f"LaTeX text has unclosed \\begin environments: {stack}"
    return None


def check_latex_text_lite(value: str) -> str | None:
    """轻量内容校验：只查控制字符与 '[object Object]' 类垃圾占位符。

    用于 marker 模块（typo_check / expression_opt / paragraph_translate）
    的 LLM 输出字段。这些字段走 JSON 传输，但 marker 化后常规段落不再含
    反斜杠，花括号 / $ 配平检查可能因残余结构（如 \\~{n}）误报，因此只做
    最小校验：
        - 控制字符：排除 \\t / \\n（合法 LaTeX 空白），\\r 仍拦截
          （JSON 未转义反斜杠的静默损坏特征）
        - 垃圾占位符
    失败消息按"内容损坏"归因，不提示"把反斜杠双写"。
    """
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"

    # 先剔除 verbatim / URL 内容，避免其中的合法字面字符被误判
    probe = _strip_verbatim(value)
    bad = _BAD_CONTROL_LITE_RE.search(probe)
    if bad:
        return (
            f"LaTeX text contains an illegal control character "
            f"(U+{ord(bad.group()):04X} at position {bad.start()}); "
            "the output appears corrupted"
        )
    if _GARBAGE_RE.search(probe):
        return "LaTeX text contains a garbage placeholder like '[object Object]'"
    return None


def validate_latex_field(
    value: str,
    *,
    check_environments: bool = False,
    bibtex: bool = False,
    relaxed: bool = False,
) -> str:
    """Pydantic 校验器回调：校验失败抛出 ValueError（带错误消息）。

    用法一（Annotated 类型）：字段声明为 LatexText。
    用法二（field_validator）：在模型内
        @field_validator("latex")
        @classmethod
        def _check(cls, value: str) -> str:
            return validate_latex_field(value)
    bibtex=True 时按 BibTeX 语义校验（% 为字面字符），用于 BibEntry 字段。
    relaxed=True 用于分隔符协议（非 JSON）路径，放行 \\t / \\r 并调整错误归因。
    """
    error = check_latex_text(value, bibtex=bibtex, relaxed=relaxed)
    if error:
        raise ValueError(error)
    if check_environments:
        env_error = check_latex_environments(value)
        if env_error:
            raise ValueError(env_error)
    return value


def validate_latex_field_lite(value: str) -> str:
    """Pydantic 校验器回调（轻量）：校验失败抛出 ValueError。"""
    error = check_latex_text_lite(value)
    if error:
        raise ValueError(error)
    return value


# Annotated 类型别名：声明 Pydantic 字段时直接复用
#     class CodeReviewSuggestion(BaseModel):
#         suggested: LatexText
LatexText = Annotated[str, AfterValidator(validate_latex_field)]

# 轻量 Annotated 类型别名：marker 模块输出字段（corrected/optimized/translated）复用
#     class TypoCheckResult(BaseModel):
#         corrected: LatexTextLite
LatexTextLite = Annotated[str, AfterValidator(validate_latex_field_lite)]

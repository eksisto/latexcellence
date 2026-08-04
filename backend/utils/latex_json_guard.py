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

    校验模式：
        - 默认（LaTeX）：% 是注释起点（\\% 除外），注释内容不参与配平；
          \\url / \\href / \\path 的 URL 参数按字面处理。
        - bibtex=True：% 是字面字符（BibTeX 字段值中的 % 不是注释），不剥离，
          避免 percent-encoding URL（如 https://x/y%20z）被误判为花括号不配平。
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
# LLM 把对象序列化成字符串时产生的垃圾占位符
_GARBAGE_RE = re.compile(r"\[object [A-Za-z]+\]")
# 环境声明：\\begin{X} / \\end{X}
_ENV_RE = re.compile(r"\\(begin|end)\{([^}]*)\}")
# 注释：偶数个反斜杠前的 % 才是注释起点（\\% 不是；\\\\% 中 \\ 换行后
# % 开始注释）。注释内容（可能含孤立花括号/$）不参与配平校验。
_COMMENT_RE = re.compile(r"(?<!\\)(?:\\\\)*%.*")
# URL 命令：\\url / \\path 单参数、\\href 第一个参数是 URL，内容按字面
# 处理（其中的 % 不是注释、花括号不计入配平）；不支持 URL 内嵌套花括号。
_URL_CMD_RE = re.compile(
    r"(\\(?:url|path|href))\s*(?:\[[^\]]*\])?\{[^{}]*\}"
)
# verbatim 类环境：内容按字面处理，不参与配平校验
_VERBATIM_ENV_RE = re.compile(
    r"\\begin\{(verbatim\*?|Verbatim|lstlisting|minted|comment)\}.*?\\end\{\1\}",
    re.DOTALL,
)
# \\verb / \\lstinline 内联命令：定界符（可为任意非字母非空白字符）之间的
# 内容按字面处理，不参与配平校验；\\verb 不能跨行，故用 [^\\r\\n] 限定。
_VERB_CMD_RE = re.compile(
    r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?([^a-zA-Z\s])[^\r\n]*?\1"
)


def _strip_verbatim(value: str, *, bibtex: bool = False) -> str:
    """剔除 verbatim 与 URL 内容，避免其中的花括号/$/控制字符干扰校验。

    \\verb|{|、\\lstinline|}|、\\url{...%20...} 里的内容都是字面量，
    全文本扫描会误判为不配平或命中控制字符，因此先移除。
    BibTeX 模式（bibtex=True）不剥离 URL 参数：% 本来就是字面字符，
    URL 若含嵌套花括号也会被完整保留。
    """
    if not bibtex:
        value = _URL_CMD_RE.sub(r"\g<1>{}", value)
    value = _VERBATIM_ENV_RE.sub("", value)
    value = _VERB_CMD_RE.sub("", value)
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
                "the output appears corrupted"
            )
        return (
            f"LaTeX text contains a control character (U+{ord(bad.group()):04X} "
            f"at position {bad.start()}); possible JSON escaping corruption — "
            "in JSON strings, every LaTeX backslash must be written as two "
            "backslash characters"
        )
    if _GARBAGE_RE.search(probe):
        return "LaTeX text contains a garbage placeholder like '[object Object]'"

    # 配平前剔除注释（BibTeX 的 % 是字面字符，不剥离），避免 % { 等
    # 合法用法被误判为花括号/$ 不配平。
    code = probe
    if not bibtex:
        code = _COMMENT_RE.sub("", code)
    braces = re.sub(r"\\[{}]", "", code)
    if braces.count("{") != braces.count("}"):
        return "LaTeX text has unbalanced braces"

    dollars = re.sub(r"\\\$", "", code)
    if dollars.count("$") % 2 != 0:
        return "LaTeX text has unbalanced dollar signs"

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


# Annotated 类型别名：声明 Pydantic 字段时直接复用
#     class CodeReviewSuggestion(BaseModel):
#         suggested: LatexText
LatexText = Annotated[str, AfterValidator(validate_latex_field)]

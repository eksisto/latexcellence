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

    字面内容（不参与配平 / 控制字符扫描）：\\verb / \\Verb / \\lstinline /
    \\mintinline、逐字环境（verbatim / Verbatim / lstlisting / minted /
    comment / alltt）与 \\url / \\path / \\href 的 URL 参数。字面区间识别
    统一来自 latex_literals.find_literal_spans（与 latex_parser 共享同一策略；
    \\href 第二参数保留校验）。

    换行损坏检测边界：JSON 路径（relaxed=False）下值首部的 "换行 + 小写字母"
    是 \\n 开头命令（\\nabla / \\newcommand 等）被 JSON 转义吞掉反斜杠的
    损坏特征。换行本身是合法 LaTeX 空白，guard 无法仅凭结果完整区分损坏与
    合法换行，因此该启发式只拦截最典型的特征；完整方案是让原始 LaTeX 脱离
    JSON（分隔符协议），多行内容走 relaxed=True 宽松校验。

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

from backend.utils.latex_literals import blank_literal_spans

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


def _strip_verbatim(value: str) -> str:
    """剔除 verbatim、内联逐字命令与 URL 参数，避免其中的字面量干扰校验。

    \\verb|{|、\\lstinline|}|、\\mintinline{python}{x={1}}、
    \\url{...%20...} 里的内容都是字面量，全文本扫描会误判为不配平或命中
    控制字符，因此先移除。识别策略与 latex_parser 共享
    （见 latex_literals.find_literal_spans）。
    """
    return blank_literal_spans(value)


def strip_latex_comments(text: str, *, bibtex: bool = False) -> str:
    """移除未转义的 % 注释（% 至行尾），保留 \\% 等转义百分号。

    反斜杠奇偶语义与 check_latex_text 完全一致：偶数个反斜杠前的 % 才是
    注释起点（\\% 不是；\\\\% 中 \\\\ 换行后 % 开始注释）。BibTeX 模式
    （bibtex=True）下 % 是字面字符，不做剥离。
    """
    if bibtex:
        return text
    return _COMMENT_RE.sub("", text)


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
    probe = _strip_verbatim(value)
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
    # JSON 路径（relaxed=False）的换行损坏边界：值首部 "换行 + 小写字母"
    # 是 \\n 开头命令（\\nabla / \\newcommand 等）被 JSON 转义吞掉反斜杠的
    # 特征。换行本身是合法空白，无法仅凭结果完整区分，只拦最典型特征。
    if not relaxed and re.match(r"\n[a-z]", probe):
        return (
            "LaTeX text starts with a newline followed by a lowercase letter; "
            "this is the damage signature of a JSON-escaped command like "
            "\\nabla or \\newcommand whose backslash was consumed by JSON "
            "parsing — in JSON strings, every LaTeX backslash must be written "
            "as two backslash characters"
        )
    if _GARBAGE_RE.search(probe):
        return (
            "LaTeX text contains a garbage placeholder like '[object Object]'; "
            "do not serialize objects into strings — output the actual LaTeX "
            "text content"
        )

    # 配平前剔除注释（BibTeX 的 % 是字面字符，不剥离），避免 % { 等
    # 合法用法被误判为花括号/$ 不配平。
    code = strip_latex_comments(probe, bibtex=bibtex)
    # 栈式扫描定位第一个不配平的花括号，返回行号与上下文，
    # 便于 LLM 直接定位修复（仅计数无法告诉模型错在哪一行）。
    stack: list[int] = []
    i = 0
    code_len = len(code)
    while i < code_len:
        ch = code[i]
        if ch == "\\" and i + 1 < code_len and code[i + 1] in "{}":
            i += 2  # 跳过 \{ / \}
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}":
            if not stack:
                return _brace_error(code, i, "unmatched }")
            stack.pop()
        i += 1
    if stack:
        return _brace_error(code, stack[0], "unclosed {")

    dollars = re.sub(r"\\\$", "", code)
    if dollars.count("$") % 2 != 0:
        return (
            "LaTeX text has unbalanced dollar signs; check for unmatched $ — "
            "literal dollar signs should be written as \\$"
        )

    return None


def _brace_error(code: str, pos: int, kind: str) -> str:
    """把花括号不配平位置格式化为带行号与上下文的错误消息。"""
    line = code.count("\n", 0, pos) + 1
    start = max(0, pos - 40)
    end = min(len(code), pos + 40)
    snippet = " ".join(code[start:end].split())
    return (
        "LaTeX text has unbalanced braces; check for unmatched { or } — "
        "this is a LaTeX source validation error, not a JSON parsing error. "
        "Do not replace structural braces in commands, environments, or "
        "key-value arguments with \\{ or \\}; only braces that should appear "
        "as printed text may be escaped that way. "
        f"First issue: {kind} at position {pos} (line {line}) near "
        f'"{snippet}"'
    )


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


def check_latex_corruption(value: str) -> str | None:
    """仅拦截内容损坏特征的宽松校验（编辑器保存等非 JSON 直传路径）。

    与 check_latex_text(relaxed=True) 同一控制字符口径（放行 \\t / \\r 等
    合法 LaTeX 空白，拦截 \\b / \\f 等静默损坏特征），并拦截
    '[object Object]' 类垃圾占位符；但**不做**花括号 / $ 配平与环境检查——
    编辑器保存的是用户手写源码的任意中间态（半行命令、未闭合公式是常态），
    配平不合法不应阻止落盘。

    校验前剔除 verbatim / URL 字面内容，避免其中的字面字符被误判。
    """
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"

    probe = _strip_verbatim(value)
    bad = _BAD_CONTROL_RELAXED_RE.search(probe)
    if bad:
        return (
            f"LaTeX text contains an illegal control character "
            f"(U+{ord(bad.group()):04X} at position {bad.start()}); "
            "the content appears corrupted — check for raw control "
            "characters in the text"
        )
    if _GARBAGE_RE.search(probe):
        return (
            "LaTeX text contains a garbage placeholder like '[object Object]'; "
            "do not serialize objects into strings — save the actual LaTeX "
            "text content"
        )
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

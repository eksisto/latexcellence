"""
LaTeX 文本提取与位置映射模块。

职责：
    1. 将 LaTeX 源码转为纯文本（剥离命令、数学公式；注释替换为 marker）
    2. marker（标记符）+ 反向还原策略：先替换再还原，确保精确写回
    3. 段落级提取：按空行分割，记录行号范围

核心设计——反向映射策略：
    - 剥离阶段：将 LaTeX 命令/公式/环境替换为唯一 marker（如 __CMD_0000__），
      记录 marker → 原始字符串的映射表
    - LLM 处理阶段：LLM 只看到 marker + 纯文本，不修改任何 marker
    - 还原阶段：将 LLM 输出中的 marker 反向替换回原始 LaTeX 内容

必须保护的 LaTeX 环境（整体替换为 marker，LLM 不可见）：
    - verbatim, lstlisting, table, tabular, tikzpicture
    - equation, equation*, align, align*, eqnarray, gather, multline
    - 注：itemize/enumerate/description 等普通环境仅替换 \\begin/\\end 命令，
      环境内的文本内容交给 LLM 处理

边界情况处理：
    - 嵌套花括号（如 \\textbf{\\textit{hello}}）：通过括号深度计数精确匹配
    - \\newcommand / \\renewcommand 自定义命令：作为普通命令整体替换
    - 转义字符（\\& \\% \\_ \\# 等）：替换为 marker
    - \\include / \\input 多层嵌套：由 project_manager 处理，本模块不涉及
    - 中英文混合（UTF-8）：正常按字符处理
    - \\verb / \\verb* / \\Verb / \\Verb* / \\lstinline / \\lstinline* /
      \\mintinline 内联逐字：整体替换为 marker（支持数字等任意非字母定界符、
      \\lstinline 可选参数 [options]、fancyvrb \\Verb 跨行内容）
    - \\url / \\path / \\href 参数：在注释替换之前整体替换为 marker，
      URL 中的 %20 不会被误当作注释起点
    - 控制符号（\\? \\' \\" \\7 \\~{n}、\\ + 换行、未配对 \\[ \\( 等）：替换为 marker
    - 行内数学 $...$ 非贪婪匹配；不成对的 $ 保持原文（合法 LaTeX 中 $ 必须写作 \\$，
      由转义字符规则处理，如 \\$500 不会进入数学匹配）
"""

import re

from backend.core.logger import get_logger
from backend.utils.latex_literals import (
    URL_CMD_START_RE,
    find_literal_spans,
    find_matching_brace,
)

# ── protected environments: entire block → single marker ──────────────────

# 仅能含字面 % 的逐字类环境（必须在注释替换之前保护）。
# 与 latex_json_guard 的逐字环境列表保持一致（含 fancyvrb Verbatim 等）。
_VERBATIM_ENVS = [
    "verbatim",
    "verbatim*",
    "Verbatim",
    "lstlisting",
    "minted",
    "comment",
    "alltt",
]

_PROTECTED_ENVS = [
    "verbatim",
    "lstlisting",
    "table",
    "table*",
    "tabular",
    "tabular*",
    "longtable",
    "tikzpicture",
    "equation",
    "equation*",
    "align",
    "align*",
    "alignat",
    "alignat*",
    "eqnarray",
    "eqnarray*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
    "subequations",
]

# ── escaped single characters (not commands with args) ────────────────────

_ESCAPED_CHARS = set("#$%&_{}")

# ── symbols that look like commands but are single tokens ─────────────────
# \~ / \^ 等控制符号不再列在这里，由 _replace_commands 的通用控制符号规则
# 统一处理（覆盖 \? \' \" \7 \~{n} 等，并能吸收紧随的 {arg}）

_SINGLE_TOKEN_COMMANDS = {
    r"\,",
    r"\:",
    r"\;",
    r"\!",
    r"\-",
    r"\\",  # line break
    r"\ ",  # explicit space
}

# 内置命令名（无参数，无需读取 {arg}）
_NO_ARG_COMMANDS = {
    "LaTeX",
    "LaTeXe",
    "TeX",
    "today",
    "newpage",
    "clearpage",
    "pagebreak",
    "linebreak",
    "newline",
    "tableofcontents",
    "listoffigures",
    "listoftables",
    "maketitle",
    "appendix",
    "hline",
    "cline",
    "toprule",
    "midrule",
    "bottomrule",
    "hfill",
    "vfill",
    "dotfill",
    "hrulefill",
    "smallskip",
    "medskip",
    "bigskip",
    "noindent",
    "indent",
    "centering",
    "raggedright",
    "raggedleft",
}

# 匹配 LaTeX marker（如 __CMD_0000__、__MATH_0001__）
MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4,}__")
# 提取 marker 内的 counter（创建序号）：__{PREFIX}_{counter:04d}__，
# 用于还原时按创建顺序排序（嵌套的 marker 先创建、counter 较小）。
_MARKER_COUNTER_RE = re.compile(r"__[A-Z]+_(\d+)__")


def _marker_restore_key(item: tuple[str, str]) -> tuple[int, str]:
    """marker 还原顺序键：counter 降序（外层 marker 先还原）。

    counter 越大表示创建越晚；嵌套时（如 \\url 在 \\footnote{\\url{...}} 里、
    \\verb 在 \\begin{verbatim}... 里）外层 marker 一定后创建，先还原外层、
    让被包裹的内层 marker 出现在文本中，随后内层才能被还原。marker 是定宽
    __{PREFIX}_{counter:04d}__，按 counter 降序同时避免短 marker 误匹配长
    marker 前缀的问题（如 __CMD_1__ vs __CMD_1001__）。非标准 marker
    回退为长度降序，保持防御性。
    """
    marker, _ = item
    match = _MARKER_COUNTER_RE.match(marker)
    if match:
        return (-int(match.group(1)), marker)
    return (-len(marker), marker)


class LatexParser:
    """
    LaTeX 源码 ↔ 纯文本 转换器。

    用法：
        parser = LatexParser()
        stripped, marker_map = parser.strip_to_plain_text(source)
        # ... LLM 处理 stripped ...
        restored = parser.restore_from_markers(processed, marker_map)
    """

    def __init__(self):
        self._counter = 0
        self._marker_map: dict[str, str] = {}
        self._logger = get_logger("latex_parser")

    # ── public API ────────────────────────────────────────────────────────

    def strip_to_plain_text(self, source: str) -> tuple[str, dict[str, str]]:
        """
        将 LaTeX 源码转换为带 marker 的纯文本。

        流程：
            1. 替换内联逐字（\\verb / \\Verb / \\lstinline / \\mintinline，
               含数字定界符、可选参数与跨行 \\Verb）
            2. 替换 \\url / \\path / \\href 参数（URL 中的 % 是字面字符）
            3. 替换逐字类环境（verbatim / Verbatim / lstlisting 等，可含字面 %）
            4. 将注释替换为 marker（原子化捕获注释内所有内容）
            5. 替换受保护环境（equation, table, tikzpicture 等）
            6. 替换展示数学模式（$$...$$, \\[...\\]）
            7. 替换行内数学模式（$...$, \\(...\\)）
            8. 替换剩余 LaTeX 命令
            9. 规范化空白

        行内数学 $...$ 采用非贪婪匹配，不因空格收紧匹配条件；
        合法的美元符号是 \\$（由转义字符规则处理），裸 $ 不配对时保持原文。

        注意：注释替换（第 4 步）在环境/数学/命令（第 5-8 步）之前，
        确保注释内的 LaTeX 结构不被单独提取。内联逐字 / URL 参数 / 逐字类
        环境（第 1-3 步）在注释之前，因其内部可含字面 %。

        参数：
            source: LaTeX 源码

        返回：
            (带 marker 的纯文本, marker → 原始内容的映射表)
        """
        self._counter = 0
        self._marker_map.clear()

        text = source

        text = self._replace_verb_inline(text)
        text = self._replace_url_args(text)
        text = self._replace_protected_envs(text, _VERBATIM_ENVS)
        text = self._replace_comments(text)
        text = self._replace_protected_envs(text)
        text = self._replace_display_math(text)
        text = self._replace_inline_math(text)
        text = self._replace_commands(text)
        text = self._normalize_whitespace(text)

        return text, dict(self._marker_map)

    def restore_from_markers(
        self, processed_text: str, marker_map: dict[str, str]
    ) -> str:
        """
        将 LLM 处理后的文本中的 marker 还原为原始 LaTeX 内容。

        按创建顺序（counter）降序还原：嵌套的 marker（如 \\url 在
        \\footnote{\\url{...}} 里、\\verb 在 \\begin{verbatim}... 里）总是
        先创建、被后创建的外层 marker 包裹。先还原外层，内层 marker 才会
        出现在文本中，随后才能被正确还原；否则内层 marker 会残留为字面文本。

        参数：
            processed_text: LLM 返回的文本（含 marker）
            marker_map: strip_to_plain_text 返回的映射表

        返回：
            还原后的完整 LaTeX 源码
        """
        result = processed_text
        # counter 降序 = 外层（后创建）先还原。marker 定宽，见 _marker_restore_key
        for marker, original in sorted(marker_map.items(), key=_marker_restore_key):
            result = result.replace(marker, original)
        return result

    # ── comment stripping ─────────────────────────────────────────────────

    def _replace_comments(self, text: str) -> str:
        """
        将 LaTeX 注释（% 至行尾）替换为 marker。
        保留被转义的百分号（\\%）。
        """
        result_lines = []
        for line in text.split("\n"):
            in_comment = False
            parts: list[str] = []
            for i, ch in enumerate(line):
                if ch == "%" and not in_comment:
                    # 检查前方反斜杠数量，偶数 = 注释开始
                    bs = 0
                    j = i - 1
                    while j >= 0 and line[j] == "\\":
                        bs += 1
                        j -= 1
                    if bs % 2 == 0:
                        in_comment = True
                        # 注释前的内容保留；注释内容替换为 marker
                        marker = self._gen_marker("COM")
                        self._marker_map[marker] = line[i:]
                        parts.append(marker)
                        continue
                if not in_comment:
                    parts.append(ch)
            result_lines.append("".join(parts))
        return "\n".join(result_lines)

    # ── inline verb ───────────────────────────────────────────────────────

    def _replace_verb_inline(self, text: str) -> str:
        """
        替换内联逐字命令（识别策略与 latex_json_guard 共享）：
            \\verb|...| / \\verb*|...|            任意单字符分隔符（含数字）
            \\Verb|...| / \\Verb*|...|            fancyvrb，内容可跨行
            \\lstinline|...| / \\lstinline*|...|  分隔符形式（可带 [options]）
            \\lstinline{...} / \\lstinline*{...}  花括号形式（可含嵌套花括号）
            \\mintinline{lang}{code}              code 可含嵌套花括号
        整体替换为一个 VERB marker，逐字内容 LLM 不可见、不可修改。
        """
        spans = find_literal_spans(text, kinds={"verb", "verb_fancy", "mintinline"})
        result: list[str] = []
        last_end = 0
        for span in spans:
            marker = self._gen_marker("VERB")
            self._marker_map[marker] = text[span.start : span.end]
            result.append(text[last_end : span.start])
            result.append(marker)
            last_end = span.end
        result.append(text[last_end:])
        return "".join(result)

    def _replace_url_args(self, text: str) -> str:
        """
        将 \\url / \\path / \\href 的完整命令（含可选参数与全部花括号参数）
        整体替换为 CMD marker。

        必须在注释替换之前执行：URL 参数里的 %（如 %20）是字面字符，若先做
        注释替换会把 %20b} visible $x$ 之类的内容当成注释吞掉。
        """
        result: list[str] = []
        pos = 0
        for m in URL_CMD_START_RE.finditer(text):
            result.append(text[pos : m.start()])
            k = m.end()
            # 第一个参数（URL）：括号计数支持嵌套花括号与字面 \\{ / \\}
            close = find_matching_brace(text, k - 1)
            if close is None:
                # URL 参数未闭合：整体原样保留，交给命令规则处理
                result.append(text[m.start() :])
                pos = len(text)
                break
            k = close + 1
            # 继续吸收后续参数（如 \\href{url}{display} 的显示文本），
            # 与 _replace_commands 的普通命令参数读取保持一致
            k = self._skip_whitespace(text, k)
            while k < len(text) and text[k] in "[{":
                if text[k] == "[":
                    k = self._find_matching(text, k, "[", "]") + 1
                else:
                    close = find_matching_brace(text, k)
                    if close is None:
                        k = len(text)
                        break
                    k = close + 1
                k = self._skip_whitespace(text, k)

            marker = self._gen_marker("CMD")
            self._marker_map[marker] = text[m.start() : k]
            result.append(marker)
            pos = k
        result.append(text[pos:])
        return "".join(result)

    # ── protected environments ────────────────────────────────────────────

    def _replace_protected_envs(self, text: str, envs: list[str] | None = None) -> str:
        """
        将受保护环境（table / equation / tikzpicture 等）
        整体替换为单个 marker。环境内的内容 LLM 不可见。

        参数：
            envs: 要替换的环境名列表。默认使用 _PROTECTED_ENVS。
        """
        if envs is None:
            envs = _PROTECTED_ENVS
        for env in envs:
            escaped = re.escape(env)
            pattern = re.compile(
                r"(\\begin\{" + escaped + r"\})(.*?)(\\end\{" + escaped + r"\})",
                re.DOTALL,
            )

            def make_replacer(_env=env):
                def replacer(m):
                    marker = self._gen_marker("ENV")
                    self._marker_map[marker] = m.group(0)
                    return f"\n{marker}\n"

                return replacer

            text = pattern.sub(make_replacer(), text)
        return text

    # ── display math ──────────────────────────────────────────────────────

    def _replace_display_math(self, text: str) -> str:
        """
        替换展示数学模式：
            $$ ... $$  → marker
            \\[ ... \\] → marker
        """

        # $$ ... $$ (non-greedy, DOTALL for multi-line)
        def dollar_replacer(m):
            marker = self._gen_marker("MATH")
            self._marker_map[marker] = m.group(0)
            return f"\n{marker}\n"

        text = re.sub(r"\$\$(.+?)\$\$", dollar_replacer, text, flags=re.DOTALL)

        # \[ ... \]
        def bracket_replacer(m):
            marker = self._gen_marker("MATH")
            self._marker_map[marker] = m.group(0)
            return f"\n{marker}\n"

        text = re.sub(r"\\\[(.+?)\\\]", bracket_replacer, text, flags=re.DOTALL)

        return text

    # ── inline math ───────────────────────────────────────────────────────

    def _replace_inline_math(self, text: str) -> str:
        """
        替换行内数学模式：
            $...$   → marker（排除 $$）
            \\(...\\) → marker
        """

        # $...$ — 使用负向断言排除 $$；非贪婪匹配；
        # 被反斜杠转义的 \$（如 \$500）不参与数学匹配，交给转义字符规则处理
        def dollar_replacer(m):
            marker = self._gen_marker("MATH")
            self._marker_map[marker] = m.group(0)
            return marker

        # 匹配 $...$：前后都不能是 $（排除 $$）且不能是 \$（排除转义美元）
        text = re.sub(
            r"(?<!\$)(?<!\\)\$(?!\$)(.+?)(?<!\$)(?<!\\)\$(?!\$)",
            dollar_replacer,
            text,
        )

        # \(...\)
        def paren_replacer(m):
            marker = self._gen_marker("MATH")
            self._marker_map[marker] = m.group(0)
            return marker

        text = re.sub(r"\\\((.+?)\\\)", paren_replacer, text)
        return text

    # ── command replacement ───────────────────────────────────────────────

    def _replace_commands(self, text: str) -> str:
        """
        替换所有剩余的 LaTeX 命令为 marker。

        字符级扫描，通过括号深度计数处理嵌套参数。
        支持：
            - \\command                    无参数
            - \\command{arg}              单参数
            - \\command[opt]{arg}         可选 + 必选
            - \\command{arg1}{arg2}       多参数
            - \\command\\command          连续命令
            - \\begin{name} / \\end{name} 普通环境标记
        """
        result: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            if ch != "\\":
                result.append(ch)
                i += 1
                continue

            # ── 转义字符 ──
            if i + 1 < n and text[i + 1] in _ESCAPED_CHARS:
                token_end = i + 2
                # 吸收紧随的 {arg}（如 \&{...}、\%{...}），避免花括号泄漏
                k = self._skip_whitespace(text, token_end)
                if k < n and text[k] == "{":
                    token_end = self._find_matching_brace(text, k + 1) + 1
                token = text[i:token_end]
                marker = self._gen_marker("CMD")
                self._marker_map[marker] = token
                result.append(marker)
                i = token_end
                continue

            # ── 单 token 命令（\, \; \: 等）──
            single_matched = False
            for tok in _SINGLE_TOKEN_COMMANDS:
                if text.startswith(tok, i):
                    marker = self._gen_marker("CMD")
                    self._marker_map[marker] = tok
                    result.append(marker)
                    i += len(tok)
                    single_matched = True
                    break
            if single_matched:
                continue

            # ── 通用控制符号：\ 后跟单个非字母字符（\? \' \" \7 \~ \^ 等）──
            # 覆盖未配对 \[ \（ 以及 \ + 换行 / EOF 处的孤立 \
            if i + 1 >= n or not (text[i + 1].isalpha() or text[i + 1] in "*@"):
                token_end = i + 2 if i + 1 < n else i + 1
                # 吸收紧随的 {arg}（如 \~{n} → 单一 marker）
                k = self._skip_whitespace(text, token_end)
                if k < n and text[k] == "{":
                    token_end = self._find_matching_brace(text, k + 1) + 1
                token = text[i:token_end]
                marker = self._gen_marker("CMD")
                self._marker_map[marker] = token
                result.append(marker)
                i = token_end
                continue

            # ── 读取命令名 ──
            j = i + 1  # 跳过 \
            while j < n and (text[j].isalpha() or text[j] in "*@"):
                j += 1

            cmd_name = text[i + 1 : j]  # 不含反斜杠

            # ── 无参数命令（\LaTeX, \today, \hline 等）──
            if (
                cmd_name in _NO_ARG_COMMANDS
                or (len(cmd_name) == 0 and j > i + 1)
                or cmd_name in _PROTECTED_ENVS
            ):
                # 检查后面是否跟了 { 参数（用户可能给无参命令传参，如极少情况）
                k = j
                while k < n and text[k] in " \t\n":
                    k += 1
                if k >= n or text[k] != "{":
                    # 确实无参数
                    token = text[i:j]
                    marker = self._gen_marker("CMD")
                    self._marker_map[marker] = token
                    result.append(marker)
                    i = j
                    continue

            # ── 开头无命令名（如行首 \\）──
            if cmd_name == "":
                # 可能是 \\ (已由单 token 处理), 或者单独的 \
                # 如果不是已知的单 token，直接跳过
                result.append(ch)
                i += 1
                continue

            # ── 普通命令：读取参数 ──
            cmd_start = i
            pos = j

            # 跳过命令名后的空白
            pos = self._skip_whitespace(text, pos)

            # 循环读取可选方括号 [opt] 和必选花括号 {arg}，
            # 支持 \cmd{arg1}[opt]{arg2}{arg3} 等模式
            had_arg = False
            while pos < n and text[pos] in "[{":
                if text[pos] == "[":
                    pos = self._find_matching(text, pos, "[", "]") + 1
                    had_arg = True
                else:
                    pos = self._find_matching_brace(text, pos + 1) + 1
                    had_arg = True
                pos = self._skip_whitespace(text, pos)

            if had_arg:
                cmd_end = pos
            else:
                # 没有花括号参数，命令名结尾就是命令结尾
                cmd_end = j

            token = text[cmd_start:cmd_end]
            if token.strip():
                marker = self._gen_marker("CMD")
                self._marker_map[marker] = token
                result.append(marker)
            i = cmd_end

        return "".join(result)

    # ── brace matching helpers ────────────────────────────────────────────

    def _find_matching_brace(self, text: str, start: int) -> int:
        """
        查找与 position(start-1) 处的 { 匹配的 }。
        正确处理嵌套。

        参数：
            text: 文本
            start: { 之后的位置

        返回：
            } 的位置索引
        """
        depth = 1
        i = start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        # 未闭合括号，返回末尾
        return len(text)

    def _find_matching(self, text: str, start: int, open_ch: str, close_ch: str) -> int:
        """
        查找与 position(start) 处的 open_ch 匹配的 close_ch。
        支持嵌套但不跨类型（如 [[...]] 内部）匹配。

        参数：
            text: 文本
            start: open_ch 的位置
            open_ch: 开始字符
            close_ch: 结束字符

        返回：
            close_ch 的位置索引
        """
        depth = 1
        i = start + 1
        while i < len(text):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(text)

    # ── whitespace ────────────────────────────────────────────────────────

    @staticmethod
    def _skip_whitespace(text: str, pos: int) -> int:
        """跳过空白字符，返回下一个非空白字符的位置。"""
        while pos < len(text) and text[pos] in " \t\n":
            pos += 1
        return pos

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """
        规范化空白：
            - 每行 strip 首尾空白（但不移除整行空白，保留段落分隔）
            - 连续 3+ 空行压缩为 2 空行
        """
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            lines.append(stripped)
        # 压缩连续空行最多 2 个
        result = []
        blank_count = 0
        for line in lines:
            if line == "":
                if blank_count < 2:
                    result.append(line)
                blank_count += 1
            else:
                result.append(line)
                blank_count = 0
        return "\n".join(result)

    # ── marker generation ─────────────────────────────────────────────────

    def _gen_marker(self, prefix: str = "CMD") -> str:
        """生成全局唯一的 marker。"""
        marker = f"__{prefix}_{self._counter:04d}__"
        self._counter += 1
        return marker

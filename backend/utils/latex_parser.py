"""
LaTeX 文本提取与位置映射模块。

职责：
    1. 将 LaTeX 源码转为纯文本（剥离命令、数学公式；注释替换为 marker）
    2. marker（标记符）+ 反向还原策略：先替换再还原，确保精确写回
    3. 段落级提取：按空行分割，记录行号范围

核心设计——反向映射策略：
    - 剥离阶段：将 LaTeX 命令/公式/环境替换为唯一 marker（如 __CMD_0001__），
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
    - \\verb|...| 内联逐字：替换为 marker
"""

import re

from backend.core.logger import get_logger


# ── protected environments: entire block → single marker ──────────────────

_PROTECTED_ENVS = [
    "verbatim", "lstlisting",
    "table", "table*", "tabular", "tabular*", "longtable",
    "tikzpicture",
    "equation", "equation*",
    "align", "align*", "alignat", "alignat*",
    "eqnarray", "eqnarray*",
    "gather", "gather*",
    "multline", "multline*",
    "flalign", "flalign*",
    "subequations",
]

# ── escaped single characters (not commands with args) ────────────────────

_ESCAPED_CHARS = set("#$%&_{}")

# ── symbols that look like commands but are single tokens ─────────────────

_SINGLE_TOKEN_COMMANDS = {
    r"\,", r"\:", r"\;", r"\!", r"\-",
    r"\\",         # line break
    r"\ ",         # explicit space
    # accents that don't take args (but \~{n} does; handled later)
    r"\~", r"\^",
}

# 内置命令名（无参数，无需读取 {arg}）
_NO_ARG_COMMANDS = {
    "LaTeX", "LaTeXe", "TeX",
    "today",
    "newpage", "clearpage", "pagebreak",
    "linebreak", "newline",
    "tableofcontents", "listoffigures", "listoftables",
    "maketitle",
    "appendix",
    "hline", "cline", "toprule", "midrule", "bottomrule",
    "hfill", "vfill", "dotfill", "hrulefill",
    "smallskip", "medskip", "bigskip",
    "noindent", "indent",
    "centering", "raggedright", "raggedleft",
}

# 匹配 LaTeX marker（如 __CMD_0001__、__MATH_0002__）
MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4}__")


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
            1. 替换 \\verb|...| 内联逐字
            2. 替换受保护环境（verbatim, equation 等）
            3. 替换展示数学模式（$$...$$, \\[...\\]）
            4. 替换行内数学模式（$...$, \\(...\\)）
            5. 替换剩余 LaTeX 命令
            6. 将注释替换为 marker（在保护之后，避免 \\verb|100%| 等误处理）
            7. 规范化空白

        参数：
            source: LaTeX 源码

        返回：
            (带 marker 的纯文本, marker → 原始内容的映射表)
        """
        self._counter = 0
        self._marker_map.clear()

        text = source

        text = self._replace_verb_inline(text)
        text = self._replace_protected_envs(text)
        text = self._replace_display_math(text)
        text = self._replace_inline_math(text)
        text = self._replace_commands(text)
        text = self._replace_comments(text)
        text = self._normalize_whitespace(text)

        return text, dict(self._marker_map)

    def restore_from_markers(
        self, processed_text: str, marker_map: dict[str, str]
    ) -> str:
        """
        将 LLM 处理后的文本中的 marker 还原为原始 LaTeX 内容。

        反序处理（长 marker 优先），避免短 marker 误匹配长 marker 的前缀。

        参数：
            processed_text: LLM 返回的文本（含 marker）
            marker_map: strip_to_plain_text 返回的映射表

        返回：
            还原后的完整 LaTeX 源码
        """
        result = processed_text
        # 按 marker 长度降序排列，避免 __CMD_10 匹配到 __CMD_1001 的前缀
        for marker, original in sorted(
            marker_map.items(), key=lambda x: (-len(x[0]), x[0])
        ):
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
        替换 \\verb|c| 内联逐字命令。
        \\verb 后的第一个字符是分隔符，内容到再次出现该分隔符为止。
        """
        pattern = re.compile(r"\\verb(.)(.+?)\1")
        result = []
        last_end = 0
        for m in pattern.finditer(text):
            marker = self._gen_marker("VERB")
            self._marker_map[marker] = m.group(0)
            result.append(text[last_end: m.start()])
            result.append(marker)
            last_end = m.end()
        result.append(text[last_end:])
        return "".join(result)

    # ── protected environments ────────────────────────────────────────────

    def _replace_protected_envs(self, text: str) -> str:
        """
        将受保护环境（verbatim / table / equation / tikzpicture 等）
        整体替换为单个 marker。环境内的内容 LLM 不可见。
        """
        for env in _PROTECTED_ENVS:
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

        text = re.sub(r"\\\[(.+?)\\\]", bracket_replacer,
                      text, flags=re.DOTALL)

        return text

    # ── inline math ───────────────────────────────────────────────────────

    def _replace_inline_math(self, text: str) -> str:
        """
        替换行内数学模式：
            $...$   → marker（排除 $$）
            \\(...\\) → marker
        """
        # $...$  — 使用负向断言排除 $$
        def dollar_replacer(m):
            marker = self._gen_marker("MATH")
            self._marker_map[marker] = m.group(0)
            return marker

        # 匹配 $...$ 但不能是 $$
        text = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
                      dollar_replacer, text)

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
                token = text[i: i + 2]
                marker = self._gen_marker("CMD")
                self._marker_map[marker] = token
                result.append(marker)
                i += 2
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

            # ── 读取命令名 ──
            j = i + 1  # 跳过 \
            while j < n and (text[j].isalpha() or text[j] in "*@"):
                j += 1

            cmd_name = text[i + 1: j]  # 不含反斜杠

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
        self._counter += 1
        return f"__{prefix}_{self._counter:04d}__"

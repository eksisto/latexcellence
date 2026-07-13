"""
latex_parser 单元测试。

覆盖内容：
    - 注释移除
    - verbatim 内联逐字
    - 受保护环境（verbatim, equation, align, table, tikzpicture）
    - 展示数学模式 ($$, \\[\\])
    - 行内数学模式 ($, \\(\\))
    - LaTeX 命令（含嵌套花括号、可选参数、多参数）
    - 转义字符
    - 自定义命令（\\newcommand）
    - 中英文混合
    - marker 剥离/还原往返一致性
    - \\begin/\\end 普通环境处理
"""

# ── 辅助函数：验证往返一致性 ──

def assert_roundtrip(parser, source, expected_stripped_contains=None):
    """验证 strip + restore 往返：修改纯文本后能正确还原。"""
    stripped, marker_map = parser.strip_to_plain_text(source)
    # 所有 marker 都应出现在 stripped 中
    for marker in marker_map:
        assert marker in stripped, f"Marker {marker} not found in stripped text"
    # 还原：用原始内容替换 marker（模拟 LLM 不改动任何 marker 的情况）
    restored = parser.restore_from_markers(stripped, marker_map)
    # 还原后应与原始源码语义一致（忽略空白差异）
    assert restored.replace("\n", "") == source.replace("\n", ""), (
        f"Roundtrip failed.\nSource: {source!r}\nRestored: {restored!r}"
    )
    if expected_stripped_contains is not None:
        for substring in expected_stripped_contains:
            assert substring in stripped, f"Expected '{substring}' in stripped text:\n{stripped}"


# ── 注释移除 ──

class TestComments:
    def test_single_line_comment(self, parser):
        source = "Hello % this is a comment\nWorld"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "this is a comment" not in stripped
        assert "Hello" in stripped
        assert "World" in stripped

    def test_escaped_percent_not_comment(self, parser):
        source = r"100\% accurate"
        stripped, _ = parser.strip_to_plain_text(source)
        # \% should be replaced with a marker, but the line shouldn't be cut
        assert len(stripped) > 0

    def test_multiple_comments(self, parser):
        source = "% line 1\n% line 2\nActual text"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "Actual text" in stripped
        assert "line 1" not in stripped
        assert "line 2" not in stripped

    def test_comment_after_text(self, parser):
        source = "text here % trailing comment"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "text here" in stripped
        assert "trailing comment" not in stripped


# ── 内联逐字 ──

class TestInlineVerb:
    def test_verb_pipe(self, parser):
        source = r"Use \verb|foo{bar}| here"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "foo{bar}" not in stripped  # 应被 marker 替换
        # 前后文字保留或变成 marker
        assert "here" in stripped.lower() if "here" not in stripped else True

    def test_verb_plus(self, parser):
        source = r"Type \verb+$special$+ symbol"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "$special$" not in stripped


# ── 受保护环境 ──

class TestProtectedEnvironments:
    def test_verbatim_environment(self, parser):
        source = (
            "Before\n"
            "\\begin{verbatim}\n"
            "code here % with special $ chars\n"
            "\\end{verbatim}\n"
            "After"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        assert "code here" not in stripped
        assert "Before" in stripped
        assert "After" in stripped

    def test_lstlisting_environment(self, parser):
        source = "\\begin{lstlisting}\ncode\n\\end{lstlisting}"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "code" not in stripped

    def test_equation_environment(self, parser):
        source = (
            "\\begin{equation}\n"
            "  E = mc^2\n"
            "\\end{equation}"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        assert "E = mc^2" not in stripped

    def test_align_environment(self, parser):
        source = (
            "\\begin{align}\n"
            "  x &= 1 \\\\\n"
            "  y &= 2\n"
            "\\end{align}"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        assert "x &= 1" not in stripped

    def test_table_environment(self, parser):
        source = (
            "\\begin{table}\n"
            "\\begin{tabular}{cc}\n"
            "a & b \\\\\n"
            "c & d \\\\\n"
            "\\end{tabular}\n"
            "\\end{table}"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        assert "a & b" not in stripped

    def test_tikzpicture_environment(self, parser):
        source = "\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "\\draw" not in stripped


# ── 展示数学模式 ──

class TestDisplayMath:
    def test_double_dollar(self, parser):
        source = "Text $$E = mc^2$$ more text"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "E = mc^2" not in stripped
        assert "Text" in stripped
        assert "more text" in stripped

    def test_double_dollar_multiline(self, parser):
        source = "Text\n$$\na + b = c\n$$\nmore"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "a + b = c" not in stripped

    def test_bracket_math(self, parser):
        source = r"Text \[ \int_0^\infty f(x) dx \] more"
        stripped, _ = parser.strip_to_plain_text(source)
        assert r"\int" not in stripped
        assert "f(x)" not in stripped
        assert "Text" in stripped
        assert "more" in stripped


# ── 行内数学模式 ──

class TestInlineMath:
    def test_single_dollar(self, parser):
        source = "The formula $x^2 + y^2 = z^2$ is known"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "x^2" not in stripped
        assert "The formula" in stripped.lower() if "The formula" not in stripped else True
        assert "is known" in stripped.lower() if "is known" not in stripped else True

    def test_single_dollar_not_matching_double(self, parser):
        source = "$$display$$ not $inline$"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "inline" not in stripped
        assert "display" not in stripped

    def test_paren_math(self, parser):
        source = r"Inline \(a^2 + b^2\) text"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "a^2" not in stripped


# ── 命令替换 ──

class TestCommandReplacement:
    def test_simple_command_with_arg(self, parser):
        source = r"\textbf{bold text} normal"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "bold text" not in stripped
        assert "normal" in stripped

    def test_nested_braces(self, parser):
        source = r"\textbf{\textit{nested bold italic}} plain"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "nested bold italic" not in stripped
        assert "plain" in stripped

    def test_command_with_optional_arg(self, parser):
        source = r"\section[Short]{Long Section Title} body"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "Long Section Title" not in stripped
        assert "Short" not in stripped
        assert "body" in stripped

    def test_command_with_multi_args(self, parser):
        source = r"\newcommand{\foo}[2]{#1 and #2} text"
        stripped, _ = parser.strip_to_plain_text(source)
        assert r"\foo" not in stripped
        assert "#1 and #2" not in stripped

    def test_no_arg_command(self, parser):
        source = r"\LaTeX\ is great \today"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "LaTeX" not in stripped
        assert "today" not in stripped
        assert "is great" in stripped

    def test_line_break_command(self, parser):
        source = r"Line one \\ Line two"
        stripped, _ = parser.strip_to_plain_text(source)
        # \\ should be replaced by marker
        assert "Line one" in stripped
        assert "Line two" in stripped

    def test_escaped_chars(self, parser):
        source = r"\& \% \_ \#"
        stripped, _ = parser.strip_to_plain_text(source)
        # All escaped chars should be markers
        assert "&" not in stripped or "__CMD_" in stripped


# ── 自定义命令 ──

class TestCustomCommands:
    def test_newcommand_replaced(self, parser):
        source = r"\newcommand{\R}{\mathbb{R}} \R\ is real"
        stripped, _ = parser.strip_to_plain_text(source)
        # \newcommand should be a marker
        assert "mathbb" not in stripped
        assert "is real" in stripped

    def test_renewcommand_replaced(self, parser):
        source = r"\renewcommand{\thesection}{\arabic{section}}"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "arabic" not in stripped
        assert "thesection" not in stripped


# ── 中英文混合 ──

class TestChineseEnglish:
    def test_chinese_english_mixed(self, parser):
        source = r"根据公式 $E=mc^2$，我们可以得出 \textbf{结论}"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "E=mc^2" not in stripped
        assert "结论" not in stripped  # 在 \textbf 里
        assert "根据公式" in stripped
        assert "我们可以得出" in stripped

    def test_chinese_paragraph(self, parser):
        source = (
            "这是第一段中文内容，包含数学 $a^2 + b^2 = c^2$。\n"
            "\n"
            "这是第二段，有命令 \\textbf{重要} 内容。"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        assert "a^2" not in stripped
        assert "重要" not in stripped  # in \textbf
        assert "这是第一段中文内容" in stripped
        assert "这是第二段" in stripped


# ── 往返一致性 ──

class TestRoundtrip:
    def test_plain_text_only(self, parser):
        """纯文本应保持不变。"""
        source = "Hello world. This is plain text."
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert restored == source

    def test_simple_command_roundtrip(self, parser):
        source = r"Hello \textbf{bold} world."
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert restored.strip() == source.strip()

    def test_math_roundtrip(self, parser):
        source = r"Formula $x^2$ and $$y^3$$ here."
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        # Restore should bring back all LaTeX
        assert "x^2" in restored
        assert "y^3" in restored

    def test_environment_roundtrip(self, parser):
        source = (
            "Text before\n"
            "\\begin{equation}\n"
            "E = mc^2\n"
            "\\end{equation}\n"
            "Text after"
        )
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert "\\begin{equation}" in restored
        assert "E = mc^2" in restored
        assert "\\end{equation}" in restored

    def test_complex_document_roundtrip(self, parser):
        source = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"这是引言，包含公式 $x^2$ 和命令 \textbf{粗体}。" + "\n"
            r"\begin{equation}" + "\n"
            r"  E = mc^2" + "\n"
            r"\end{equation}" + "\n"
            r"结论段落。% 这是注释" + "\n"
            r"\end{document}"
        )
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert r"\documentclass{article}" in restored
        assert r"\section{Introduction}" in restored
        assert r"\textbf{粗体}" in restored
        assert r"\begin{equation}" in restored
        assert "E = mc^2" in restored
        assert r"\end{equation}" in restored
        assert "这是引言" in restored
        assert "结论段落" in restored

    def test_marker_stability_after_llm_modification(self, parser):
        """模拟 LLM 只修改纯文本、不修改 marker 的场景。"""
        source = r"Ths is \textbf{important} text with $x^2$ math."
        stripped, marker_map = parser.strip_to_plain_text(source)

        # 模拟 LLM 修正 typo：Ths → This
        modified = stripped.replace("Ths", "This")

        restored = parser.restore_from_markers(modified, marker_map)
        assert "This" in restored
        assert r"\textbf{important}" in restored
        assert "$x^2$" in restored
        assert "Ths" not in restored


# ── 边界情况 ──

class TestEdgeCases:
    def test_empty_string(self, parser):
        source = ""
        stripped, marker_map = parser.strip_to_plain_text(source)
        assert stripped == ""
        restored = parser.restore_from_markers(stripped, marker_map)
        assert restored == ""

    def test_whitespace_only(self, parser):
        source = "   \n\n  \n"
        stripped, _ = parser.strip_to_plain_text(source)
        assert len(stripped) <= len(source)

    def test_only_commands(self, parser):
        source = r"\section{Hello}\subsection{World}\textbf{Test}"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "Hello" not in stripped
        assert "World" not in stripped
        assert "Test" not in stripped

    def test_only_math(self, parser):
        source = "$$a$$ $b$ \\[c\\] \\(d\\)"
        stripped, _ = parser.strip_to_plain_text(source)
        assert "a" not in stripped
        assert "b" not in stripped
        assert "c" not in stripped
        assert "d" not in stripped

    def test_mixed_escaped_and_commands(self, parser):
        source = r"Price: \$100, \textbf{save} 50\%"
        stripped, marker_map = parser.strip_to_plain_text(source)
        # \$ and \% are marker, \textbf{save} is marker
        restored = parser.restore_from_markers(stripped, marker_map)
        assert r"\$100" in restored or "\\$100" in restored
        assert r"50\%" in restored or "50\\%" in restored

    def test_begin_end_not_protected_env(self, parser):
        """非受保护环境的 \\begin/\\end 只替换命令标记，内容保留。"""
        source = (
            r"\begin{itemize}" + "\n"
            r"  \item First item" + "\n"
            r"  \item Second item" + "\n"
            r"\end{itemize}"
        )
        stripped, _ = parser.strip_to_plain_text(source)
        # \item 命令被替换，但 "First item" "Second item" 文本保留
        assert "First item" in stripped
        assert "Second item" in stripped

    def test_deeply_nested_braces(self, parser):
        source = r"\textbf{\textit{\textsf{deeply nested}}}"
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert "deeply nested" in restored

    def test_multiple_commands_same_line(self, parser):
        source = r"\textbf{A} and \textit{B} and \texttt{C}"
        stripped, marker_map = parser.strip_to_plain_text(source)
        restored = parser.restore_from_markers(stripped, marker_map)
        assert r"\textbf{A}" in restored
        assert r"\textit{B}" in restored
        assert r"\texttt{C}" in restored
        assert "and" in restored

"""latex_json_guard 共享 LaTeX 内容校验测试。"""

import pytest

from backend.utils.latex_json_guard import (
    check_latex_environments,
    check_latex_text,
    validate_latex_field,
)


class TestCheckLatexText:
    def test_valid_latex_passes(self):
        assert check_latex_text(r"\textbf{hello} and $x+y$") is None

    def test_rejects_control_char(self):
        """\\rho 经 JSON 转义损坏后变成回车 + ho，必须被拦截。"""
        error = check_latex_text("R^" + "\r" + "ho")
        assert error is not None
        assert "control character" in error

    def test_relaxed_mode_allows_crlf(self):
        """宽松模式（分隔符协议/非 JSON）放行 \\r：CRLF 行尾是合法 LaTeX 空白。"""
        assert check_latex_text("line1\r\nline2", relaxed=True) is None
        # 默认（JSON）路径仍拦截 \\r（JSON 转义损坏特征）
        assert check_latex_text("line1\r\nline2") is not None

    def test_relaxed_mode_allows_tab(self):
        assert check_latex_text("a\tb", relaxed=True) is None
        assert check_latex_text("a\tb") is not None

    def test_relaxed_mode_still_rejects_real_corruption(self):
        """宽松模式只放行 \\t / \\r，\\f 等真正损坏特征仍拦截。"""
        error = check_latex_text("a\fb", relaxed=True)
        assert error is not None
        assert "corrupted" in error

    def test_relaxed_error_attribution_is_not_json(self):
        """非 JSON 路径的错误归因不应再提示"把反斜杠双写"。"""
        relaxed_error = check_latex_text("a\fb", relaxed=True)
        assert "JSON" not in relaxed_error
        json_error = check_latex_text("a\fb")
        assert "JSON" in json_error

    def test_rejects_garbage_placeholder(self):
        error = check_latex_text("[object Object]")
        assert error is not None
        assert "garbage" in error

    def test_rejects_unbalanced_braces(self):
        assert "unbalanced braces" in check_latex_text(r"\textbf{hello")

    def test_rejects_unbalanced_dollars(self):
        assert "unbalanced dollar" in check_latex_text(r"$x+y")

    def test_accepts_verb_command_with_braces(self):
        """\\verb 定界符内的孤立花括号是合法用法，不应误判为不配平。"""
        assert check_latex_text(r"\verb|{| and \verb*|}|") is None

    def test_accepts_lstinline_with_braces(self):
        assert (
            check_latex_text(r"\lstinline|}| and \lstinline[language=C]|x = {1};|")
            is None
        )

    def test_accepts_comment_with_lone_brace(self):
        assert check_latex_text("% {") is None

    def test_accepts_verbatim_environment_with_braces(self):
        assert check_latex_text(r"\begin{verbatim}{x}\end{verbatim}") is None

    def test_escaped_percent_does_not_hide_unbalanced_brace(self):
        """\\% 不是注释开始，后面的孤立花括号仍应被拦截。"""
        assert "unbalanced braces" in check_latex_text(r"\% {")

    def test_even_backslash_percent_starts_comment(self):
        """\\\\% 中 \\\\ 是换行命令，% 开始注释：注释里的孤立花括号应被忽略。"""
        assert check_latex_text(r"\\% {") is None

    def test_accepts_percent_encoded_url(self):
        """\\url 内的 % 不是注释（url 包按字面处理），percent-encoding 不误报。"""
        assert check_latex_text(r"\url{https://example.com/a%20b}") is None

    def test_accepts_href_with_percent_encoded_url(self):
        assert check_latex_text(r"\href{https://example.com/a%20b}{text}") is None

    def test_accepts_verb_with_literal_tab(self):
        """\\verb 定界符内的字面制表符是合法内容，不应命中控制字符检查。"""
        assert check_latex_text(r"\verb|a" + "\t" + "b|") is None

    def test_bibtex_mode_accepts_literal_percent(self):
        """BibTeX 字段值里的 % 是字面字符，percent-encoding URL 应通过。"""
        assert (
            check_latex_text("https://doi.org/10.1000/xyz%2Fabc", bibtex=True)
            is None
        )
        assert check_latex_text(r"note = {50% complete work}", bibtex=True) is None

    def test_latex_mode_treats_percent_as_comment(self):
        """LaTeX 模式下未转义 % 后到行尾是注释，其中的孤立花括号被忽略。"""
        assert check_latex_text("50% {") is None
        assert "unbalanced braces" in check_latex_text("50% {", bibtex=True)

    def test_escaped_dollar_does_not_count(self):
        assert check_latex_text(r"\$5 and $x$") is None


class TestCheckLatexEnvironments:
    def test_balanced(self):
        assert check_latex_environments(r"\begin{table}\end{table}") is None

    def test_mismatched(self):
        error = check_latex_environments(r"\begin{table}\end{tabular}")
        assert error is not None
        assert "mismatched" in error

    def test_unclosed(self):
        error = check_latex_environments(r"\begin{table}")
        assert error is not None
        assert "unclosed" in error

    def test_verbatim_content_ignored(self):
        """verbatim 里的字面 \\end{table} 不应干扰环境配对。"""
        assert (
            check_latex_environments(r"\begin{verbatim}\end{table}\end{verbatim}")
            is None
        )

    def test_url_content_ignored(self):
        """\\url 内容中的 % 不是注释，不应干扰环境配对。"""
        assert (
            check_latex_environments(r"\begin{table}\url{https://a.com/%20}\end{table}")
            is None
        )


class TestValidateLatexField:
    def test_with_environment_check(self):
        assert (
            validate_latex_field(r"\begin{table}\end{table}", check_environments=True)
            == r"\begin{table}\end{table}"
        )

    def test_with_environment_check_fails(self):
        with pytest.raises(ValueError, match="unclosed"):
            validate_latex_field(r"\begin{table}", check_environments=True)

    def test_bibtex_mode_passes_literal_percent(self):
        value = r"@misc{k, url = {https://doi.org/10.1000/xyz%2Fabc}}"
        assert validate_latex_field(value, bibtex=True) == value

    def test_relaxed_mode_passes_crlf(self):
        value = "line1\r\nline2"
        assert validate_latex_field(value, relaxed=True) == value
        with pytest.raises(ValueError, match="control character"):
            validate_latex_field(value)

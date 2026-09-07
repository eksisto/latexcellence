"""latex_json_guard 共享 LaTeX 内容校验测试。"""

import pytest

from backend.utils.latex_json_guard import (
    check_latex_corruption,
    check_latex_environments,
    check_latex_text,
    check_latex_text_lite,
    validate_latex_field,
    validate_latex_field_lite,
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

    def test_accepts_fancy_verb(self):
        """fancyvrb \\Verb 定界符内的孤立花括号是合法内容。"""
        assert check_latex_text(r"\Verb|{| and \Verb*|}|") is None

    def test_accepts_mintinline_with_braces(self):
        """\\mintinline 的 code 参数含花括号/美元符是合法内容。"""
        assert check_latex_text(r"\mintinline{python}{x = {1};} and $y$") is None
        assert check_latex_text(r"\mintinline[breaklines]{c++}{$v$ = {1};}") is None

    def test_accepts_alltt_environment(self):
        """alltt 环境内容按字面处理，其中的花括号不参与配平。"""
        assert check_latex_text(r"\begin{alltt}a { b }\end{alltt} and text") is None

    def test_url_with_nested_braces_and_dollar(self):
        """URL 参数内的 $ / 嵌套花括号是字面字符，不应误报。"""
        assert check_latex_text(r"\url{https://a.com/x{y}z$}") is None
        assert check_latex_text(r"\href{https://a.com/x{y}z$}{text}") is None

    def test_bibtex_url_with_dollar_and_braces(self):
        """bibtex 模式同样剥离 URL 参数：$ / { 是字面字符。"""
        assert (
            check_latex_text(
                r"\url{https://doi.org/10.1000/x{y}%2Fz$}",
                bibtex=True,
            )
            is None
        )

    def test_bibtex_mode_accepts_literal_percent(self):
        """BibTeX 字段值里的 % 是字面字符，percent-encoding URL 应通过。"""
        assert (
            check_latex_text("https://doi.org/10.1000/xyz%2Fabc", bibtex=True) is None
        )
        assert check_latex_text(r"note = {50% complete work}", bibtex=True) is None

    def test_latex_mode_treats_percent_as_comment(self):
        """LaTeX 模式下未转义 % 后到行尾是注释，其中的孤立花括号被忽略。"""
        assert check_latex_text("50% {") is None
        assert "unbalanced braces" in check_latex_text("50% {", bibtex=True)

    def test_escaped_dollar_does_not_count(self):
        assert check_latex_text(r"\$5 and $x$") is None

    def test_error_hints_are_specific_per_type(self):
        """错误归因分诊：控制字符 / 占位符 / 配平给出不同修复提示。"""
        control = check_latex_text("a\fb")
        garbage = check_latex_text("[object Object]")
        braces = check_latex_text(r"\textbf{hello")
        dollars = check_latex_text(r"$x+y")

        # 控制字符提示 JSON 转义（双写反斜杠）
        assert "backslash" in control
        # 占位符提示不要序列化对象
        assert "do not serialize objects" in garbage
        # 配平提示转义字面花括号 / 美元符
        assert "\\{" in braces and "unbalanced braces" in braces
        assert "\\$" in dollars and "unbalanced dollar" in dollars


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


class TestCheckLatexTextLite:
    """轻量校验（marker 模块输出字段）测试。"""

    def test_valid_text_passes(self):
        assert check_latex_text_lite(r"\textbf{hello} and $x+y$") is None

    def test_allows_tab_and_newline(self):
        """\\t / \\n 是合法 LaTeX 空白，轻量校验放行。"""
        assert check_latex_text_lite("line1\nline2\ttabbed") is None

    def test_blocks_carriage_return(self):
        """\\r 在 JSON 路径仍拦截（未转义反斜杠的静默损坏特征）。"""
        error = check_latex_text_lite("R^" + "\r" + "ho")
        assert error is not None
        assert "control character" in error

    def test_blocks_form_feed(self):
        error = check_latex_text_lite("a\fb")
        assert error is not None
        assert "corrupted" in error

    def test_blocks_garbage_placeholder(self):
        error = check_latex_text_lite("[object Object]")
        assert error is not None
        assert "garbage" in error

    def test_error_attribution_is_content_corruption_not_json(self):
        """失败消息按"内容损坏"归因，不提示"把反斜杠双写"。"""
        error = check_latex_text_lite("a\fb")
        assert "JSON" not in error
        assert "corrupted" in error

    def test_skips_verbatim_content(self):
        """verbatim 内合法字面内容（如 $ / { }）不参与轻量校验。"""
        assert check_latex_text_lite(r"\verb|{| and \lstinline|$|") is None

    def test_non_string_rejected(self):
        error = check_latex_text_lite(123)
        assert error is not None
        assert "expected a string" in error

    def test_validate_field_lite_raises_on_corruption(self):
        with pytest.raises(ValueError, match="control character"):
            validate_latex_field_lite("R^" + "\r" + "ho")
        assert validate_latex_field_lite("a\tb\nc") == "a\tb\nc"

    def test_latex_text_lite_type_rejects_corruption(self):
        """LatexTextLite 作为 Pydantic 字段类型时拦截损坏内容。"""
        from pydantic import BaseModel, ValidationError

        from backend.utils.latex_json_guard import LatexTextLite

        class M(BaseModel):
            text: LatexTextLite

        with pytest.raises(ValidationError):
            M(text="R^" + "\r" + "ho")
        with pytest.raises(ValidationError):
            M(text="[object Object]")


class TestCheckLatexCorruption:
    """损坏特征专项校验（编辑器保存路径）测试：只拦损坏，不查配平。"""

    def test_allows_tab_crlf_and_newline(self):
        """\\t / \\r / \\n 是合法 LaTeX 空白（宽松口径），不得误拦。"""
        assert check_latex_corruption("line1\r\nline2\ttabbed\nline3") is None

    def test_blocks_real_control_character_corruption(self):
        error = check_latex_corruption("a\fb")
        assert error is not None
        assert "control character" in error
        # 非 JSON 直传路径的错误归因不提示"把反斜杠双写"
        assert "JSON" not in error

    def test_blocks_garbage_placeholder(self):
        error = check_latex_corruption("value = [object Object]")
        assert error is not None
        assert "garbage" in error

    def test_does_not_check_brace_or_dollar_balance(self):
        """编辑中间态合法：未配平花括号 / $ 均放行（editor-design §5.1）。"""
        assert check_latex_corruption(r"\textbf{hello $x + y") is None
        assert check_latex_corruption(r"\begin{itemize} never closed") is None

    def test_skips_verbatim_content(self):
        """verbatim / URL 字面内容中的特殊字符不参与校验。"""
        assert check_latex_corruption(r"\verb|a[object Object]b|") is None

    def test_non_string_rejected(self):
        error = check_latex_corruption(None)
        assert error is not None
        assert "expected a string" in error

"""针对 JSON/LaTeX 输出防护链路的对抗性回归测试。

这些用例刻意覆盖“JSON 能解析，但 LaTeX 已经变形”的情况，以及合法但
很容易被校验器误报的 LaTeX。历史上记录的缺口（数字定界符、\\Verb、
URL %、分隔符协议截断、BibTeX 字段校验、换行损坏、单引号替换）修复后
均已成为正式回归测试。
"""

import json
import re

import pytest
from pydantic import ValidationError

from backend.utils.json_utils import safe_parse_json
from backend.utils.latex_json_guard import (
    check_latex_environments,
    check_latex_text,
    check_latex_text_lite,
)
from backend.utils.latex_parser import LatexParser
from backend.utils.latex_literals import extract_delimited_block, remove_delimited_block
from backend.modules.formula_ocr.service import FormulaOCRService
from backend.modules.bib_manager.models import ExtractedMetadata
from backend.modules.style_modify.service import StyleModifyService
from backend.modules.table_ocr.service import TableOCRService
from backend.modules.writing_chat.service import WritingChatService


@pytest.mark.parametrize(
    "source",
    [
        r"纯文本与中文混排。",
        r"\textbf{嵌套 \emph{命令}} 与 \ref{sec:intro}。",
        r"\? \' \" \7 \~{n} \^{o} \ + 换行",
        "孤立反斜杠\\",
        r"价格 \$500，公式 $x + y$。",
        r"\(a^2 + b^2\) 与 $$c^2$$",
        r"% 注释中的 \frac{x}{y} { $ 不应暴露",
        r"\verb|\frac{x}{y} { $ %| and \lstinline[language=C]|x = {1};|",
        r"\begin{verbatim}literal \textbf{x} { $\end{verbatim}",
        r"\begin{equation}\frac{1}{2}\end{equation} text",
        r"\newcommand{\foo}[1]{\textbf{#1}} 文本",
        "数学和文字混合：\u03b1、\U0001d465、中文。",
    ],
)
def test_parser_roundtrip_for_adversarial_latex(source: str) -> None:
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(source)

    restored = parser.restore_from_markers(stripped, marker_map)
    assert re.sub(r"\s+", "", restored) == re.sub(r"\s+", "", source)


@pytest.mark.parametrize(
    "source",
    [
        # URL 嵌套在命令参数中（回归：marker 嵌套还原顺序）
        r"\footnote{\url{https://x/y%20z}}",
        r"\textbf{see \url{https://x/y%20z}}",
        r"\emph{\url{https://x/y%20z} important}",
        r"\caption{\url{https://x/y%20z}}",
        r"\footnote{see \href{https://x/y%20z}{the site}}",
        # URL 嵌套在受保护环境 / 注释中
        r"\begin{table}\url{https://x/y%20z}\end{table}",
        r"\begin{equation}\url{https://x/y%20z}\end{equation}",
        r"% Source: \url{https://example.com} and more",
        # \verb 嵌套在逐字环境中
        r"\begin{verbatim}a \verb|b % c|\end{verbatim}",
        r"\begin{Verbatim}a \verb|b % c|\end{Verbatim}",
        r"\begin{minted}{python}\verb|b % c|\end{minted}",
        r"\begin{alltt}a \verb|b % c|\end{alltt}",
    ],
)
def test_parser_roundtrip_nested_markers(source: str) -> None:
    """嵌套 marker 的往返：内层（URL/\\verb）被外层命令/环境/注释包裹时，
    还原必须先还原外层、再还原内层，否则内层 marker 残留为字面文本。"""
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(source)

    restored = parser.restore_from_markers(stripped, marker_map)
    assert re.sub(r"\s+", "", restored) == re.sub(r"\s+", "", source)


@pytest.mark.parametrize(
    ("source", "expected_marker"),
    [
        (r"\verb|a|", r"\verb|a|"),
        (r"\lstinline|x = {1}|", r"\lstinline|x = {1}|"),
    ],
)
def test_parser_hides_every_inline_verbatim_form(
    source: str, expected_marker: str
) -> None:
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(source)

    assert stripped == "__VERB_0000__"
    assert list(marker_map.values()) == [expected_marker]


def test_parser_protects_digit_delimited_verb() -> None:
    test_parser_hides_every_inline_verbatim_form(r"\verb1a{b}$1", r"\verb1a{b}$1")


def test_parser_protects_lstinline_with_options() -> None:
    test_parser_hides_every_inline_verbatim_form(
        r"\lstinline[language=C]|x = {1}|",
        r"\lstinline[language=C]|x = {1}|",
    )


def test_parser_protects_digit_delimited_lstinline() -> None:
    test_parser_hides_every_inline_verbatim_form(
        r"\lstinline1\frac{x}{y}1",
        r"\lstinline1\frac{x}{y}1",
    )


def test_parser_protects_fancyvrb_verb() -> None:
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(
        r"\Verb|raw { $ \\end{table}|"
    )

    assert stripped == "__VERB_0000__"
    assert list(marker_map.values()) == [r"\Verb|raw { $ \\end{table}|"]


def test_parser_does_not_swallow_text_after_percent_encoded_url() -> None:
    parser = LatexParser()
    stripped, _ = parser.strip_to_plain_text(
        r"\url{https://example.test/a%20b} visible $x$"
    )

    assert "visible" in stripped
    assert "__MATH_" in stripped


@pytest.mark.parametrize(
    "extractor",
    [
        FormulaOCRService()._extract_latex_block,
        TableOCRService._extract_latex_block,
        StyleModifyService._extract_latex_block,
        WritingChatService._extract_edit_latex_block,
    ],
)
def test_delimiter_inside_verbatim_content_is_not_a_protocol_end(
    extractor,
) -> None:
    content = r"\verb|literal <<<END>>>|"
    response = f"<<<LATEX>>>{content}<<<END>>>"

    assert extractor(response) == content


@pytest.mark.parametrize(
    ("text", "open_", "end_", "expected"),
    [
        # 回归：土耳其字母 İ (U+0130) 的 lower() 变成两个字符。旧实现先
        # text.lower() 搜索、再用 lower 下标切原文，遇到这类字符整体错位、
        # 静默截断（曾把 x 错切成 '<'）。
        ("İ<<<LATEX>>>x<<<END>>>", "<<<LATEX>>>", "<<<END>>>", "x"),
        ("<<<LATEX>>>İstanbul & more<<<END>>>", "<<<LATEX>>>", "<<<END>>>", "İstanbul & more"),
        ("<<<LATEX>>>x<<<END>>>İ", "<<<LATEX>>>", "<<<END>>>", "x"),
        # 中文 lower() 是恒等变换，本就不受影响，作为对照
        ("前置中文<<<LATEX>>>x<<<END>>>", "<<<LATEX>>>", "<<<END>>>", "x"),
    ],
)
def test_extract_delimited_block_survives_length_changing_lowercase(
    text: str, open_: str, end_: str, expected: str
) -> None:
    assert extract_delimited_block(text, open_, end_) == expected


def test_remove_delimited_block_survives_length_changing_lowercase() -> None:
    text = "before İ<<<LATEX>>>x<<<END>>> after"
    assert (
        remove_delimited_block(text, "<<<LATEX>>>", "<<<END>>>")
        == "before İ after"
    )


def test_restore_uses_longer_markers_first() -> None:
    parser = LatexParser()
    markers = {"__CMD_1__": "short", "__CMD_10000__": "long"}

    assert (
        parser.restore_from_markers("__CMD_1__ + __CMD_10000__", markers)
        == "short + long"
    )


@pytest.mark.parametrize(
    "value",
    [
        r"\textbf{hello} and $x+y$",
        r"\% literal percent, \{literal brace\}, \$ literal dollar",
        "line one\nline two",
        "line one\r\nline two",
        "line one\tline two",
        r"% comment with { and $",
        r"\verb1a{b}$1 and \lstinline[language=C]|x={1}|",
        r"\Verb|raw { $ \\end{table}| and $x$",
        r"\mintinline{python}{x = {1}; y = '$'} and $z$",
        r"\begin{alltt}raw { $ [object Object]\end{alltt} and text",
        r"\begin{Verbatim}raw { $\end{Verbatim} and text",
        r"\url{https://example.test/a{b}$c%20} and $x$",
        r"\href[title={a}]{https://example.test/a{b}$}{shown {text}}",
        r"\path{C:\\Program Files\\a{b}} and text",
        "Unicode：中文、日本語、emoji 😀，以及组合字符 e\u0301。",
    ],
)
def test_guard_accepts_legal_and_literal_heavy_latex(value: str) -> None:
    assert check_latex_text(value, relaxed=True) is None


@pytest.mark.parametrize(
    "control",
    [*(chr(i) for i in range(0, 10)), *(chr(i) for i in range(11, 32)), "\x7f"],
)
def test_guard_rejects_silent_json_control_damage(control: str) -> None:
    error = check_latex_text("prefix" + control + "suffix")

    assert error is not None
    assert "control character" in error


@pytest.mark.parametrize(
    "value",
    [
        "[object Object]",
        "[object Array]",
        "[object Function]",
        "prefix [object Promise] suffix",
        "[object object]",
    ],
)
def test_guard_rejects_serialized_object_placeholders(value: str) -> None:
    assert check_latex_text(value) is not None
    assert check_latex_text_lite(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        r"\textbf{unclosed",
        r"unclosed}",
        r"$x+y",
        r"x+y$",
        r"\href{https://example.test}{shown {text}",
        r"\url{https://example.test/{nested}",
    ],
)
def test_guard_rejects_unbalanced_structure(value: str) -> None:
    assert check_latex_text(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        r"\begin{itemize}\end{enumerate}",
        r"\end{document}",
        r"\begin{itemize}",
        r"\begin{a}\begin{b}\end{a}\end{b}",
    ],
)
def test_environment_guard_rejects_stack_errors(value: str) -> None:
    assert check_latex_environments(value) is not None


@pytest.mark.parametrize(
    ("latex", "expected_control"),
    [
        (r"\rho", "\r"),
        (r"\frac{x}{y}", "\f"),
        (r"\begin{itemize}", "\b"),
        (r"\textbf{x}", "\t"),
    ],
)
def test_json_parse_success_does_not_mean_latex_is_safe(
    latex: str, expected_control: str
) -> None:
    damaged = json.loads('{"latex":"' + latex + '"}')["latex"]

    assert expected_control in damaged
    assert check_latex_text(damaged) is not None


def test_bib_metadata_fields_reject_json_damaged_latex() -> None:
    damaged = json.loads('{"fields":{"title":"\\rho"}}')["fields"]

    with pytest.raises(ValidationError):
        ExtractedMetadata.model_validate({"fields": damaged})


@pytest.mark.parametrize("latex", [r"\newcommand{x}{y}", r"\nabla x"])
def test_guard_catches_newline_escape_corruption(latex: str) -> None:
    damaged = json.loads('{"latex":"' + latex + '"}')["latex"]

    assert check_latex_text(damaged) is not None


def test_correct_json_escaping_survives_guard() -> None:
    source = r"\newcommand{\foo}[1]{\textbf{#1}} and $x$"
    encoded = json.dumps({"latex": source}, ensure_ascii=False)

    decoded = json.loads(encoded)["latex"]
    assert decoded == source
    assert check_latex_text(decoded) is None


def test_safe_json_cleanup_does_not_corrupt_apostrophes() -> None:
    result = safe_parse_json('{"latex": "author\'s text",}')

    assert result == {"latex": "author's text"}


@pytest.mark.parametrize(
    "value",
    [
        "a\tb\nc",
        "a\r\nb",
        r"\verb|a\tb|",
        r"\Verb|a\rb|",
    ],
)
def test_lite_guard_preserves_json_safe_whitespace_policy(value: str) -> None:
    if "\r" in value and not value.startswith((r"\verb", r"\Verb")):
        assert check_latex_text_lite(value) is not None
    else:
        assert check_latex_text_lite(value) is None


def test_large_balanced_input_is_checked_without_recursion() -> None:
    value = " ".join(r"\textbf{" + str(i) + "}" for i in range(2000))

    assert check_latex_text(value) is None

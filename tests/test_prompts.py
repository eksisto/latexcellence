"""backend/core/prompts.py 提示词模板健壮性测试。

覆盖三类回归：
    1. get_prompt 对所有模板执行 str.format()，字面花括号必须写成 {{ / }}；
    2. 含占位符的模板漏传参数时快速失败，禁止把 {language} 字面量发给 LLM；
    3. BibTeX 上下文使用 BIBTEX_JSON_ESCAPE_RULES，LaTeX 上下文使用
       LATEX_JSON_ESCAPE_RULES，两者语义不串用。
"""

import re

import pytest

from backend.core.prompts import (
    _PROMPTS,
    get_prompt,
)

_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def _iter_templates():
    """遍历所有可通过 get_prompt 获取的模板（pgfplots_templates 为注入值，排除）。"""
    for module, keys in _PROMPTS.items():
        for key, template in keys.items():
            if key == "pgfplots_templates":
                continue
            yield module, key, template


def test_all_templates_survive_format():
    """所有模板用占位符参数格式化均不抛异常，且渲染结果不残留 '{{' 转义符。"""
    for module, key, template in _iter_templates():
        placeholders = sorted(set(_PLACEHOLDER_RE.findall(template)))
        rendered = template.format(**{name: "X" for name in placeholders})
        assert "{{" not in rendered, f"{module}.{key} 渲染结果残留 '{{'"


def test_missing_placeholder_fails_fast():
    """含占位符的模板漏传参数必须抛 KeyError，而不是把字面占位符发给 LLM。"""
    for module, key, template in _iter_templates():
        if not _PLACEHOLDER_RE.findall(template):
            continue
        with pytest.raises(KeyError):
            template.format()


def test_literal_braces_render_single():
    """转义后的字面花括号在渲染结果中恢复为单花括号。"""
    assert "\\begin{table}[htbp]" in get_prompt("table_ocr", "system")
    assert "\\caption{...}" in get_prompt("table_ocr", "system")

    formula = get_prompt("formula_ocr", "vision_system")
    assert "\\frac{a}{b}" in formula
    assert "\\sqrt[3]{x}" in formula
    assert "\\begin{equation}" in formula

    analyzer = get_prompt("latex_package_analyzer", "system")
    assert '{"packages": ["booktabs"]}' in analyzer
    assert "\\begin{tabular}{>{\\bfseries}l p{3cm}}" in analyzer
    assert "\\begin{equation}" in analyzer

    rewriting = get_prompt("web_search", "query_rewriting_system")
    assert '"intent": {' in rewriting
    assert '"filters": {"target_users"' in rewriting

    bibtex = get_prompt("bib_manager", "bibtex_generation")
    assert "title = {...}" in bibtex
    assert '"fields": {' in bibtex


def test_bibtex_rules_used_in_bib_manager():
    """bib_manager 使用 BibTeX 专用转义规则，不使用 LaTeX 数学模式规则。"""
    format_analysis = get_prompt(
        "bib_manager", "format_analysis", reference_entry="test"
    )
    bibtex_generation = get_prompt("bib_manager", "bibtex_generation")
    for prompt in (format_analysis, bibtex_generation):
        assert "BibTeX backslash must be written as TWO" in prompt
        assert "structural delimiters" in prompt
        assert "not math-mode" in prompt
        assert "group braces and dollar signs balanced" not in prompt


def test_latex_rules_still_used_for_latex_json():
    """code_review 等 LaTeX × JSON 场景仍使用 LATEX_JSON_ESCAPE_RULES。"""
    code_review = get_prompt("code_review", "system", language="Chinese")
    assert "group braces and dollar signs balanced" in code_review
    assert "structural delimiters" not in code_review


def test_plot_gen_workflow_read_data_wording():
    """agent_system 工作流与规则 9 不再冲突：脚本内读数据 ≠ read_file 工具。"""
    prompt = get_prompt(
        "plot_gen",
        "agent_system",
        profile="shape: 10 rows",
        chart_plan='{"chart_type": "line"}',
        pgfplots_reference="line template",
        style="nature",
        language="zh",
        max_rounds=30,
    )
    assert "load the data from" in prompt
    assert "Do NOT call the `read_file` tool on the data file" in prompt


def test_typo_changes_contract_in_prompt():
    """typo_check 明确 changes 与 corrected 的双向约束，type 保留本地化展示模板。"""
    prompt = get_prompt("typo_check", "system", language="Chinese")
    raw = _PROMPTS["typo_check"]["system"]
    assert "changes MUST be an empty array []" in prompt
    assert "changes MUST be non-empty" in prompt
    assert '"type": "<' in raw
    assert '{language}>"' in raw
    assert '"type": "typo"' not in raw


def test_expression_changes_contract_in_prompt():
    """expression_opt 明确 changes 与 optimized 的双向约束。"""
    prompt = get_prompt(
        "expression_opt", "base_system", custom_prompt="x", language="Chinese"
    )
    assert "changes MUST be an empty array []" in prompt
    assert "changes MUST be non-empty" in prompt


def test_compile_error_translator_json_escape_rules():
    """编译错误翻译输出 JSON 承载 LaTeX，必须附 JSON 转义规则且示例为转义形式。"""
    prompt = get_prompt("compile_error_translator", "system", language="Chinese")
    assert "JSON STRING ESCAPING (CRITICAL)" in prompt
    assert "each LaTeX backslash becomes TWO backslashes" in prompt
    assert "\\\\usepackage{amsmath}" in prompt
    assert "\\\\alpa" in prompt
    assert "\\\\alpha" in prompt
    assert "File 'xxx.sty' not found" in prompt
    assert "Underfull \\hbox" in prompt

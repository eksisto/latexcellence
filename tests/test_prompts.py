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
    GENERATION_FIDELITY_RULE,
    JSON_ONLY_RULE,
    MARKER_PROTECTION_RULES,
    NO_INVENTION_RULE,
    UNTRUSTED_DATA_RULE,
    _PROMPTS,
    get_prompt,
)
from backend.modules.bib_manager.style_registry import list_all_styles
from tools.prompt_comprehension_eval.run_eval import (
    _plot_refine_check,
    _template_planning_check,
    _time_context_check,
)

_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def _iter_templates():
    """遍历所有可通过 get_prompt 获取的模板（pgfplots_templates 为注入值，排除）。"""
    for module, keys in _PROMPTS.items():
        for key, template in keys.items():
            if key == "pgfplots_templates":
                continue
            yield module, key, template


def _render(module: str, key: str) -> str:
    """用占位符占位值渲染提示词，供结构测试使用。"""
    template = _PROMPTS[module][key]
    placeholders = sorted(set(_PLACEHOLDER_RE.findall(template)))
    return template.format(**{name: "X" for name in placeholders})


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
    assert '"original_query": "the exact user query"' in rewriting
    assert '"intent": "fact"' in rewriting
    assert '"languages": ["zh", "en"]' in rewriting

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


def test_llm_output_schemas_no_paragraph_index():
    """LLM 输出 schema 不再要求 paragraph_index，索引由服务端统一注入。"""
    for module, key in (
        ("typo_check", "system"),
        ("expression_opt", "base_system"),
        ("code_review", "system"),
        ("paragraph_translate", "system"),
    ):
        assert '"paragraph_index"' not in _PROMPTS[module][key]


def test_expression_changes_contract_in_prompt():
    """expression_opt 明确 changes 与 optimized 的双向约束。"""
    prompt = get_prompt(
        "expression_opt", "base_system", custom_prompt="x", language="Chinese"
    )
    assert "changes MUST be an empty array []" in prompt
    assert "changes MUST be non-empty" in prompt


_JSON_ARCH_PROMPTS = [
    ("typo_check", "system"),
    ("expression_opt", "base_system"),
    ("code_review", "system"),
    ("paragraph_translate", "system"),
    ("compile_error_translator", "system"),
    ("web_search", "query_rewriting_system"),
    ("latex_package_analyzer", "system"),
    ("bib_manager", "format_analysis"),
    ("bib_manager", "metadata_extraction"),
    ("bib_manager", "bibtex_generation"),
    ("plot_gen", "chart_recommendation_system"),
]


def test_json_prompts_follow_section_order():
    """JSON 类提示词统一为 role → critical_rules → output_format 结构。"""
    for module, key in _JSON_ARCH_PROMPTS:
        prompt = _render(module, key)
        assert "<role>" in prompt, f"{module}.{key} 缺少 <role>"
        assert "<critical_rules>" in prompt, f"{module}.{key} 缺少 <critical_rules>"
        assert "<output_format>" in prompt, f"{module}.{key} 缺少 <output_format>"
        assert (
            prompt.index("<role>")
            < prompt.index("<critical_rules>")
            < prompt.index("<output_format>")
        ), f"{module}.{key} 分区顺序不符合统一结构"


def test_json_prompts_full_section_order():
    """JSON 类提示词完整分区顺序：role → task → critical_rules → [input] →
    output_format → examples；custom_instructions 只能作为末尾扩展。"""
    for module, key in _JSON_ARCH_PROMPTS:
        raw = _PROMPTS[module][key]
        assert (
            raw.index("<role>")
            < raw.index("<task>")
            < raw.index("<critical_rules>")
            < raw.index("<output_format>")
        ), f"{module}.{key} 主分区顺序不符合统一结构"
        if "<input>" in raw:
            assert (
                raw.index("<critical_rules>")
                < raw.index("<input>")
                < raw.index("<output_format>")
            ), f"{module}.{key} 的 <input> 位置错误"
        if "<examples>" in raw:
            assert raw.index("<output_format>") < raw.index(
                "<examples>"
            ), f"{module}.{key} 的 <examples> 必须在 <output_format> 之后"
        if "<custom_instructions>" in raw:
            assert raw.index("<examples>") < raw.index(
                "<custom_instructions>"
            ), f"{module}.{key} 的 <custom_instructions> 只能放在末尾"


def test_marker_protected_prompts_use_shared_rules():
    """Marker 保护提示词复用共享规则块，且规则位于 JSON schema 之前。"""
    for module, key in (
        ("typo_check", "system"),
        ("expression_opt", "base_system"),
        ("paragraph_translate", "system"),
    ):
        prompt = _render(module, key)
        assert (
            MARKER_PROTECTION_RULES in prompt
        ), f"{module}.{key} 未复用共享 Marker 规则"
        assert prompt.index("ABSOLUTELY DO NOT") < prompt.index("JSON format")


_FACT_EXTRACTION_PROMPTS = [
    ("bib_manager", "format_analysis"),
    ("bib_manager", "metadata_extraction"),
]

_GENERATION_PROMPTS = [
    ("typo_check", "system"),
    ("expression_opt", "base_system"),
    ("paragraph_translate", "system"),
    ("code_review", "system"),
    ("compile_error_translator", "system"),
    ("web_search", "query_rewriting_system"),
    ("latex_package_analyzer", "system"),
    ("bib_manager", "bibtex_generation"),
    ("plot_gen", "chart_recommendation_system"),
    ("style_modify", "system"),
    ("writing_chat", "edit_system"),
]


def test_no_invention_rule_used_in_fact_extraction_prompts():
    """事实/数据提取类提示词使用 NO_INVENTION_RULE。"""
    for module, key in _FACT_EXTRACTION_PROMPTS:
        assert NO_INVENTION_RULE in _render(
            module, key
        ), f"{module}.{key} 缺少 NO_INVENTION_RULE"


def test_generation_fidelity_rule_used_in_generation_prompts():
    """生成类提示词使用 GENERATION_FIDELITY_RULE，不得误用 NO_INVENTION_RULE。"""
    for module, key in _GENERATION_PROMPTS:
        prompt = _render(module, key)
        assert (
            GENERATION_FIDELITY_RULE in prompt
        ), f"{module}.{key} 缺少 GENERATION_FIDELITY_RULE"
        assert NO_INVENTION_RULE not in prompt, (
            f"{module}.{key} 误用 NO_INVENTION_RULE（生成类任务应使用 "
            "GENERATION_FIDELITY_RULE）"
        )


def test_shared_untrusted_data_rule_used():
    """代码审查与写作助手统一使用外部数据不可信规则。"""
    for module, key in (
        ("code_review", "system"),
        ("writing_chat", "chat_system"),
    ):
        prompt = _render(module, key)
        assert UNTRUSTED_DATA_RULE in prompt, f"{module}.{key} 缺少 UNTRUSTED_DATA_RULE"


def test_user_prompts_wrap_input():
    """逐段检查类 user 提示词使用 <input> 标签包裹输入文本。"""
    for module, key in (
        ("typo_check", "user"),
        ("expression_opt", "user"),
        ("paragraph_translate", "user"),
    ):
        prompt = _render(module, key)
        assert "<input>\nX\n</input>" in prompt, f"{module}.{key} 未包裹 <input>"


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


def test_package_analyzer_prompt_has_no_static_mapping():
    """LLM 兜底提示词不再内置 Feature→Package 映射，避免与规则表漂移。"""
    analyzer = get_prompt("latex_package_analyzer", "system")
    assert "Feature → Package Mapping" not in analyzer
    assert "column specifiers → array" not in analyzer
    assert "Non-trivial math" not in analyzer
    assert "p{} column" in analyzer
    assert "\\begin{equation}" in analyzer


def test_format_analysis_prompt_defers_style_to_registry():
    """format_analysis 输出不含 latex_* 字段，样式由后端注册表统一填充。"""
    prompt = get_prompt("bib_manager", "format_analysis", reference_entry="test")
    assert "known mappings as reference" not in prompt
    assert "Do NOT include LaTeX style fields" in prompt
    assert '"latex_backend"' not in prompt
    assert '"latex_preamble"' not in prompt


def test_format_analysis_prompt_covers_style_registry():
    """格式分析提示词列出的格式名必须覆盖样式注册表，防止识别名与映射名脱节。"""
    prompt = get_prompt("bib_manager", "format_analysis", reference_entry="test")
    for name in list_all_styles():
        assert name in prompt


def test_format_analysis_prompt_has_canonical_examples():
    """格式分析提示词为 LLM 兜底提供每种格式的规范样例。"""
    prompt = get_prompt("bib_manager", "format_analysis", reference_entry="test")
    assert "## Format Reference Examples" in prompt
    assert "Phys. Rev. Lett." in prompt
    assert "J. Am. Chem. Soc." in prompt
    assert "J. ACM" in prompt
    assert "Nature 580" in prompt
    assert "JAMA" in prompt


def test_template_gen_quality_gate_not_universal():
    """template_gen 质量门禁不再对所有文档类型强制 citation/TOC/图表。"""
    prompt = get_prompt(
        "template_gen",
        "agent_system",
        language="Chinese",
        max_files=30,
        max_rounds=30,
        compile_enabled="available",
    )
    assert "at least one citation" not in prompt
    assert "never add a construct" in prompt
    assert "labels/refs resolve" in prompt


# ── 阶段 2：高频模块改写回归 ────────────────────────────────────────────────


def test_phase2_high_frequency_prompts_have_examples():
    """错字查找/表达优化/逐段翻译/代码审查/写作编辑提示词均含紧凑示例。"""
    for module, key in (
        ("typo_check", "system"),
        ("expression_opt", "base_system"),
        ("paragraph_translate", "system"),
        ("code_review", "system"),
        ("writing_chat", "edit_system"),
    ):
        assert "<examples>" in _render(module, key), f"{module}.{key} 缺少 <examples>"


def test_phase2_examples_note_output_language():
    """示例只演示格式，实际输出语言必须由 {language} 占位符控制。"""
    note = "in {language} in your actual output"
    for module, key in (
        ("typo_check", "system"),
        ("expression_opt", "base_system"),
        ("paragraph_translate", "system"),
        ("code_review", "system"),
        ("writing_chat", "edit_system"),
    ):
        assert note in _PROMPTS[module][key], f"{module}.{key} 示例缺少输出语言说明"


def test_phase2_no_change_contracts_mention_byte_identity():
    """无修改契约明确要求逐字节返回原文、changes 为空数组。"""
    assert "byte-identical" in _render("typo_check", "system")
    assert "byte-for-byte" in _render("typo_check", "system")
    assert "identical to the original field" in _render("expression_opt", "base_system")
    assert "identical to the original field" in _render("paragraph_translate", "system")


def test_paragraph_translate_preserves_academic_requirements():
    """逐段翻译保留原学术翻译要求：术语不意译、全文术语一致、客观正式语气。"""
    prompt = get_prompt(
        "paragraph_translate", "system", language="Chinese", target_language="English"
    )
    assert "do NOT paraphrase technical terms" in prompt
    assert "consistent terminology throughout the paper" in prompt
    assert "objective, formal academic tone" in prompt


def test_expression_opt_preserves_guideline_coverage():
    """表达优化保留原指南：修正拗口表达、改善逻辑衔接与过渡。"""
    prompt = get_prompt(
        "expression_opt", "base_system", custom_prompt="x", language="Chinese"
    )
    assert "fix awkward or unnatural phrasing" in prompt
    assert "improve logical flow and transitions" in prompt


def test_phase2_unclear_input_contracts():
    """识别不了时必须空结果并明说，不臆造命令/DOI/文献/报错。"""
    compile_prompt = get_prompt(
        "compile_error_translator", "system", language="Chinese"
    )
    assert "cannot be identified" in compile_prompt
    assert "empty array []" in compile_prompt
    assert "Never fabricate error messages" in compile_prompt

    analyzer = get_prompt("latex_package_analyzer", "system")
    assert "Never invent package names" in analyzer

    rewriting = get_prompt("web_search", "query_rewriting_system")
    assert "never fabricate required terms" in rewriting

    format_analysis = get_prompt(
        "bib_manager", "format_analysis", reference_entry="test"
    )
    assert "Never fabricate a format" in format_analysis
    metadata = get_prompt("bib_manager", "metadata_extraction")
    assert "Never fabricate DOIs" in metadata
    bibtex = get_prompt("bib_manager", "bibtex_generation")
    assert "never invent DOIs" in bibtex


def test_phase2_ocr_unrecognizable_contract():
    """OCR 图片无法辨认时输出空块 + 0.0 置信度，绝不编造公式/表格。"""
    formula = get_prompt("formula_ocr", "vision_system")
    assert "EMPTY LaTeX block" in formula
    assert "Do NOT invent or guess a formula" in formula
    table = get_prompt("table_ocr", "system")
    assert "EMPTY LaTeX block" in table
    assert "Do NOT fabricate or guess table cells" in table


def test_phase2_writing_chat_contracts():
    """写作助手对话不臆造引用、信息不足明说；编辑提示词含分隔符协议示例。"""
    chat = get_prompt(
        "writing_chat", "chat_system", context_block="", language="Chinese"
    )
    assert "Never invent URLs, references, DOIs, citations, or data" in chat
    assert "insufficient" in chat
    assert "[EVIDENCE document_id#chunk_id]" in chat
    assert "page outline first" in chat
    edit = get_prompt(
        "writing_chat", "edit_system", instruction="x", language="Chinese"
    )
    assert "<examples>" in edit
    assert "\\ref{tab:results}" in edit


# ── 阶段 3：长 Agent 提示词瘦身与拆分 ────────────────────────────────────────

_TEMPLATE_GEN_KEYS = (
    "agent_system",
    "planning_system",
    "compile_phase_system",
    "validation_phase_system",
)


def test_template_gen_phase_blocks_exist():
    """template_gen 已拆分为核心 system + planning/compile/validation 阶段块。"""
    for key in _TEMPLATE_GEN_KEYS:
        assert key in _PROMPTS["template_gen"], f"template_gen 缺少 {key}"
        assert _render("template_gen", key)


def test_template_gen_core_is_compact():
    """核心 system 只保留工作流/失败预算/硬约束，长清单移入阶段块。"""
    core = _render("template_gen", "agent_system")
    assert len(core) < 9000, "核心 system 仍然过长"
    assert "CATEGORY PLAYBOOKS" not in core
    assert "MISSING RESOURCE PROTOCOL" not in core
    assert "VISUAL QUALITY GATE" not in core
    assert "COMPILATION ERROR RECOVERY PROTOCOL" not in core
    # 细节按阶段承担
    assert "CATEGORY PLAYBOOKS" in _render("template_gen", "planning_system")
    assert "COMPILATION ERROR RECOVERY PROTOCOL" in _render(
        "template_gen", "compile_phase_system"
    )
    assert "VISUAL QUALITY GATE" in _render("template_gen", "validation_phase_system")


def test_template_gen_phase_blocks_require_no_extra_placeholders():
    """阶段块不引入新的占位符，可直接用空 kwargs 渲染。"""
    for key in _TEMPLATE_GEN_KEYS[1:]:
        assert get_prompt("template_gen", key)


def test_prompt_eval_accepts_semantically_complete_planning_answer():
    """规划评测接受列全核心契约但未重复 small/minimal 的合法回答。"""
    response = (
        "The first compile contract should contain a standard class, one style "
        "layer, public metadata setters/accessors, and plain body text."
    )
    assert _template_planning_check(response)[0] == "PASS"


def test_prompt_eval_accepts_direct_finish_answer():
    """绘图 refine 评测接受简洁的中文直接 finish 回答。"""
    assert _plot_refine_check("直接调用 finish。")[0] == "PASS"


def test_prompt_eval_accepts_chinese_date_format():
    """时间上下文评测接受与 ISO 日期等价的中文年月日格式。"""
    assert _time_context_check("今天是2031年5月6日。")[0] == "PASS"


def test_plot_gen_agent_compressed_and_refine_deduped():
    """plot_gen agent 去重后 ASCII 规则只保留一处；refine 引用会话规则。"""
    agent = get_prompt(
        "plot_gen",
        "agent_system",
        profile="x",
        chart_plan="y",
        pgfplots_reference="z",
        style="nature",
        language="zh",
        max_rounds=30,
    )
    refine = get_prompt("plot_gen", "refine_system", message="m", language="zh")
    assert agent.count("FIGURE TEXT LANGUAGE") == 1
    assert "All generation rules from the session system prompt" in refine
    assert len(refine) < len(agent)


def test_style_modify_agent_compressed_and_refine_deduped():
    """style_modify 去重后编译可用性只出现一次；refine 复用 agent 规则。"""
    agent = get_prompt(
        "style_modify",
        "agent_system",
        compile_enabled="available",
        language="zh",
        max_rounds=20,
    )
    refine = get_prompt(
        "style_modify",
        "refine_system",
        compile_enabled="available",
        message="m",
        language="zh",
    )
    assert agent.count("Shadow compilation:") == 1
    assert "Follow the session system prompt rules" in refine
    assert "Never edit the real project directly" in refine
    assert len(refine) < len(agent)

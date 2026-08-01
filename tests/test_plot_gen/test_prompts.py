"""plot_gen 提示词模板测试。"""

from backend.core.prompts import get_prompt, render_pgfplots_reference


def test_render_known_chart_types():
    """box/line/heatmap 均返回已验证模板且强制 col sep=comma。"""
    box = render_pgfplots_reference("box")
    assert "\\usepgfplotslibrary{statistics}" in box
    assert "boxplot" in box
    assert "col sep=comma" in box

    line = render_pgfplots_reference("line")
    assert "xmode=log" in line
    assert "y error plus=sem" in line
    assert "col sep=comma" in line

    heatmap = render_pgfplots_reference("heatmap")
    assert "matrix plot" in heatmap
    assert "mesh/cols=" in heatmap
    assert "col sep=comma" in heatmap


def test_render_case_insensitive_and_none():
    """chart_type 大小写不敏感；None/未知类型回退到 line 模板。"""
    assert render_pgfplots_reference("LINE") == render_pgfplots_reference("line")
    assert "xmode=log" in render_pgfplots_reference(None)
    assert "xmode=log" in render_pgfplots_reference("unknown_type")


def test_render_scatter_falls_back_with_guidance():
    """未提供专用模板的类型（scatter 等）返回引导文本。"""
    ref = render_pgfplots_reference("scatter")
    assert "line template" in ref
    assert "col sep=comma" in ref


def test_agent_system_formats_with_reference():
    """注入模板后 agent_system 可正常 format，且模板内容原样保留。"""
    prompt = get_prompt(
        "plot_gen",
        "agent_system",
        profile="shape: 10 rows",
        chart_plan='{"chart_type": "line"}',
        pgfplots_reference=render_pgfplots_reference("line"),
        style="nature",
        language="zh",
        max_rounds=30,
    )
    assert "VERIFIED PGFPLOTS TEMPLATES" in prompt
    assert "\\addplot table[x=dose, y=mean, y error plus=sem," in prompt
    assert "col sep=comma" in prompt
    assert "{{data_SeriesA.csv}}" not in prompt, "模板花括号不应被双重转义"

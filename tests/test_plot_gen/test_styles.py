"""plot_gen 风格系统测试。"""

from backend.modules.plot_gen.styles import list_styles, render_setup_code


def test_list_styles_contains_defaults():
    keys = {s["key"] for s in list_styles()}
    assert {"nature", "science", "cell", "ieee", "acs"} <= keys


def test_render_setup_code_contains_matplotlib():
    code = render_setup_code("nature")
    assert "matplotlib" in code
    assert "axes.prop_cycle" in code

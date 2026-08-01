"""plot_gen 图表推荐测试。"""

from backend.modules.plot_gen.data_parser import parse_data_file
from backend.modules.plot_gen.recommender import deterministic_chart_plan


def test_deterministic_trend_plan(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("time,value\n2024-01-01,1\n2024-01-02,2\n", encoding="utf-8")
    profile = parse_data_file(path, max_rows=100)
    plan = deterministic_chart_plan(profile, "显示趋势")
    assert plan.chart_type == "line"


def test_deterministic_bar_plan(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("group,value\nA,1\nB,2\n", encoding="utf-8")
    profile = parse_data_file(path, max_rows=100)
    plan = deterministic_chart_plan(profile, "比较两组")
    assert plan.chart_type == "bar"
    assert plan.x == "group"

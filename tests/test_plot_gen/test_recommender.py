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


def test_deterministic_distribution_request_prefers_box(tmp_path):
    """用户关注分组分布/偏态/异常值时，应推荐箱线图而非散点。"""
    path = tmp_path / "data.csv"
    path.write_text(
        "Treatment,Biomass_mg\n"
        "Control,45.2\nControl,47.8\nControl,44.1\n"
        "Low,28.5\nLow,26.9\nLow,29.7\n",
        encoding="utf-8",
    )
    profile = parse_data_file(path, max_rows=100)
    plan = deterministic_chart_plan(
        profile, "观察不同处理组下生物量的分布形态，检查是否存在偏态或异常值"
    )
    assert plan.chart_type == "box"
    assert plan.x == "Treatment"
    assert plan.y == "Biomass_mg"


def test_distribution_request_en_keywords(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("group,value\nA,1\nA,2\nB,3\nB,4\nC,5\nC,6\n", encoding="utf-8")
    profile = parse_data_file(path, max_rows=100)
    plan = deterministic_chart_plan(profile, "check skew and outliers per group")
    assert plan.chart_type == "box"

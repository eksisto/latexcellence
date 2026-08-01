"""受控 PGFPlots 生成器（tikzgen）单元测试。"""

import json
from pathlib import Path

from backend.modules.plot_gen.tikzgen import generate_figure_tex


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ",".join(header) + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n",
        encoding="utf-8",
    )


def _write_spec(sandbox: Path, spec: dict) -> None:
    (sandbox / "artifacts").mkdir(parents=True, exist_ok=True)
    (sandbox / "artifacts" / "tikz_spec.json").write_text(
        json.dumps(spec), encoding="utf-8"
    )


def _line_spec() -> dict:
    return {
        "chart_type": "line",
        "xlabel": "Dose (uM)",
        "ylabel": "Inhibition (%)",
        "xmode": "log",
        "legend": {"pos": "south east"},
        "series": [
            {
                "file": "data_A.csv",
                "x": "dose",
                "y": "mean",
                "y_plus": "sem",
                "y_minus": "sem",
                "label": "DrugA",
                "color": "C0",
            }
        ],
    }


def test_line_spec_renders_table_and_legend(tmp_path):
    sandbox = tmp_path / "s"
    _write_csv(
        sandbox / "artifacts" / "data_A.csv",
        ["dose", "mean", "sem"],
        [[0.1, 0.5, 0.05], [1.0, 0.8, 0.1]],
    )
    _write_spec(sandbox, _line_spec())

    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    assert r["error"] is None
    assert "TikZ source generated" in r["message"]
    tex = (sandbox / "artifacts" / "figure.tex").read_text(encoding="utf-8")
    assert "\\addplot+[mark=none, plotcolor0]" in tex
    assert (
        "table[x=dose, y=mean, y error plus=sem, y error minus=sem, col sep=comma]"
        in tex
    )
    assert "\\addlegendentry{DrugA}" in tex
    assert "xmode=log" in tex
    assert "legend pos=south east" in tex
    assert "\\definecolor{plotcolor0}{RGB}{31,119,180}" in tex


def test_missing_spec_returns_not_generated(tmp_path):
    sandbox = tmp_path / "s"
    (sandbox / "artifacts").mkdir(parents=True)
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert r["error"] is None
    assert "tikz_spec.json is missing" in r["message"]
    assert not (sandbox / "artifacts" / "figure.tex").exists()


def test_invalid_spec_reports_validation_error(tmp_path):
    sandbox = tmp_path / "s"
    _write_spec(sandbox, {"chart_type": "heatmap"})  # 缺 heatmap 子对象
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert r["error"] is not None
    assert "validation failed" in r["error"]
    assert "heatmap" in r["error"]


def test_invalid_json_reports_error(tmp_path):
    sandbox = tmp_path / "s"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "tikz_spec.json").write_text("{not json", encoding="utf-8")
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "not valid JSON" in r["error"]


def test_missing_data_file_reports_error(tmp_path):
    sandbox = tmp_path / "s"
    _write_spec(sandbox, _line_spec())  # data_A.csv 不存在
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "data file data_A.csv is missing" in r["error"]
    assert "data_A.csv" in r["error"]


def test_non_numeric_series_column_reports_error(tmp_path):
    sandbox = tmp_path / "s"
    _write_csv(
        sandbox / "artifacts" / "data_A.csv",
        ["dose", "mean"],
        [["low", 0.5], ["high", 0.8]],
    )
    _write_spec(sandbox, _line_spec())
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "non-numeric" in r["error"]
    assert "dose" in r["error"]


def test_heatmap_non_integer_indices_reports_error(tmp_path):
    """热力图 x/y 用变量名而非整数索引时，生成阶段直接报错。"""
    sandbox = tmp_path / "s"
    rows = [["x", "y", "value"]]
    for name in ["temp", "humidity"]:
        for other in ["temp", "humidity"]:
            rows.append([name, other, 0.9])
    _write_csv(sandbox / "artifacts" / "data_corr.csv", ["x", "y", "value"], rows[1:])
    _write_spec(
        sandbox,
        {
            "chart_type": "heatmap",
            "heatmap": {
                "matrix_file": "data_corr.csv",
                "cols": 2,
                "x_labels": ["temp", "humidity"],
                "y_labels": ["temp", "humidity"],
            },
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "non-numeric" in r["error"]


def test_heatmap_fractional_indices_reports_error(tmp_path):
    """热力图 x/y 用小数而非整数索引时，生成阶段直接报错。"""
    sandbox = tmp_path / "s"
    rows = [["x", "y", "value"]]
    for x in [0.5, 1.5, 2.5, 3.5]:
        for y in [0, 1, 2, 3]:
            rows.append([x, y, 0.2])
    _write_csv(sandbox / "artifacts" / "data_corr.csv", ["x", "y", "value"], rows[1:])
    _write_spec(
        sandbox,
        {
            "chart_type": "heatmap",
            "heatmap": {"matrix_file": "data_corr.csv", "cols": 4},
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "must be integer" in r["error"]


def test_heatmap_empty_column_reports_error(tmp_path):
    """热力图 x 列全空时，生成阶段直接报错而不是抛 ValueError。"""
    sandbox = tmp_path / "s"
    rows = [["x", "y", "value"]]
    for _ in range(16):
        rows.append(["", "", ""])
    _write_csv(sandbox / "artifacts" / "data_corr.csv", ["x", "y", "value"], rows[1:])
    _write_spec(
        sandbox,
        {
            "chart_type": "heatmap",
            "heatmap": {"matrix_file": "data_corr.csv", "cols": 4},
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is False
    assert "must be non-empty" in r["error"]


def test_box_renders_statistics_and_xticklabels(tmp_path):
    sandbox = tmp_path / "s"
    for i, g in enumerate(["Control", "Low", "Mid", "High"]):
        _write_csv(
            sandbox / "artifacts" / f"data_{g}.csv",
            ["measurement"],
            [[10 + i * 3 + j] for j in range(20)],
        )
    _write_spec(
        sandbox,
        {
            "chart_type": "box",
            "ylabel": "Measurement",
            "box": {"xticklabels": ["Control", "Low", "Mid", "High"]},
            "series": [
                {"file": f"data_{g}.csv", "x": "x", "y": "measurement"}
                for g in ["Control", "Low", "Mid", "High"]
            ],
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    tex = (sandbox / "artifacts" / "figure.tex").read_text(encoding="utf-8")
    assert "\\usepgfplotslibrary{statistics}" in tex
    assert "boxplot/draw direction=y" in tex
    assert (
        "\\addplot+[boxplot] table[y=measurement, col sep=comma] {data_Control.csv};"
        in tex
    )
    assert "xticklabels={Control, Low, Mid, High}" in tex


def test_heatmap_renders_matrix_and_cells(tmp_path):
    sandbox = tmp_path / "s"
    rows = [["x", "y", "value"]]
    for i in range(4):
        for j in range(4):
            rows.append([j, i, round(0.9 if i == j else 0.2, 3)])
    _write_csv(sandbox / "artifacts" / "data_corr.csv", ["x", "y", "value"], rows[1:])
    _write_spec(
        sandbox,
        {
            "chart_type": "heatmap",
            "heatmap": {
                "matrix_file": "data_corr.csv",
                "cols": 4,
                "colormap": "RdBu_r",
                "meta_min": -1,
                "meta_max": 1,
                "x_labels": ["a", "b", "c", "d"],
                "y_labels": ["a", "b", "c", "d"],
                "colorbar_label": "Pearson r",
                "cell_text": [{"row": 0, "col": 1, "text": "+0.20", "color": "black"}],
            },
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    tex = (sandbox / "artifacts" / "figure.tex").read_text(encoding="utf-8")
    assert "matrix plot, mesh/cols=4" in tex
    assert "colormap={mymap}{rgb255(0pt)=" in tex
    assert "colorbar style={ylabel={Pearson r}}" in tex
    assert "point meta min=-1" in tex
    assert "\\node[font=\\tiny, text=cellcolor0_1] at (axis cs:1,0) {+0.20};" in tex
    assert "y dir=reverse" in tex


def test_bar_renders_ybar_and_xticklabels(tmp_path):
    sandbox = tmp_path / "s"
    _write_csv(
        sandbox / "artifacts" / "data_north.csv",
        ["x_index", "sales"],
        [[0, 100], [1, 200], [2, 300]],
    )
    _write_spec(
        sandbox,
        {
            "chart_type": "bar",
            "ylabel": "Sales",
            "bar": {"xticklabels": ["A", "B", "C"]},
            "series": [
                {
                    "file": "data_north.csv",
                    "x": "x_index",
                    "y": "sales",
                    "label": "North",
                }
            ],
        },
    )
    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    tex = (sandbox / "artifacts" / "figure.tex").read_text(encoding="utf-8")
    assert "ybar" in tex
    assert "xtick={0,...,2}" in tex
    assert "xticklabels={A, B, C}" in tex
    assert "table[x=x_index, y=sales, col sep=comma]" in tex


def test_non_ascii_label_produces_warning(tmp_path):
    sandbox = tmp_path / "s"
    _write_csv(
        sandbox / "artifacts" / "data_A.csv",
        ["dose", "mean", "sem"],
        [[0.1, 0.5, 0.05]],
    )
    spec = _line_spec()
    spec["ylabel"] = "抑制率 (%)"
    _write_spec(sandbox, spec)
    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    assert any("Non-ASCII" in w for w in r["warnings"])


def test_special_chars_escaped_in_labels(tmp_path):
    sandbox = tmp_path / "s"
    _write_csv(
        sandbox / "artifacts" / "data_A.csv",
        ["x", "y"],
        [[1, 2]],
    )
    spec = {
        "chart_type": "line",
        "xlabel": "pH & conc_1 (%)",
        "series": [{"file": "data_A.csv", "x": "x", "y": "y", "label": "A&B"}],
    }
    _write_spec(sandbox, spec)
    r = generate_figure_tex(sandbox)
    assert r["generated"] is True
    tex = (sandbox / "artifacts" / "figure.tex").read_text(encoding="utf-8")
    assert "xlabel={pH \\& conc\\_1 (\\%)}" in tex
    assert "\\addlegendentry{A\\&B}" in tex

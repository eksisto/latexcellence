"""plot_gen 数据画像测试。"""

from pathlib import Path

import pytest

from backend.modules.plot_gen.data_parser import (
    ALLOWED_DATA_SUFFIXES,
    parse_data_file,
)


def test_parse_csv_profile(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_text(
        "group,value,time\n"
        "A,1.0,2024-01-01\n"
        "A,2.0,2024-01-02\n"
        "B,3.0,2024-01-03\n",
        encoding="utf-8",
    )
    profile = parse_data_file(path, max_rows=100)
    assert profile.rows == 3
    assert profile.columns == 3
    by_name = {c.name: c.inferred_type for c in profile.column_profiles}
    assert by_name["group"] == "categorical"
    assert by_name["value"] == "continuous"
    assert by_name["time"] in ("datetime", "categorical")


def test_parse_rejects_too_many_rows(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_text("x\n1\n2\n3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_data_file(path, max_rows=2)


def test_allowed_suffixes_contains_csv():
    assert ".csv" in ALLOWED_DATA_SUFFIXES


def test_parse_preview_contains_no_nan(tmp_path: Path):
    """前几行含缺失值时，preview 不应出现 NaN（保证 JSON 可序列化）。"""
    import json

    path = tmp_path / "data.csv"
    path.write_text("group,value\nA,1.5\nB,\nC,3.0\n", encoding="utf-8")
    profile = parse_data_file(path, max_rows=100)
    assert profile.preview == [
        {"group": "A", "value": 1.5},
        {"group": "B", "value": None},
        {"group": "C", "value": 3.0},
    ]
    # 模拟 Starlette JSONResponse（allow_nan=False），不得抛异常
    json.dumps(profile.model_dump(), allow_nan=False)


def test_infer_medium_cardinality_column_as_categorical(tmp_path: Path):
    """中等基数列（如 4 个取值/12 行）应判为 categorical，而非 unknown。"""
    path = tmp_path / "data.csv"
    path.write_text(
        "Treatment,value\n"
        "Control,1\nControl,2\nControl,3\n"
        "Low,4\nLow,5\nLow,6\n"
        "Medium,7\nMedium,8\nMedium,9\n"
        "High,10\nHigh,11\nHigh,12\n",
        encoding="utf-8",
    )
    profile = parse_data_file(path, max_rows=100)
    by_name = {c.name: c.inferred_type for c in profile.column_profiles}
    assert by_name["Treatment"] == "categorical"
    assert by_name["value"] == "continuous"

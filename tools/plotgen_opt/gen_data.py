"""生成 5 个科研制图场景的逼真测试数据集（含正常值/异常值/边界值）。

场景：
  s1_time_series  时间序列数据对比分析（三组 180 天）
  s2_correlation  多变量相关性热力图（12 个数值变量 + 批次/样本列）
  s3_distribution 实验结果统计分布图（4 个剂量组）
  s4_category     分类数据柱状/饼图（类别 x 地区销售额）
  s5_dose_response 领域特定：剂量-反应曲线（半对数坐标，IC50 比较）
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "data"


def seed_all(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)


def gen_time_series() -> pd.DataFrame:
    """三组每日观测，含趋势/季节项/噪声；注入缺失、尖峰异常值与近零边界值。"""
    dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(180)]
    groups = ["Control", "TreatmentA", "TreatmentB"]
    base = {"Control": 20.0, "TreatmentA": 24.0, "TreatmentB": 28.0}
    trend = {"Control": 0.02, "TreatmentA": 0.05, "TreatmentB": 0.09}
    rows: list[dict] = []
    for i, d in enumerate(dates):
        for g in groups:
            v = (
                base[g]
                + trend[g] * i
                + 3.0 * np.sin(2 * np.pi * i / 30)
                + np.random.normal(0, 1.2)
            )
            rows.append(
                {"date": d.isoformat(), "group": g, "value": round(float(v), 2)}
            )
    df = pd.DataFrame(rows)
    # 异常值：两个尖峰（5~6 倍正常波动）
    df.loc[df.index[37], "value"] = 48.5
    df.loc[df.index[120], "value"] = 4.0
    # 边界值：近零的负值（传感器下限）
    df.loc[df.index[15], "value"] = -0.05
    df.loc[df.index[88], "value"] = 0.0
    # 缺失值：随机 3 行
    for idx in [11, 77, 143]:
        df.loc[idx, "value"] = np.nan
    return df


def gen_correlation() -> pd.DataFrame:
    """12 个相关变量（3 个相关簇）+ batch 类别列 + sample_id。
    注入缺失值、一个离群列与极端值。"""
    n = 200
    rng = np.random.default_rng(7)
    c1 = rng.normal(size=(n, 4)) @ rng.normal(size=(4, 4)) * 0.6
    c2 = rng.normal(size=(n, 4)) @ rng.normal(size=(4, 4)) * 0.6
    c3 = rng.normal(size=(n, 4)) @ rng.normal(size=(4, 4)) * 0.6
    X = np.hstack([c1, c2, c3])
    cols = [
        "temp",
        "humidity",
        "pressure",
        "wind_speed",
        "pm25",
        "pm10",
        "no2",
        "so2",
        "o3",
        "co",
        "solar_radiation",
        "rainfall",
    ]
    df = pd.DataFrame(X, columns=cols)
    # 加量纲
    scales = {
        "temp": 30,
        "humidity": 60,
        "pressure": 20,
        "wind_speed": 8,
        "pm25": 50,
        "pm10": 80,
        "no2": 40,
        "so2": 10,
        "o3": 80,
        "co": 1.5,
        "solar_radiation": 400,
        "rainfall": 30,
    }
    for c in cols:
        df[c] = df[c] * scales[c] + scales[c]
    # 类别与 id 列
    df["batch"] = rng.choice(["B1", "B2", "B3"], size=n)
    df["sample_id"] = [f"S{i:04d}" for i in range(n)]
    # 缺失值（2%）
    for c in ["pm25", "o3", "rainfall"]:
        miss = rng.choice(n, size=int(n * 0.02), replace=False)
        df.loc[miss, c] = np.nan
    # 离群值（一个极端站点）
    df.loc[5, "pm25"] = 999.0
    df.loc[6, "no2"] = -5.0
    df.loc[7, "co"] = 12.0
    return df


def gen_distribution() -> pd.DataFrame:
    """4 个剂量组 x 40 样本；含偏态分布、组内离群值与零边界值。"""
    groups = ["Control", "Low", "Mid", "High"]
    means = {"Control": 10.0, "Low": 12.5, "Mid": 16.0, "High": 22.0}
    sds = {"Control": 1.8, "Low": 2.5, "Mid": 3.0, "High": 5.0}
    rng = np.random.default_rng(11)
    rows: list[dict] = []
    for g in groups:
        vals = rng.normal(means[g], sds[g], size=40)
        # 偏态：右尾
        vals = vals + rng.gamma(2, 0.8, size=40) * (g in ("Mid", "High"))
        for i, v in enumerate(vals):
            rows.append({"group": g, "measurement": round(float(v), 2)})
    df = pd.DataFrame(rows)
    # 每个组注入 1~2 个离群值（3~8 倍 IQR）
    outliers = [
        (3, 28.0),
        (17, 30.5),
        (42, 9.2),
        (60, 31.0),
        (95, 38.0),
        (120, 45.0),
    ]
    for idx, v in outliers:
        df.loc[idx, "measurement"] = v
    # 边界值：接近零的测量（检测下限）
    df.loc[38, "measurement"] = 0.1
    df.loc[150, "measurement"] = 0.0
    # 一个缺失值
    df.loc[70, "measurement"] = np.nan
    return df


def gen_category() -> pd.DataFrame:
    """6 类 x 4 地区销售额；含零销量、负值（退货）与大额异常订单。"""
    categories = ["Electronics", "Clothing", "Food", "Books", "Furniture", "Toys"]
    regions = ["North", "South", "East", "West"]
    base = {
        "Electronics": 48000,
        "Clothing": 32000,
        "Food": 26000,
        "Books": 9000,
        "Furniture": 15000,
        "Toys": 11000,
    }
    rng = np.random.default_rng(23)
    rows: list[dict] = []
    for c in categories:
        for r in regions:
            v = base[c] * rng.uniform(0.6, 1.5) * rng.choice([1.0, 1.0, 1.0, 0.9])
            rows.append({"category": c, "region": r, "sales": round(float(v), 2)})
    df = pd.DataFrame(rows)
    # 边界/异常值
    df.loc[0, "sales"] = 0.0  # 零销量
    df.loc[3, "sales"] = -1200.0  # 退货负值
    df.loc[17, "sales"] = 240000.0  # 大额异常订单
    return df


def gen_dose_response() -> pd.DataFrame:
    """3 种化合物 x 8 个剂量 x 3 重复；S 型曲线 + 噪声。
    注入越界值（>100%）、缺失值、零剂量对照。"""
    doses = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0]
    ic50 = {"DrugA": 0.8, "DrugB": 12.0, "DrugC": 300.0}
    hill = {"DrugA": 1.1, "DrugB": 0.9, "DrugC": 1.4}
    rng = np.random.default_rng(31)
    rows: list[dict] = []
    for compound, i50 in ic50.items():
        for dose in doses:
            for rep in range(1, 4):
                frac = 1.0 / (1.0 + (dose / i50) ** (-hill[compound]))
                resp = 100.0 * frac + rng.normal(0, 2.5)
                resp = min(max(resp, 0.0), 100.0)
                rows.append(
                    {
                        "compound": compound,
                        "dose": dose,
                        "replicate": rep,
                        "inhibition_pct": round(float(resp), 2),
                    }
                )
    df = pd.DataFrame(rows)
    # 零剂量对照（边界：0 剂量无法取对数）
    for compound in ic50:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    [
                        {
                            "compound": compound,
                            "dose": 0.0,
                            "replicate": 1,
                            "inhibition_pct": round(float(rng.normal(2, 1.5)), 2),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    # 越界值：>100% 的异常测量
    df.loc[3, "inhibition_pct"] = 118.0
    # 缺失值
    df.loc[20, "inhibition_pct"] = np.nan
    return df


SCENARIOS = {
    "s1_time_series": {
        "file": "s1_time_series.csv",
        "description": (
            "比较 Control、TreatmentA、TreatmentB 三组在 180 天内的每日观测值"
            "随时间的变化趋势，突出各组差异。"
        ),
        "style": "nature",
        "gen": gen_time_series,
    },
    "s2_correlation": {
        "file": "s2_correlation.xlsx",
        "description": (
            "分析 12 个环境监测变量的两两相关关系，绘制相关系数热力图，"
            "标注显著相关。"
        ),
        "style": "nature",
        "gen": gen_correlation,
    },
    "s3_distribution": {
        "file": "s3_distribution.csv",
        "description": (
            "比较 4 个剂量组的实验结果测量值分布，关注偏态和离群值，"
            "展示各组中位数与四分位距。"
        ),
        "style": "nature",
        "gen": gen_distribution,
    },
    "s4_category": {
        "file": "s4_category.csv",
        "description": (
            "展示各产品类别在各地区的销售额，比较不同类别之间的销售额大小。"
        ),
        "style": "nature",
        "gen": gen_category,
    },
    "s5_dose_response": {
        "file": "s5_dose_response.csv",
        "description": (
            "绘制三种化合物的剂量-反应曲线，横轴为剂量（对数坐标），"
            "纵轴为抑制率（%），比较各化合物的半数抑制浓度 IC50。"
        ),
        "style": "nature",
        "gen": gen_dose_response,
    },
}


def main() -> None:
    seed_all()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    for key, spec in SCENARIOS.items():
        df = spec["gen"]()
        path = OUT / spec["file"]
        if path.suffix == ".xlsx":
            df.to_excel(path, index=False)
        else:
            df.to_csv(path, index=False)
        manifest[key] = {
            "file": spec["file"],
            "description": spec["description"],
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "nulls": int(df.isna().sum().sum()),
        }
        print(f"{key}: {len(df)} rows x {len(df.columns)} cols -> {path.name}")
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

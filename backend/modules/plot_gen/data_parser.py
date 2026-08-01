"""数据文件解析与画像生成。"""

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.logger import get_logger
from backend.modules.plot_gen.models import ColumnProfile, DataProfile

logger = get_logger(__name__)

ALLOWED_DATA_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xls", ".json")


def normalize_filename(filename: str) -> str:
    """将上传文件名转换为安全的沙箱文件名。"""
    safe = Path(filename).name
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe)
    return safe or "data.csv"


def parse_data_file(path: Path, max_rows: int) -> DataProfile:
    """解析数据文件并生成画像。"""
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_DATA_SUFFIXES:
        raise ValueError(
            f"Unsupported file type: '{suffix}'. Supported: csv/tsv/xlsx/xls/json"
        )

    df = _read_dataframe(path, suffix)
    if len(df) > max_rows:
        raise ValueError(f"Data rows exceed the limit: {len(df)} > {max_rows}")

    columns = [_build_column_profile(name, df[name]) for name in df.columns]
    preview = _build_preview(df)
    summary = _build_raw_summary(df, columns)

    return DataProfile(
        file_name=path.name,
        rows=len(df),
        columns=len(df.columns),
        column_profiles=columns,
        preview=preview,
        raw_summary=summary,
    )


def _read_dataframe(path: Path, suffix: str) -> pd.DataFrame:
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin1"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Failed to decode CSV. Supported encodings: utf-8, gb18030")
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=0)
    if suffix == ".json":
        return pd.read_json(BytesIO(path.read_bytes()))
    raise ValueError(f"Unsupported file type: '{suffix}'")


def _build_preview(df: pd.DataFrame) -> list[dict[str, Any]]:
    """构建前几行预览，缺失值统一转为 None（避免 NaN 破坏 JSON 序列化）。"""
    result: list[dict[str, Any]] = []
    for _, row in df.head(5).iterrows():
        result.append({str(k): (None if pd.isna(v) else v) for k, v in row.items()})
    return result


def _build_column_profile(name: str, series: pd.Series) -> ColumnProfile:
    missing = int(series.isna().sum())
    missing_ratio = round(missing / len(series), 4) if len(series) else 0.0
    unique = int(series.nunique(dropna=True))
    sample = [None if pd.isna(v) else v for v in series.head(5).tolist()]

    inferred = _infer_type(series, unique, missing)
    quantiles = None
    top_values = None

    if inferred == "continuous":
        desc = series.describe()
        quantiles = [float(desc.get(q, 0)) for q in ("min", "25%", "50%", "75%", "max")]
    elif inferred in ("categorical", "id"):
        top_values = [
            str(v) for v in series.dropna().value_counts().head(8).index.tolist()
        ]

    return ColumnProfile(
        name=str(name),
        inferred_type=inferred,
        dtype=str(series.dtype),
        missing=missing,
        missing_ratio=missing_ratio,
        unique=unique,
        sample=sample,
        quantiles=quantiles,
        top_values=top_values,
    )


def _infer_type(series: pd.Series, unique: int, missing: int) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "continuous"
    if isinstance(series.dtype, pd.StringDtype) or series.dtype == object:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
        if parsed.notna().sum() >= max(1, int(len(series) * 0.8)):
            return "datetime"
    n = len(series) - missing
    # 只有行数足够多时全唯一列才判定为 id，避免小样本分类列被误判
    if n >= 10 and unique == n:
        return "id"
    if unique <= max(2, min(int(n * 0.5), 30)):
        return "categorical"
    return "unknown"


def _build_raw_summary(df: pd.DataFrame, columns: list[ColumnProfile]) -> str:
    lines = [f"Shape: {len(df)} rows x {len(df.columns)} columns"]
    for col in columns:
        lines.append(
            f"- {col.name}: {col.inferred_type} | missing={col.missing_ratio} | "
            f"unique={col.unique}"
        )
    return "\n".join(lines)


def redundancy_hint_pairs(profile: DataProfile) -> list[tuple[str, str]]:
    """找出“一个单元格只有一行”的 (x, 分类列) 组合。

    当 unique(x) * unique(cat) ≈ 行数 时，说明 x × cat 的每个组合只有一条记录，
    对该组合做 mean/sd 聚合或加误差棒没有意义，且会显著增加 TikZ 输出复杂度。
    """
    rows = profile.rows
    if rows <= 0:
        return []
    by_name = {c.name: c for c in profile.column_profiles}
    cats = [
        c.name
        for c in profile.column_profiles
        if c.inferred_type == "categorical" and c.unique >= 1
    ]
    others = [
        c.name
        for c in profile.column_profiles
        if c.inferred_type in ("datetime", "continuous", "id") and c.unique >= 2
    ]
    pairs: list[tuple[str, str]] = []
    for cat in cats:
        for other in others:
            product = by_name[cat].unique * by_name[other].unique
            if product and abs(product - rows) <= max(1, int(rows * 0.05)):
                pairs.append((other, cat))
    return pairs


def build_profile_context(profile: DataProfile) -> str:
    """生成给 LLM 的完整数据画像上下文（原始摘要 + 列明细 + 预览）。

    列明细包含 top_values / quantiles / sample，帮助 LLM 理解列语义，
    从而减少其通过 read_file 读取原始数据的需要。
    """
    parts = [profile.raw_summary]
    detail_lines = []
    for c in profile.column_profiles:
        detail = f"- {c.name}: {c.inferred_type} | unique={c.unique}"
        extras = []
        if c.top_values:
            extras.append("top=" + ", ".join(str(v) for v in c.top_values[:6]))
        if c.quantiles:
            extras.append("quantiles=" + ", ".join(f"{q:.2g}" for q in c.quantiles))
        elif c.sample:
            extras.append("sample=" + ", ".join(str(v) for v in c.sample[:6]))
        if extras:
            detail += " | " + " | ".join(extras)
        detail_lines.append(detail)
    parts.append("Columns:")
    parts.extend(detail_lines)
    parts.append("Preview (first rows):")
    parts.append(json.dumps(profile.preview[:5], ensure_ascii=False, default=str))
    hints = redundancy_hint_pairs(profile)
    if hints:
        parts.append("Redundancy hints (one row per cell -> aggregation/error bars NOT needed):")
        for other, cat in hints[:3]:
            parts.append(
                f"- {other} x {cat}: unique product == rows ({profile.rows})"
            )
    return "\n".join(parts)

"""数据文件解析与画像生成。"""

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
    if len(series) > 0 and unique == len(series) - missing:
        return "id"
    if unique <= max(2, int(len(series) ** 0.5)):
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

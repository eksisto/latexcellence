"""受控 PGFPlots 生成器（确定性 TikZ 渲染，替代 matplot2tikz）。

Agent 脚本只负责计算数据并写出声明式 spec，后端据此确定性渲染
``artifacts/figure.tex``，避免 LLM 手写 PGFPlots 的语法/列名错误，
以及 matplot2tikz 0.5.4 对误差棒、日期、色图兼容性差的问题。

数据契约（由脚本写入沙箱）：
    - ``artifacts/tikz_spec.json``：图表声明（本模块校验渲染）
    - ``artifacts/data_*.csv``：每个系列/分组的绘图数据（ASCII 列名）

本模块不调用 LLM、不执行 Python，纯后端确定性渲染。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import pandas as pd

from backend.core.logger import get_logger
from backend.utils.json_utils import format_validation_error
from backend.utils.path_guard import resolve_project_path

# PGFPlots 预置色图白名单（可直接用 colormap/<name> 引用）
PGFPLOTS_COLORMAPS = {
    "viridis",
    "hot",
    "jet",
    "cool",
    "blackwhite",
    "gray",
    "bluered",
    "greenyellow",
    "redyellow",
}

# matplotlib RdBu_r 11 段色标（与 matplot2tikz 输出一致，PGFPlots 无内置）
RDBU_R_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (5, 48, 97)),
    (0.1, (33, 102, 172)),
    (0.2, (67, 147, 195)),
    (0.3, (146, 197, 222)),
    (0.4, (209, 229, 240)),
    (0.5, (247, 247, 247)),
    (0.6, (253, 219, 199)),
    (0.7, (244, 165, 130)),
    (0.8, (214, 96, 77)),
    (0.9, (178, 24, 43)),
    (1.0, (103, 0, 31)),
]
RDBU_STOPS = [(1.0 - p, rgb) for p, rgb in reversed(RDBU_R_STOPS)]

# matplotlib 常用颜色名 → RGB（不引入 matplotlib 到后端进程）
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 128, 0),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "steelblue": (70, 130, 180),
    "orange": (255, 127, 14),
    "forestgreen": (34, 139, 34),
    "darkorange": (255, 140, 0),
    "purple": (128, 0, 128),
    "brown": (165, 42, 42),
    "pink": (255, 192, 203),
    "cyan": (0, 255, 255),
    "olive": (128, 128, 0),
    # matplotlib tab10 默认色环
    "C0": (31, 119, 180),
    "C1": (255, 127, 14),
    "C2": (44, 160, 44),
    "C3": (214, 39, 40),
    "C4": (148, 103, 189),
    "C5": (140, 86, 75),
    "C6": (227, 119, 194),
    "C7": (127, 127, 127),
    "C8": (188, 189, 34),
    "C9": (23, 190, 207),
}

LEGEND_POSITIONS = {
    "north east",
    "north west",
    "south east",
    "south west",
    "north",
    "south",
    "east",
    "west",
}


class SeriesSpec(BaseModel):
    """单个系列/分组：数据文件 + 列映射。"""

    model_config = ConfigDict(extra="forbid")

    file: str
    x: str
    y: str
    y_plus: str | None = None
    y_minus: str | None = None
    label: str | None = None
    mark: str | None = None
    color: str | None = None


class LegendSpec(BaseModel):
    """图例设置。"""

    model_config = ConfigDict(extra="forbid")

    show: bool = True
    pos: str = "north east"


class BarSpec(BaseModel):
    """柱状图设置（类别坐标用整数索引 + xticklabels 展示）。"""

    model_config = ConfigDict(extra="forbid")

    xticklabels: list[str] = []
    group_labels: list[str] = []


class BoxSpec(BaseModel):
    """箱线图设置。"""

    model_config = ConfigDict(extra="forbid")

    xticklabels: list[str] = []


class CellSpec(BaseModel):
    """热力图单元格文字标注。"""

    model_config = ConfigDict(extra="forbid")

    row: int
    col: int
    text: str
    color: str = "black"


class HeatmapSpec(BaseModel):
    """热力图设置（矩阵数据为长表：x, y, value）。"""

    model_config = ConfigDict(extra="forbid")

    matrix_file: str
    cols: int = Field(gt=0)
    x_labels: list[str] = []
    y_labels: list[str] = []
    colormap: str = "viridis"
    colormap_stops: list[tuple[float, list[float]]] | None = None
    meta_min: float | None = None
    meta_max: float | None = None
    colorbar_label: str | None = None
    cell_text: list[CellSpec] = []


class TikzSpec(BaseModel):
    """tikz_spec.json 顶层声明。"""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal[
        "line", "scatter", "bar", "box", "heatmap", "histogram", "kde", "violin"
    ]
    xlabel: str | None = None
    ylabel: str | None = None
    xmode: Literal["linear", "log"] = "linear"
    ymode: Literal["linear", "log"] = "linear"
    xmin: float | None = None
    xmax: float | None = None
    ymin: float | None = None
    ymax: float | None = None
    grid: bool = True
    legend: LegendSpec = LegendSpec()
    series: list[SeriesSpec] = []
    bar: BarSpec | None = None
    box: BoxSpec | None = None
    heatmap: HeatmapSpec | None = None

    @model_validator(mode="after")
    def _check_required_sections(self) -> "TikzSpec":
        ct = self.chart_type
        if ct == "heatmap" and self.heatmap is None:
            raise ValueError("chart_type 'heatmap' requires the 'heatmap' sub-spec")
        if ct in ("box", "violin") and not self.series:
            raise ValueError(
                f"chart_type '{ct}' requires at least one 'series' entry "
                "(each group = one CSV)"
            )
        if ct in ("line", "scatter", "bar", "histogram", "kde") and not self.series:
            raise ValueError(f"chart_type '{ct}' requires at least one 'series' entry")
        if self.legend.pos not in LEGEND_POSITIONS:
            raise ValueError(
                f"legend.pos must be one of: {', '.join(sorted(LEGEND_POSITIONS))}"
            )
        return self


def _escape(text: str) -> str:
    """转义 PGFPlots 文本中的特殊字符。"""
    return (
        text.replace("\\", r"\textbackslash{}")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("&", r"\&")
        .replace("_", r"\_")
    )


def _color_to_rgb(color: str) -> tuple[int, int, int] | None:
    """把 matplotlib 颜色名/hex 转为 RGB 三元组；无法识别返回 None。"""
    c = color.strip()
    if c in NAMED_COLORS:
        return NAMED_COLORS[c]
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", c)
    if m:
        v = int(m.group(1), 16)
        return (v >> 16, (v >> 8) & 0xFF, v & 0xFF)
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", c)
    if m:
        v = m.group(1)
        return tuple(int(ch * 2, 16) for ch in v)  # type: ignore[return-value]
    return None


def _colormap_options(hm: HeatmapSpec) -> list[str]:
    """生成色图相关 axis 选项。"""
    opts: list[str] = []
    stops = hm.colormap_stops
    name = (hm.colormap or "viridis").strip()
    if stops:
        pieces = []
        for pos, rgb in sorted(stops):
            if len(rgb) != 3:
                continue
            r, g, b = (max(0, min(255, int(v))) for v in rgb)
            pieces.append(f"rgb255({pos * 1000:.0f}pt)=({r},{g},{b})")
        if pieces:
            opts.append("colormap={mymap}{" + "; ".join(pieces) + "}")
        else:
            opts.append("colormap/viridis")
    elif name.lower() == "rdbu_r":
        pieces = [
            f"rgb255({p * 1000:.0f}pt)=({r},{g},{b})" for p, (r, g, b) in RDBU_R_STOPS
        ]
        opts.append("colormap={mymap}{" + "; ".join(pieces) + "}")
    elif name.lower() == "rdbu":
        pieces = [
            f"rgb255({p * 1000:.0f}pt)=({r},{g},{b})" for p, (r, g, b) in RDBU_STOPS
        ]
        opts.append("colormap={mymap}{" + "; ".join(pieces) + "}")
    elif name.lower() in PGFPLOTS_COLORMAPS:
        opts.append(f"colormap/{name.lower()}")
    else:
        # 未知名称回退 viridis（不阻断生成）
        opts.append("colormap/viridis")
    if hm.meta_min is not None:
        opts.append(f"point meta min={hm.meta_min:g}")
    if hm.meta_max is not None:
        opts.append(f"point meta max={hm.meta_max:g}")
    return opts


def _axis_options(spec: TikzSpec) -> list[str]:
    """生成 \begin{axis}[...] 选项。"""
    opts: list[str] = []
    if spec.xlabel:
        opts.append(f"xlabel={{{_escape(spec.xlabel)}}}")
    if spec.ylabel:
        opts.append(f"ylabel={{{_escape(spec.ylabel)}}}")
    if spec.xmode == "log":
        opts.append("xmode=log")
    if spec.ymode == "log":
        opts.append("ymode=log")
    if spec.xmin is not None:
        opts.append(f"xmin={spec.xmin:g}")
    if spec.xmax is not None:
        opts.append(f"xmax={spec.xmax:g}")
    if spec.ymin is not None:
        opts.append(f"ymin={spec.ymin:g}")
    if spec.ymax is not None:
        opts.append(f"ymax={spec.ymax:g}")
    if spec.grid:
        opts.append("grid=both")

    ct = spec.chart_type
    if ct in ("bar", "histogram"):
        opts.append("ybar")
        opts.append("bar width=8pt")
        xticklabels = (spec.bar.xticklabels if spec.bar else []) or []
        if xticklabels:
            opts.append(f"xtick={{0,...,{len(xticklabels) - 1}}}")
            opts.append(
                "xticklabels={" + ", ".join(_escape(x) for x in xticklabels) + "}"
            )
            # 类别多或标签长时水平排布必然重叠，确定性旋转（热力图同款规则）
            max_label_len = max((len(str(x)) for x in xticklabels), default=0)
            if (len(xticklabels) >= 5 and max_label_len >= 6) or max_label_len >= 10:
                opts.append(
                    "xticklabel style={rotate=45,anchor=east,font=\\scriptsize}"
                )
        elif spec.bar and spec.bar.group_labels:
            # 无 x 类别时按组标签展示
            opts.append(
                "symbolic x coords={"
                + ", ".join(_escape(x) for x in spec.bar.group_labels)
                + "}"
            )
    elif ct in ("box", "violin"):
        opts.append("boxplot/draw direction=y")
        n = len(spec.series)
        xticklabels = (spec.box.xticklabels if spec.box else []) or []
        if xticklabels:
            opts.append(f"xtick={{1,...,{len(xticklabels)}}}")
            opts.append(
                "xticklabels={" + ", ".join(_escape(x) for x in xticklabels) + "}"
            )
        elif n:
            opts.append(f"xtick={{1,...,{n}}}")
    elif ct == "heatmap" and spec.heatmap is not None:
        opts.extend(_colormap_options(spec.heatmap))
        opts.append("colorbar")
        if spec.heatmap.colorbar_label:
            opts.append(
                f"colorbar style={{ylabel={{{_escape(spec.heatmap.colorbar_label)}}}}}"
            )
        if spec.heatmap.x_labels:
            opts.append("xtick={0,...," + str(len(spec.heatmap.x_labels) - 1) + "}")
            opts.append(
                "xticklabels={"
                + ", ".join(_escape(x) for x in spec.heatmap.x_labels)
                + "}"
            )
            opts.append("xticklabel style={rotate=45,anchor=east,font=\\scriptsize}")
        if spec.heatmap.y_labels:
            opts.append("ytick={0,...," + str(len(spec.heatmap.y_labels) - 1) + "}")
            opts.append(
                "yticklabels={"
                + ", ".join(_escape(x) for x in spec.heatmap.y_labels)
                + "}"
            )
            opts.append("yticklabel style={font=\\scriptsize}")
        opts.append("y dir=reverse")

    if spec.legend.show and ct not in ("box", "violin"):
        opts.append(f"legend pos={spec.legend.pos}")
        # 半透明白色底衬降低图例对数据线的遮挡，同时保持可读性
        opts.append("legend style={fill=white, fill opacity=0.8, draw=none}")
    return opts


def _mark_options(series: SeriesSpec, ct: str, color_name: str | None) -> str:
    """单系列 addplot 选项。"""
    parts: list[str] = []
    if ct in ("box", "violin"):
        parts.append("boxplot")
    elif ct == "scatter":
        parts.append("only marks")
        parts.append(series.mark or "mark=*")
    elif series.mark:
        parts.append(f"mark={series.mark}")
    else:
        parts.append("mark=none")
    if color_name is not None:
        parts.append(color_name)
    return ", ".join(parts) if parts else ""


def _table_opts(series: SeriesSpec, ct: str) -> str:
    if ct in ("box", "violin"):
        # 箱线图按组读取单列数据，x 列无意义且 CSV 中不存在
        opts = f"y={series.y}"
    else:
        opts = f"x={series.x}, y={series.y}"
    # 误差列是 table 选项（声明列名），但 PGFPlots 默认不绘制误差棒，
    # 还需在 \addplot+[...] 里启用 error bars/.cd, y dir=both, y explicit
    # 才会真正画出（由 _error_bar_options 注入）。
    if series.y_plus:
        opts += f", y error plus={series.y_plus}"
    if series.y_minus:
        opts += f", y error minus={series.y_minus}"
    opts += ", col sep=comma"
    return opts


def _error_bar_enable() -> str:
    """返回 addplot 选项前缀，启用误差棒绘制（table 选项声明列名之后必需）。"""
    return "error bars/.cd, y dir=both, y explicit"


def _render(spec: TikzSpec) -> str:
    """把校验通过的 spec 渲染为 PGFPlots 片段。"""
    ct = spec.chart_type
    lines: list[str] = [
        "% This file was generated by LateXcellence tikzgen (controlled PGFPlots).",
        "% Regenerate by rerunning python_sandbox after editing tikz_spec.json.",
    ]
    if ct in ("box", "violin"):
        lines.insert(2, r"\usepgfplotslibrary{statistics}")
    # 误差棒是 pgfplots 核心原生能力：在 addplot 启用
    # `error bars/.cd, y dir=both, y explicit`，table 选项给
    # `y error plus`/`y error minus` 列名即可绘制，无需加载额外 library。

    # 自定义颜色定义
    color_defs: list[str] = []
    color_map: dict[str, str] = {}
    for idx, s in enumerate(spec.series):
        rgb = _color_to_rgb(s.color) if s.color else None
        if rgb is None:
            continue
        cname = f"plotcolor{idx}"
        color_map[id(s)] = cname
        color_defs.append(
            f"\\definecolor{{{cname}}}{{RGB}}{{{rgb[0]},{rgb[1]},{rgb[2]}}}"
        )
    lines.extend(color_defs)

    lines.append(r"\begin{tikzpicture}")
    opts = _axis_options(spec)
    if opts:
        lines.append(r"\begin{axis}[" + "\n  " + ",\n  ".join(opts) + "\n]")
    else:
        lines.append(r"\begin{axis}")

    if ct == "heatmap" and spec.heatmap is not None:
        hm = spec.heatmap
        lines.append(
            r"\addplot[matrix plot, mesh/cols="
            + str(hm.cols)
            + r"] table[x=x, y=y, meta=value, col sep=comma] {"
            + hm.matrix_file
            + "};"
        )
        for cell in hm.cell_text:
            rgb = _color_to_rgb(cell.color) or (0, 0, 0)
            cell_color = f"cellcolor{cell.row}_{cell.col}"
            lines.append(
                f"\\definecolor{{{cell_color}}}{{RGB}}{{{rgb[0]},{rgb[1]},{rgb[2]}}}"
            )
            lines.append(
                f"\\node[font=\\scriptsize, text={cell_color}] at "
                f"(axis cs:{cell.col},{cell.row}) "
                f"{{{_escape(cell.text)}}};"
            )
    else:
        for s in spec.series:
            mo = _mark_options(s, ct, color_map.get(id(s)))
            if ct in ("bar", "histogram"):
                mo = f"{mo}, ybar".strip(", ")
            if s.y_plus or s.y_minus:
                mo = (mo + ", " if mo else "") + _error_bar_enable()
            addplot = (
                f"\\addplot+[{mo}] table[{_table_opts(s, ct)}] {{{s.file}}};"
                if mo
                else f"\\addplot+ table[{_table_opts(s, ct)}] {{{s.file}}};"
            )
            lines.append(addplot)
            if spec.legend.show and s.label and ct not in ("box", "violin"):
                lines.append(f"\\addlegendentry{{{_escape(s.label)}}}")

    lines.append(r"\end{axis}")
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def _check_non_ascii(spec: TikzSpec) -> list[str]:
    """检查声明文本中的非 ASCII 字符，返回警告。"""
    texts: list[str] = []
    if spec.xlabel:
        texts.append(spec.xlabel)
    if spec.ylabel:
        texts.append(spec.ylabel)
    if spec.heatmap is not None:
        texts.extend(spec.heatmap.x_labels)
        texts.extend(spec.heatmap.y_labels)
        if spec.heatmap.colorbar_label:
            texts.append(spec.heatmap.colorbar_label)
        texts.extend(c.text for c in spec.heatmap.cell_text)
    if spec.bar is not None:
        texts.extend(spec.bar.xticklabels)
    if spec.box is not None:
        texts.extend(spec.box.xticklabels)
    for s in spec.series:
        if s.label:
            texts.append(s.label)
    chars = sorted({ch for text in texts for ch in text if ord(ch) > 0x7F})
    if not chars:
        return []
    sample = " ".join(repr(c) for c in chars[:8])
    return [
        "Non-ASCII characters detected in tikz_spec.json labels "
        f"({sample}). They render only with xelatex plus a CJK-capable font "
        "(the backend auto-detects the engine). If the user did not request "
        "non-ASCII labels, use ASCII equivalents (e.g. 'uM', 'degC'); if "
        "missing-glyph/font errors appear, fall back to ASCII."
    ]


def _read_csv(artifacts_dir: Path, ref: str) -> tuple[pd.DataFrame | None, str | None]:
    """读取 artifacts/ 下的 CSV/TSV，返回 (df, error)。"""
    try:
        p = resolve_project_path(
            artifacts_dir.parent,
            f"artifacts/{ref}",
            allowed_suffixes=(".csv", ".tsv", ".txt"),
        )
    except ValueError as e:
        return None, f"{ref} (path rejected: {e})"
    if not p.exists():
        return None, ref
    try:
        sep = "\t" if p.suffix.lower() == ".tsv" else ","
        return pd.read_csv(p, sep=sep), None
    except Exception as e:  # noqa: BLE001
        return None, f"{ref} (unreadable: {e})"


def _validate_numeric(df: pd.DataFrame, col: str, ref: str) -> str | None:
    """检查列存在且可全部转数值，返回错误描述或 None。"""
    if col not in df.columns:
        return f"column '{col}' not found in {ref}"
    coerced = pd.to_numeric(df[col], errors="coerce")
    bad = coerced.isna() & df[col].notna()
    if bad.any():
        samples = ", ".join(repr(v) for v in df.loc[bad, col].head(5))
        return (
            f"column '{col}' in {ref} contains {int(bad.sum())} non-numeric "
            f"value(s) (e.g. {samples}); convert it to numbers first"
        )
    return None


def _validate_error_column(
    df: pd.DataFrame, col: str, ref: str, kind: str
) -> str | None:
    """误差列（y_plus/y_minus）：存在性 + 数值性 + 空值检查。

    空单元格（单重复组 sem/sd 为 NaN 的常见来源）会让 PGFPlots 静默丢弃
    误差棒——编译成功但图是错的，必须在生成期阻断并给出精确修法。
    """
    numeric_error = _validate_numeric(df, col, ref)
    if numeric_error is not None:
        return f"{numeric_error} (referenced as {kind})"
    coerced = pd.to_numeric(df[col], errors="coerce")
    na_rows = [int(i) for i in df.index[coerced.isna()][:5]]
    if na_rows:
        return (
            f"column '{col}' ({kind}) in {ref} has empty value(s) at data "
            f"row(s) {na_rows} — PGFPlots silently drops error bars for empty "
            "cells. This usually comes from single-replicate groups whose "
            "sem/sd is NaN: fill with 0 (e.g. agg['sem'] = "
            "agg['sem'].fillna(0)) or drop those rows"
        )
    return None


def _validate_log_positive(
    spec: TikzSpec, df: pd.DataFrame, col: str, ref: str, axis: str
) -> str | None:
    """对数轴坐标必须严格为正，否则 PGFPlots 静默丢点。"""
    mode = spec.xmode if axis == "x" else spec.ymode
    if mode != "log" or col not in df.columns:
        return None
    values = pd.to_numeric(df[col], errors="coerce")
    nonpos = values <= 0
    if not bool(nonpos.any()):
        return None
    samples = ", ".join(f"{v:g}" for v in values[nonpos].head(3))
    return (
        f"column '{col}' in {ref} has {int(nonpos.sum())} non-positive "
        f"value(s) (e.g. {samples}) but {axis}mode=log — PGFPlots silently "
        "drops non-positive coordinates on a log axis. Filter them out in "
        f"the script (e.g. df = df[df['{col}'] > 0]) before writing the CSV"
    )


def _derive_bar_labels(spec: TikzSpec, loaded: dict[str, pd.DataFrame]) -> list[str]:
    """系列 CSV 含 category 文本列而 xticklabels 缺失时，按 x 索引推导标签。

    索引不干净或系列间类别不一致时返回空列表（不推导），交由结构校验报错。
    """
    derived: dict[int, str] = {}
    for s in spec.series:
        df = loaded.get(s.file)
        if df is None or "category" not in df.columns or s.x not in df.columns:
            continue
        x_vals = pd.to_numeric(df[s.x], errors="coerce")
        if bool(x_vals.isna().any()):
            return []
        for xi, cat in zip(x_vals, df["category"].astype(str)):
            idx = int(xi)
            if idx in derived and derived[idx] != cat:
                return []
            derived[idx] = cat
    if not derived:
        return []
    n = max(derived) + 1
    if set(derived) != set(range(n)):
        return []
    return [derived[i] for i in range(n)]


def _validate_bar_series(spec: TikzSpec, loaded: dict[str, pd.DataFrame]) -> list[str]:
    """柱状图结构校验：索引完整覆盖 + y 非空 + 类别顺序交叉验证。

    缺行（某类别无柱）、重复索引（同槽双柱）、y 空值与 xticklabels 顺序
    错位都是静默数据保真问题——PGFPlots 编译成功但图是错的，必须在
    生成期阻断。系列 CSV 含 ``category`` 文本列时逐行核对
    ``xticklabels[x] == category``。
    """
    labels = (spec.bar.xticklabels if spec.bar else []) or []
    if not labels:
        return []
    n = len(labels)
    expected = set(range(n))
    problems: list[str] = []
    for s in spec.series:
        df = loaded.get(s.file)
        if df is None or s.x not in df.columns:
            continue
        x_vals = pd.to_numeric(df[s.x], errors="coerce")
        if bool((x_vals.isna() & df[s.x].notna()).any()):
            continue  # 非数值字符串已由 _validate_numeric 报告
        clean = x_vals.dropna()
        if bool((clean % 1 != 0).any()):
            problems.append(
                f"{s.file}: bar x values must be integer indices 0..{n - 1}, "
                "not fractional numbers"
            )
            continue
        present = {int(v) for v in clean}
        if len(clean) != len(present):
            problems.append(
                f"{s.file}: duplicate x index — each category slot may appear "
                "only once per series (duplicates draw two bars in one slot)"
            )
        extra = sorted(present - expected)
        if extra:
            problems.append(
                f"{s.file}: x index {extra} out of range 0..{n - 1} "
                f"(bar.xticklabels has {n} entries)"
            )
        missing = sorted(expected - present)
        if missing:
            named = ", ".join(f"{i} ('{labels[i]}')" for i in missing[:5])
            problems.append(
                f"{s.file}: missing category x index {named} — every series "
                f"CSV must contain exactly one row per category index "
                f"0..{n - 1}; a missing row renders as a missing bar. If the "
                "category truly has no value, write an explicit 0"
            )
        if s.y in df.columns:
            y_empty = pd.to_numeric(df[s.y], errors="coerce").isna() & df[s.y].isna()
            if bool(y_empty.any()):
                rows = [int(i) for i in df.index[y_empty][:5]]
                problems.append(
                    f"{s.file}: column '{s.y}' has empty value(s) at data "
                    f"row(s) {rows} — renders as missing bar(s); write an "
                    "explicit 0 for empty categories"
                )
        if "category" in df.columns and not bool(x_vals.isna().any()):
            mismatches: list[str] = []
            for xi, cat in zip(x_vals, df["category"].astype(str)):
                idx = int(xi)
                if 0 <= idx < n and labels[idx] != cat:
                    mismatches.append(
                        f"x={idx}: CSV category '{cat}' != "
                        f"xticklabels[{idx}] '{labels[idx]}'"
                    )
                if len(mismatches) >= 5:
                    break
            if mismatches:
                problems.append(
                    f"{s.file}: category column does not match "
                    f"bar.xticklabels order ({'; '.join(mismatches)}). Keep "
                    "the category order identical across every series CSV "
                    "and bar.xticklabels (x index i must mean xticklabels[i])"
                )
    return problems


def _validate_csvs(spec: TikzSpec, artifacts_dir: Path) -> tuple[str | None, list[str]]:
    """渲染前校验引用 CSV 的列/数值类型/网格维度，返回 (错误消息, 警告列表)。

    把本应在 latexmk 阶段才暴露的列名/数值错误提前到生成阶段，
    避免 Agent 在编译错误上反复试错浪费轮次；同时拦截静默数据保真
    问题（误差棒空值、对数轴非正坐标、柱状图缺柱/标签错位）。
    柱状图系列含 category 列而 xticklabels 缺失时，此处推导并回填 spec。
    """
    problems: list[str] = []
    warnings: list[str] = []
    loaded: dict[str, pd.DataFrame] = {}
    for s in spec.series:
        df, err = _read_csv(artifacts_dir, s.file)
        if err is not None:
            problems.append(f"data file {err} is missing; write it into artifacts/")
            continue
        loaded[s.file] = df
        if spec.chart_type in ("box", "violin"):
            problems.extend(
                p for p in [_validate_numeric(df, s.y, s.file)] if p is not None
            )
        else:
            problems.extend(
                p
                for p in [
                    _validate_numeric(df, s.x, s.file),
                    _validate_numeric(df, s.y, s.file),
                    _validate_log_positive(spec, df, s.x, s.file, "x"),
                    _validate_log_positive(spec, df, s.y, s.file, "y"),
                ]
                if p is not None
            )
        if s.y_plus:
            problems.extend(
                p
                for p in [_validate_error_column(df, s.y_plus, s.file, "y_plus")]
                if p is not None
            )
        if s.y_minus:
            problems.extend(
                p
                for p in [_validate_error_column(df, s.y_minus, s.file, "y_minus")]
                if p is not None
            )

    if spec.chart_type == "bar":
        if not ((spec.bar.xticklabels if spec.bar else []) or []):
            derived = _derive_bar_labels(spec, loaded)
            if derived:
                if spec.bar is None:
                    spec.bar = BarSpec(xticklabels=derived)
                else:
                    spec.bar.xticklabels = derived
                warnings.append(
                    "bar.xticklabels was empty; derived from the category "
                    f"column: {derived}"
                )
        problems.extend(_validate_bar_series(spec, loaded))
        # 负值柱状图：PGFPlots 的 ybar 默认把 ymin 裁到 0，负值柱会不可见。
        # 数据含负值时确定性注入 ymin，保证柱体向下延伸（用户显式指定除外）。
        if not problems:
            y_values: list[float] = []
            for s in spec.series:
                df = loaded.get(s.file)
                if df is None or s.y not in df.columns:
                    continue
                vals = pd.to_numeric(df[s.y], errors="coerce").dropna()
                if len(vals):
                    y_values.extend([float(vals.min()), float(vals.max())])
            if y_values:
                min_y = min(y_values)
                if min_y < 0 and spec.ymin is None:
                    spec.ymin = min_y * 1.1

    if spec.heatmap is not None:
        hm = spec.heatmap
        df, err = _read_csv(artifacts_dir, hm.matrix_file)
        if err is not None:
            problems.append(
                f"heatmap data file {err} is missing; write it into artifacts/"
            )
        else:
            loaded[hm.matrix_file] = df
            for col in ("x", "y", "value"):
                problems.extend(
                    p
                    for p in [_validate_numeric(df, col, hm.matrix_file)]
                    if p is not None
                )
            if problems:
                return (
                    "TikZ spec data validation failed:\n- " + "\n- ".join(problems),
                    warnings,
                )
            n = hm.cols
            x_vals = pd.to_numeric(df["x"], errors="coerce")
            y_vals = pd.to_numeric(df["y"], errors="coerce")
            # 空列/全 NaN 列在此拦截，避免后续 int(min()) 抛 ValueError
            if x_vals.notna().sum() == 0 or y_vals.notna().sum() == 0:
                problems.append(
                    f"heatmap {hm.matrix_file} columns 'x'/'y' must be non-empty "
                    "integer indices 0..n-1 (found empty column)"
                )
            else:
                x_is_int = bool((x_vals.dropna() % 1 == 0).all())
                y_is_int = bool((y_vals.dropna() % 1 == 0).all())
                xmin, xmax = int(x_vals.min()), int(x_vals.max())
                ymin, ymax = int(y_vals.min()), int(y_vals.max())
                if not x_is_int or not y_is_int:
                    problems.append(
                        f"heatmap x/y values in {hm.matrix_file} must be integer "
                        "indices 0..n-1, not fractional/other numbers"
                    )
                if xmin < 0 or xmax > n - 1 or x_vals.nunique() != n:
                    problems.append(
                        f"heatmap x values in {hm.matrix_file} must be integer indices "
                        f"0..{n - 1} (found {xmin}..{xmax}, {x_vals.nunique()} unique); "
                        "write row/column indices, not variable names — display labels "
                        "go into heatmap.x_labels/y_labels"
                    )
                if ymin < 0 or ymax > n - 1 or y_vals.nunique() != n:
                    problems.append(
                        f"heatmap y values in {hm.matrix_file} must be integer indices "
                        f"0..{n - 1} (found {ymin}..{ymax}, {y_vals.nunique()} unique)"
                    )
            if len(df) != n * n:
                problems.append(
                    f"heatmap {hm.matrix_file} must contain {n * n} rows "
                    f"({n} x-values x {n} y-values), found {len(df)}"
                )
            if hm.x_labels and len(hm.x_labels) != n:
                problems.append(
                    f"heatmap.x_labels has {len(hm.x_labels)} entries but cols={n}"
                )
            if hm.y_labels and len(hm.y_labels) != n:
                problems.append(
                    f"heatmap.y_labels has {len(hm.y_labels)} entries but cols={n}"
                )
            # 发散色图（RdBu_r/RdBu）适合正负都有且跨越零点的数据；数据
            # 单边（全正或全负）时发散色图只剩一半色阶，视觉上难区分强度，
            # 确定性回退到感知均匀的 viridis 并告知 Agent。
            if not problems:
                values = pd.to_numeric(df["value"], errors="coerce").dropna()
                if len(values):
                    vmin, vmax = float(values.min()), float(values.max())
                    if hm.colormap.strip().lower() in ("rdbu_r", "rdbu") and (
                        vmin >= 0 or vmax <= 0
                    ):
                        hm.colormap = "viridis"
                        warnings.append(
                            "heatmap colormap was 'RdBu_r'/'RdBu' but the data "
                            f"values are one-sided ({vmin:g}..{vmax:g}); switched "
                            "to viridis for better contrast"
                        )

    if not problems:
        return None, warnings
    return (
        "TikZ spec data validation failed:\n- " + "\n- ".join(problems),
        warnings,
    )


def generate_figure_tex(sandbox: Path) -> dict[str, Any]:
    """从沙箱内的 tikz_spec.json + 数据 CSV 生成 artifacts/figure.tex。

    返回：
        {
          "generated": bool,          # 是否成功生成 figure.tex
          "error": str | None,        # 失败原因（spec 缺失时也为 None）
          "message": str,             # 给 Agent 的说明文本
          "warnings": list[str],      # 非阻断警告
          "size": int | None,         # 生成文件字节数
        }

    spec 缺失不属于失败：脚本可能走手写 figure.tex 回退路径，
    调用方据此提示 Agent 补写 spec 或手写 TikZ。
    """
    logger = get_logger("plot_gen.tikzgen")
    artifacts_dir = sandbox / "artifacts"
    spec_path = artifacts_dir / "tikz_spec.json"
    if not spec_path.exists():
        return {
            "generated": False,
            "error": None,
            "message": (
                "TikZ source NOT generated: artifacts/tikz_spec.json is missing. "
                "Write the spec (see TIKZ PROTOCOL in the system prompt) and "
                "rerun python_sandbox, or hand-write artifacts/figure.tex."
            ),
            "warnings": [],
            "size": None,
        }

    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        msg = f"tikz_spec.json is not valid JSON: {e}"
        return {
            "generated": False,
            "error": msg,
            "message": msg,
            "warnings": [],
            "size": None,
        }

    try:
        spec = TikzSpec.model_validate(raw)
    except ValidationError as e:
        msg = "tikz_spec.json validation failed.\n" + format_validation_error(
            e.errors()
        )
        return {
            "generated": False,
            "error": msg,
            "message": msg,
            "warnings": [],
            "size": None,
        }

    artifacts_dir = sandbox / "artifacts"
    data_error, data_warnings = _validate_csvs(spec, artifacts_dir)
    if data_error is not None:
        msg = (
            data_error + "\n\nFix tikz_spec.json and/or the data CSVs, then rerun "
            "python_sandbox (the backend regenerates figure.tex)."
        )
        return {
            "generated": False,
            "error": msg,
            "message": msg,
            "warnings": data_warnings,
            "size": None,
        }

    warnings = data_warnings + _check_non_ascii(spec)
    tex = _render(spec)
    out_path = resolve_project_path(
        sandbox, "artifacts/figure.tex", allowed_suffixes=(".tex",)
    )
    out_path.write_text(tex, encoding="utf-8")
    logger.info(
        "tikzgen generated figure.tex (%d bytes) for %s",
        len(tex.encode("utf-8")),
        sandbox,
    )
    msg = (
        "TikZ source generated: artifacts/figure.tex "
        f"({len(tex.encode('utf-8'))} bytes, chart_type={spec.chart_type}). "
        "Call compile_tikz next."
    )
    return {
        "generated": True,
        "error": None,
        "message": msg,
        "warnings": warnings,
        "size": len(tex.encode("utf-8")),
    }

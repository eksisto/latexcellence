"""科研绘图风格预设。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlotStyle:
    """单个风格预设。"""

    key: str
    label: str
    font_family: str
    font_size: float
    figure_width: float
    figure_height: float
    line_width: float
    palette: tuple[str, ...]
    grid: bool
    spines: tuple[str, ...]
    legend_loc: str
    dpi: int


OKABE_ITO = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#F0E442",
    "#000000",
)

TOL_MUTED = (
    "#332288",
    "#88CCEE",
    "#44AA99",
    "#117733",
    "#999933",
    "#DDCC77",
    "#CC6677",
    "#882255",
    "#AA4499",
)

STYLES: dict[str, PlotStyle] = {
    "nature": PlotStyle(
        key="nature",
        label="Nature",
        font_family="sans-serif",
        font_size=8,
        figure_width=3.5,
        figure_height=2.6,
        line_width=1.0,
        palette=OKABE_ITO,
        grid=False,
        spines=("left", "bottom"),
        legend_loc="best",
        dpi=300,
    ),
    "science": PlotStyle(
        key="science",
        label="Science",
        font_family="sans-serif",
        font_size=8,
        figure_width=3.5,
        figure_height=2.5,
        line_width=1.0,
        palette=TOL_MUTED,
        grid=False,
        spines=("left", "bottom", "top", "right"),
        legend_loc="best",
        dpi=300,
    ),
    "cell": PlotStyle(
        key="cell",
        label="Cell",
        font_family="sans-serif",
        font_size=8,
        figure_width=3.5,
        figure_height=2.8,
        line_width=1.0,
        palette=OKABE_ITO,
        grid=False,
        spines=("left", "bottom"),
        legend_loc="best",
        dpi=300,
    ),
    "ieee": PlotStyle(
        key="ieee",
        label="IEEE",
        font_family="sans-serif",
        font_size=8,
        figure_width=3.5,
        figure_height=2.4,
        line_width=1.0,
        palette=OKABE_ITO,
        grid=False,
        spines=("left", "bottom", "top", "right"),
        legend_loc="best",
        dpi=300,
    ),
    "acs": PlotStyle(
        key="acs",
        label="ACS",
        font_family="sans-serif",
        font_size=8,
        figure_width=3.5,
        figure_height=2.5,
        line_width=1.0,
        palette=OKABE_ITO,
        grid=False,
        spines=("left", "bottom"),
        legend_loc="best",
        dpi=300,
    ),
}


def list_styles() -> list[dict[str, str]]:
    """返回可用的风格列表。"""
    return [{"key": s.key, "label": s.label} for s in STYLES.values()]


def validate_style_key(style_key: str) -> str:
    """校验风格 key，非法时回退到 nature。"""
    return style_key if style_key in STYLES else "nature"


def render_setup_code(style_key: str) -> str:
    """渲染注入绘图脚本开头的 matplotlib 风格代码。"""
    style = STYLES[validate_style_key(style_key)]
    palette = ", ".join(f"'{c}'" for c in style.palette)
    spine_args = ", ".join(f"'{s}'" for s in style.spines)
    return f'''
import matplotlib

matplotlib.rcParams.update({{
    "font.family": "{style.font_family}",
    "font.sans-serif": ["Arial", "Helvetica", "Noto Sans CJK SC", "DejaVu Sans"],
    "font.size": {style.font_size},
    "axes.titlesize": {style.font_size + 1},
    "axes.labelsize": {style.font_size},
    "xtick.labelsize": {style.font_size - 1},
    "ytick.labelsize": {style.font_size - 1},
    "legend.fontsize": {style.font_size - 1},
    "figure.figsize": ({style.figure_width}, {style.figure_height}),
    "figure.dpi": {style.dpi},
    "savefig.dpi": {style.dpi},
    "lines.linewidth": {style.line_width},
    "axes.linewidth": 0.8,
    "axes.grid": {"True" if style.grid else "False"},
    "grid.linewidth": 0.5,
    "grid.alpha": 0.4,
    "legend.frameon": False,
    "axes.prop_cycle": matplotlib.cycler(color=[{palette}]),
}}
)


def apply_plot_style(ax=None):
    """Apply journal-oriented spine and layout defaults."""
    if ax is None:
        return
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(spine in ({spine_args}))
'''

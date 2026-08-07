"""公式图片的确定性行内/行间视觉判定（不依赖 LLM）。

背景：
    ``\\sum`` / ``\\prod`` / ``\\lim`` 等大型算子的上下标在行内（text style）
    绘制在符号右侧，在行间（display style）绘制在符号正上下方；``\\int``
    默认两种样式都把上下限放在右侧，但字形宽高比不同（Computer Modern 下
    文本样式约 2.0，显示样式约 2.5）。因此可以从输入图片的连通分量几何
    直接推断公式是行内还是行间，作为 LLM 判定的确定性矫正。

设计原则：
    - 只对证据明确的情况投票：找到大型算子且上下标位置清晰时给出判定，
      否则返回 ``None``（保持 LLM / 结构矫正的结论，绝不强行翻转）。
    - 所有阈值均为相对量（与图片宽度/高度的比值、宽高比），与 DPI 无关。
    - 纯确定性实现，无模型调用，失败时由调用方兜底（不影响 OCR 主流程）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy import ndimage

# 分析用图片最大边长：超过则等比缩小，仅影响速度，不影响相对几何
_ANALYSIS_MAX_DIM = 1200

# 算子候选分量的宽高比范围（括号等细高条 > 3.2，分数横线 < 0.4，均被排除）
_HOST_MIN_ASPECT = 0.40
_HOST_MAX_ASPECT = 3.20

# "宽型算子"（\\sum / \\prod / \\lim / \\max 等）：宽高比上限
_WIDE_OP_MAX_ASPECT = 1.70
# "积分型算子"（\\int 族）：只有宽高比不超过该值时，侧向上下标才视为行内证据
# （文本样式 ∫ 宽高比约 2.0，显示样式约 2.5）
_INTEGRAL_MAX_ASPECT_VOTE = 2.20

# 宽型算子：高度须占图高一定比例，且宽度须达到图宽 / 图高的相对门槛
_WIDE_MIN_H_FRAC = 0.25
_WIDE_MIN_W_FRAC = 0.04
_WIDE_MIN_W_H_FRAC = 0.18

# 积分型算子：高度占比更高（普通字母在行间图中通常不足 45% 图高）
_INTEGRAL_MIN_H_FRAC = 0.45
_INTEGRAL_MIN_W_H_FRAC = 0.16

# 上下标小分量：高度上限（相对图高）
_SMALL_MAX_H_FRAC = 0.48

# 侧向成对上下标：左侧起点与算子右边的最小重叠要求
_SIDE_X0_MARGIN = 0.30  # S.x0 >= H.x1 - margin * H.w
_SIDE_X0_OFFSET = 0.50  # S.x0 >= H.x0 + offset * H.w
# 侧向成对上下标与算子的最大水平间距（成对时更严格）
_PAIR_GAP_FRAC = 0.45
# 单个右下角下标与算子的最大水平间距（\\lim 的条件距符号更远）
_LOWER_GAP_FRAC = 0.80


@dataclass
class VisualInlineHint:
    """视觉判定的结果。

    inline 为 None 表示证据不足，调用方应保持原判定。
    """

    inline: bool | None
    confidence: float = 0.0
    reason: str = ""

    @property
    def decided(self) -> bool:
        return self.inline is not None


def analyze_formula_image(img: Image.Image) -> VisualInlineHint:
    """分析公式图片，返回行内/行间视觉判定（证据不足时为 None）。"""
    try:
        return _analyze(img)
    except Exception:
        # 视觉判定只是增强，任何异常都不应影响 OCR 主流程
        return VisualInlineHint(None, 0.0, "visual-analysis-error")


def _analyze(img: Image.Image) -> VisualInlineHint:
    if img.width > _ANALYSIS_MAX_DIM or img.height > _ANALYSIS_MAX_DIM:
        ratio = min(_ANALYSIS_MAX_DIM / img.width, _ANALYSIS_MAX_DIM / img.height)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
        )

    comps, h, w = _components(img)
    if not comps:
        return VisualInlineHint(None, 0.0, "no-components")

    hosts = _find_hosts(comps, h, w)
    smalls = [c for c in comps if c["h"] <= _SMALL_MAX_H_FRAC * h and c["w"] >= 2]

    display_strong = 0
    inline_strong = 0
    inline_weak = 0
    reasons: list[str] = []

    for kind, host in hosts:
        delta, reason = _vote_host(kind, host, smalls)
        display_strong += delta[0]
        inline_strong += delta[1]
        inline_weak += delta[2]
        if reason:
            reasons.append(reason)

    return _aggregate(display_strong, inline_strong, inline_weak, reasons)


def _components(img: Image.Image) -> tuple[list[dict], int, int]:
    """二值化并提取连通分量（含包围盒与中心点）。"""
    gray = np.asarray(img.convert("L"))
    h, w = gray.shape
    if h < 16 or w < 16:
        return [], h, w

    ink = gray < 200
    labels, n = ndimage.label(ink, structure=np.ones((3, 3)))
    objs = ndimage.find_objects(labels)

    comps: list[dict] = []
    for i, sl in enumerate(objs):
        if sl is None:
            continue
        ys, xs = sl
        bh = ys.stop - ys.start
        bw = xs.stop - xs.start
        area = int((labels[sl] == i + 1).sum())
        if area < 3:
            continue
        comps.append(
            {
                "x0": xs.start,
                "x1": xs.stop,
                "y0": ys.start,
                "y1": ys.stop,
                "h": bh,
                "w": bw,
                "cx": (xs.start + xs.stop) / 2.0,
                "cy": (ys.start + ys.stop) / 2.0,
            }
        )
    return comps, h, w


def _find_hosts(comps: list[dict], h: int, w: int) -> list[tuple[str, dict]]:
    """筛选算子候选：宽型（求和/极限类）与积分型（\\int 族）。"""
    hosts: list[tuple[str, dict]] = []
    for c in comps:
        if c["w"] < 5:
            continue
        aspect = c["h"] / max(c["w"], 1)
        if not (_HOST_MIN_ASPECT <= aspect <= _HOST_MAX_ASPECT):
            continue
        if aspect <= _WIDE_OP_MAX_ASPECT:
            if c["h"] < _WIDE_MIN_H_FRAC * h:
                continue
            if c["w"] < _WIDE_MIN_W_FRAC * w or c["w"] < _WIDE_MIN_W_H_FRAC * h:
                continue
            hosts.append(("wide", c))
        else:
            if c["h"] < _INTEGRAL_MIN_H_FRAC * h:
                continue
            if c["w"] < _INTEGRAL_MIN_W_H_FRAC * h:
                continue
            hosts.append(("integral", c))
    return hosts


def _vote_host(
    kind: str,
    host: dict,
    smalls: list[dict],
) -> tuple[tuple[int, int, int], str]:
    """对单个算子候选投票。

    返回 ((display_strong, inline_strong, inline_weak), 原因描述)。
    """
    hx0, hx1 = host["x0"], host["x1"]
    hy0, hy1 = host["y0"], host["y1"]
    hw, hh = host["w"], host["h"]
    cy = host["cy"]

    win_x0 = hx0 - 0.40 * hw
    win_x1 = hx1 + 1.50 * hw
    win_y0 = hy0 - 0.45 * hh
    win_y1 = hy1 + 0.45 * hh
    near = [
        s
        for s in smalls
        if s["x0"] <= win_x1
        and s["x1"] >= win_x0
        and s["y0"] <= win_y1
        and s["y1"] >= win_y0
    ]

    side_s = [
        s
        for s in near
        if s["x0"] >= hx1 - _SIDE_X0_MARGIN * hw
        and s["x0"] >= hx0 + _SIDE_X0_OFFSET * hw
        and s["x1"] > hx1 - 0.10 * hw
    ]
    cent_s = [s for s in near if hx0 - 0.10 * hw <= s["cx"] <= hx1 + 0.10 * hw]

    side_upper = [s for s in side_s if s["y1"] <= cy]
    side_lower = [s for s in side_s if s["y0"] >= cy]
    cent_upper = [s for s in cent_s if s["y1"] <= hy0 + 0.20 * hh]
    cent_lower = [s for s in cent_s if s["y0"] >= hy1 - 0.20 * hh]

    pair_side = _find_side_pair(side_upper, side_lower, hx1, hw)
    pair_cent = bool(cent_upper and cent_lower)

    display_strong = 0
    inline_strong = 0
    inline_weak = 0
    reason = ""

    if kind == "wide":
        if pair_cent:
            display_strong += 1
            reason = "wide@%d above-below" % hx0
        elif pair_side:
            inline_strong += 1
            reason = "wide@%d side-pair" % hx0
        else:
            for s in side_lower:
                if not side_upper:
                    gap = s["x0"] - hx1
                    if gap <= _LOWER_GAP_FRAC * hw:
                        inline_weak += 1
                        reason = "wide@%d side-lower" % hx0
                    break
    else:
        aspect = hh / max(hw, 1)
        if pair_cent:
            display_strong += 1
            reason = "integral@%d above-below" % hx0
        elif (pair_side or side_lower) and aspect <= _INTEGRAL_MAX_ASPECT_VOTE:
            inline_weak += 1
            reason = "integral@%d side+aspect-%.2f" % (hx0, aspect)

    return (display_strong, inline_strong, inline_weak), reason


def _find_side_pair(
    side_upper: list[dict], side_lower: list[dict], hx1: float, hw: float
) -> tuple[dict, dict] | None:
    """找一对上下堆叠且相互水平重叠的右侧上下标（\\sum 行内样式）。"""
    for a in side_upper:
        for b in side_lower:
            if max(a["x0"], b["x0"]) < min(a["x1"], b["x1"]) - 1:
                gap = min(a["x0"], b["x0"]) - hx1
                if gap <= _PAIR_GAP_FRAC * hw:
                    return (a, b)
    return None


def _aggregate(
    display_strong: int,
    inline_strong: int,
    inline_weak: int,
    reasons: list[str],
) -> VisualInlineHint:
    """汇总各算子投票：行间证据优先，其次行内强证据，最后弱证据。"""
    if display_strong > 0:
        return VisualInlineHint(
            False,
            min(0.95, 0.74 + 0.06 * display_strong),
            ";".join(reasons) or "display",
        )
    if inline_strong > 0:
        return VisualInlineHint(
            True,
            min(0.92, 0.74 + 0.06 * inline_strong),
            ";".join(reasons) or "inline",
        )
    if inline_weak > 0:
        return VisualInlineHint(True, 0.60, ";".join(reasons) or "inline")
    return VisualInlineHint(None, 0.0, ";".join(reasons) or "none")

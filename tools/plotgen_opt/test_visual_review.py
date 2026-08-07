"""plotgen_opt/visual_review.py 的离线视觉审查工具测试。"""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
PROJECT_ROOT = TOOLS_ROOT.parent  # 项目根（backend 所在）
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TOOLS_ROOT))

from plotgen_opt.visual_review import (  # noqa: E402
    VisualReview,
    _build_user_content,
    _image_to_base64,
)


def _make_png(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


def test_visual_review_model_valid():
    review = VisualReview.model_validate(
        {
            "axis_labels": 8,
            "legend": 7,
            "colors": 9,
            "data_fidelity": 10,
            "overall": 8,
            "issues": ["legend overlaps data"],
            "suggestions": ["move legend outside"],
        }
    )
    assert review.overall == 8
    assert review.issues == ["legend overlaps data"]


def test_visual_review_model_rejects_out_of_range():
    with pytest.raises(ValidationError):
        VisualReview.model_validate(
            {
                "axis_labels": 8,
                "legend": 7,
                "colors": 9,
                "data_fidelity": 11,  # 超出 0-10
                "overall": 8,
            }
        )


def test_image_to_base64_returns_data_uri():
    uri, mime = _image_to_base64(_make_png())
    assert uri.startswith("data:image/png;base64,")
    assert mime == "image/png"


def test_image_to_base64_downscales_large_image():
    uri, _ = _image_to_base64(_make_png((3000, 2000)))
    assert len(uri) < len(_make_png((3000, 2000))) * 2


def test_build_user_content_includes_plan_and_image():
    parts = _build_user_content(
        "dose-response curve",
        {"chart_type": "line", "x": "dose", "y": "mean"},
        "data:image/png;base64,abc",
    )
    assert parts[0]["type"] == "text"
    assert "dose-response curve" in parts[0]["text"]
    assert '"chart_type": "line"' in parts[0]["text"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,abc"

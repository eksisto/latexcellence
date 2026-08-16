"""plotgen_opt/visual_review.py 的离线视觉审查工具测试。"""

import io
import sys
from types import SimpleNamespace
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
    _convert_messages_to_responses,
    _image_to_base64,
)
import plotgen_opt.visual_review as visual_review  # noqa: E402
from visual_review_api import build_responses_request  # noqa: E402


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


def test_convert_messages_to_responses_maps_vision_parts():
    instructions, input_messages = _convert_messages_to_responses(
        [
            {"role": "system", "content": "Review strictly."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this figure."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,abc",
                            "detail": "high",
                        },
                    },
                ],
            },
        ]
    )

    assert instructions == "Review strictly."
    assert input_messages == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect this figure."},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,abc",
                    "detail": "high",
                },
            ],
        }
    ]


def test_responses_vision_request_omits_json_mode_for_compatibility():
    """含图像时不发送部分兼容端点拒绝的 Responses JSON mode。"""
    request = build_responses_request(
        [
            {"role": "system", "content": "Return JSON."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Review."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ],
            },
        ],
        model="review-model",
        max_tokens=128,
        response_format={"type": "json_object"},
    )

    assert "text" not in request
    assert request["input"][0]["content"][1]["type"] == "input_image"


@pytest.mark.asyncio
async def test_review_chat_uses_responses_endpoint(monkeypatch):
    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(output_text='{"overall": 8}')

    responses = FakeResponses()
    monkeypatch.setattr(visual_review, "REVIEW_MODEL", "review-model")
    monkeypatch.setattr(visual_review, "REVIEW_BASE_URL", "")
    monkeypatch.setattr(visual_review, "REVIEW_API_KEY", "review-key")
    monkeypatch.setattr(
        visual_review,
        "_get_review_client",
        lambda: SimpleNamespace(responses=responses),
    )

    result = await visual_review._review_chat(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Review the figure."},
        ],
        temperature=0.1,
        max_tokens=128,
        response_format={"type": "json_object"},
    )

    assert result == '{"overall": 8}'
    assert responses.kwargs["model"] == "review-model"
    assert responses.kwargs["instructions"] == "Return JSON."
    assert responses.kwargs["max_output_tokens"] == 128
    assert "max_tokens" not in responses.kwargs
    assert responses.kwargs["text"] == {"format": {"type": "json_object"}}


@pytest.mark.asyncio
async def test_review_chat_html_error_gets_clear_message(monkeypatch):
    """评审端点返回 HTML/404 时，给出配置提示而不是倾倒整页 HTML。"""

    class BoomResponses:
        async def create(self, **kwargs):
            raise RuntimeError("<!DOCTYPE html><html><title>404 - Not Found</title>")

    monkeypatch.setattr(visual_review, "REVIEW_MODEL", "review-model")
    monkeypatch.setattr(visual_review, "REVIEW_BASE_URL", "")
    monkeypatch.setattr(visual_review, "REVIEW_API_KEY", "review-key")
    monkeypatch.setattr(
        visual_review,
        "_get_review_client",
        lambda: SimpleNamespace(responses=BoomResponses()),
    )

    with pytest.raises(RuntimeError, match="HTML/404"):
        await visual_review._review_chat(
            [{"role": "user", "content": "Review the figure."}]
        )

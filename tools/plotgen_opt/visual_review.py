"""LLM 视觉质量审查（plot_gen 回归门禁的离线评估工具）。

用法：
    python tools/plotgen_opt/visual_review.py <tag> [s1_time_series,s2_correlation,...]
    python tools/plotgen_opt/visual_review.py <tag>            # 审查该 tag 全部场景

对每个场景读取 results/<tag>/<scenario>/ 下的产物：
    - 优先使用 TikZ 编译产物 artifacts/tikz_main.pdf（pdftoppm 转 PNG），
      缺失时回退到 matplotlib 预览图 artifacts/figure.png；
    - 把图 + chart_plan + 场景描述交给 LLM（vision）做结构化评分；
    - 结果写入 summary.json 的 visual_review 字段。

评分维度（各 0~10）：轴标签、图例、配色、数据一致性 + 总评；
issues/suggestions 提供改进建议。该审查为离线评估，不参与
run_plotgen.py 的启发式 100 分评分。
"""

import asyncio
import base64
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
PROJECT_ROOT = TOOLS_ROOT.parent  # 项目根（backend 所在）
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TOOLS_ROOT))

from backend.core.config import load_config  # noqa: E402
from backend.core.llm_client import get_llm_client  # noqa: E402
from backend.utils.json_utils import retry_with_validation  # noqa: E402
from tex_path import ensure_tex_path  # noqa: E402

load_config("config.json")

MAX_IMAGE_DIM = 1024


class VisualReview(BaseModel):
    """LLM 视觉审查结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    axis_labels: int = Field(ge=0, le=10)
    legend: int = Field(ge=0, le=10)
    colors: int = Field(ge=0, le=10)
    data_fidelity: int = Field(ge=0, le=10)
    overall: int = Field(ge=0, le=10)
    issues: list[str] = []
    suggestions: list[str] = []


SYSTEM_PROMPT = (
    "You are a rigorous scientific figure reviewer. Evaluate the rendered "
    "figure against the chart plan and the dataset description. Score each "
    "dimension from 0 (unusable) to 10 (publication-ready): axis_labels "
    "(clear axis titles/tick labels, correct units), legend (present when "
    "multiple series, readable, non-overlapping), colors (perceptually "
    "reasonable, distinguishable series, appropriate colormap for heatmaps), "
    "data_fidelity (the figure reflects the described data: trend, error "
    "bars, significance marks, axes ranges match the data). Return JSON only "
    "with keys: axis_labels, legend, colors, data_fidelity, overall, issues "
    "(list of concrete problems), suggestions (list of concrete fixes)."
)


def _image_to_base64(img_bytes: bytes) -> tuple[str, str]:
    """把图片字节缩放到 MAX_IMAGE_DIM 内并转 base64，返回 (data_uri, mime)。"""
    from PIL import Image

    img = Image.open(io.BytesIO(img_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}", "image/png"


def _render_pdf_to_png(pdf_path: Path, out_dir: Path) -> Path | None:
    """pdftoppm 渲染 PDF 第一页为 PNG；不可用时返回 None。"""
    if not pdf_path.exists() or shutil.which("pdftoppm") is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"{pdf_path.stem}_review"
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                "110",
                "-f",
                "1",
                "-l",
                "1",
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return None
    pages = sorted(out_dir.glob(f"{prefix.name}-*.png"))
    return pages[0] if pages else None


def _load_review_image(sandbox: Path, result_dir: Path) -> tuple[bytes | None, str]:
    """选择审查用图：优先 TikZ 编译产物 PDF，回退 matplotlib 预览 PNG。"""
    pdf = sandbox / "artifacts" / "tikz_main.pdf"
    png = sandbox / "artifacts" / "figure.png"
    rendered = _render_pdf_to_png(pdf, result_dir / "_review")
    if rendered is not None:
        return rendered.read_bytes(), str(rendered)
    if png.exists():
        return png.read_bytes(), str(png)
    return None, ""


def _build_user_content(
    description: str, chart_plan: dict[str, Any], data_uri: str
) -> list[dict[str, Any]]:
    plan_text = json.dumps(chart_plan, ensure_ascii=False, indent=2)
    return [
        {
            "type": "text",
            "text": (
                f"Dataset description: {description}\n\n"
                f"Chart plan: {plan_text}\n\n"
                "Review the rendered figure below."
            ),
        },
        {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
    ]


async def review_scenario(
    llm: Any, tag: str, key: str, result_dir: Path, out_root: Path
) -> dict[str, Any]:
    summary_path = result_dir / "summary.json"
    if not summary_path.exists():
        print(f"[{key}] SKIP: no summary.json")
        return {}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    sandbox = Path(summary.get("sandbox", ""))
    img_bytes, img_source = _load_review_image(sandbox, result_dir)
    if img_bytes is None:
        print(f"[{key}] SKIP: no reviewable image")
        return {}

    data_uri, _ = _image_to_base64(img_bytes)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_content(
                summary.get("description", ""),
                summary.get("chart_plan", {}),
                data_uri,
            ),
        },
    ]
    try:
        review: VisualReview = await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=VisualReview,
            max_retries=3,
            logger_name="plot_gen.visual_review",
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[{key}] REVIEW FAILED: {e}")
        return {}

    record = {
        "image_source": img_source,
        **review.model_dump(),
    }
    summary["visual_review"] = record
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{key}] overall={review.overall}/10 "
        f"(axis={review.axis_labels} legend={review.legend} "
        f"colors={review.colors} fidelity={review.data_fidelity}) "
        f"issues={len(review.issues)} src={Path(img_source).name}"
    )
    return {"scenario": key, **record}


async def main() -> None:
    ensure_tex_path()
    if len(sys.argv) < 2:
        print(__doc__)
        return
    tag = sys.argv[1]
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    out_root = Path(__file__).resolve().parent / "results"
    tag_dir = out_root / tag
    if not tag_dir.exists():
        print(f"no results for tag '{tag}'")
        return
    scenarios = keys or sorted(p.name for p in tag_dir.iterdir() if p.is_dir())
    llm = get_llm_client()
    all_reviews: dict[str, Any] = {}
    for key in scenarios:
        result_dir = tag_dir / key
        rec = await review_scenario(llm, tag, key, result_dir, out_root)
        if rec:
            all_reviews[key] = rec
    (tag_dir / "visual_reviews.json").write_text(
        json.dumps(all_reviews, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if all_reviews:
        avg = sum(r["overall"] for r in all_reviews.values()) / len(all_reviews)
        print(f"\n=== {tag} visual review avg overall = {avg:.1f}/10 ===")


if __name__ == "__main__":
    asyncio.run(main())

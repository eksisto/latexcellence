# -*- coding: utf-8 -*-
"""LLM 视觉质量审查（template_gen 离线评估工具）。

用法：
    python tools/templategen_opt/visual_review.py <tag> [thesis,beamer,resume,...]

对每个场景读取 results/<tag>/<scenario>/sandbox/ 下的主 PDF，
用 pdftoppm 渲染前若干页为 PNG，交给 LLM（vision）按维度评分，
结果写入 summary.json 的 visual_review 字段。

评分维度（各 0~10）：typography（字体层级）、layout（边距/版式）、
aesthetics（配色/视觉）、requirement_adherence（需求遵守）、overall。
"""

import asyncio
import base64
import io
import json
import os
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

from backend.core.config import get_config, load_config  # noqa: E402
from backend.core.llm_client import get_llm_client  # noqa: E402
from backend.utils.json_utils import retry_with_validation  # noqa: E402
from tex_path import ensure_tex_path  # noqa: E402
from templategen_opt.descriptions import SCENARIOS  # noqa: E402
from visual_review_api import build_responses_request  # noqa: E402

load_config("config.json")

MAX_IMAGE_DIM = 1024

# 独立评审模型（默认与生成模型相同；通过环境变量切换到更强的视觉模型）：
#   LX_REVIEW_LLM_MODEL     评审模型名，如 gpt-5.6-luna （留空 = 用生成模型）
#   LX_REVIEW_LLM_BASE_URL  评审模型端点（留空 = 用生成模型端点）
#   LX_REVIEW_LLM_KEY       评审模型 API Key（留空 = 用生成模型 Key）
REVIEW_MODEL = os.environ.get("LX_REVIEW_LLM_MODEL", "").strip()
REVIEW_BASE_URL = os.environ.get("LX_REVIEW_LLM_BASE_URL", "").strip()
REVIEW_API_KEY = os.environ.get("LX_REVIEW_LLM_KEY", "").strip()
_review_client = None


def _get_review_client() -> Any:
    """按环境变量创建独立评审客户端（进程内只创建一次）。"""
    global _review_client
    if _review_client is None:
        from openai import AsyncOpenAI

        cfg = get_config().llm
        _review_client = AsyncOpenAI(
            api_key=REVIEW_API_KEY or cfg.api_key,
            base_url=REVIEW_BASE_URL or cfg.base_url,
        )
    return _review_client


def _active_review_model() -> str:
    """当前评审实际使用的模型名（用于日志提示）。"""
    if REVIEW_MODEL:
        return REVIEW_MODEL
    return get_config().llm.model


async def _review_chat(
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    """调用评审模型；独立评审客户端使用 Responses API。"""
    if REVIEW_MODEL or REVIEW_BASE_URL or REVIEW_API_KEY:
        client = _get_review_client()
        kwargs = build_responses_request(
            messages,
            model=REVIEW_MODEL or get_config().llm.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        resp = await client.responses.create(**kwargs)
        return resp.output_text or ""
    llm = get_llm_client()
    return await llm.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )


class TemplateVisualReview(BaseModel):
    """模板视觉审查结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    typography: float = Field(ge=0, le=10)
    layout: float = Field(ge=0, le=10)
    aesthetics: float = Field(ge=0, le=10)
    requirement_adherence: float = Field(ge=0, le=10)
    overall: float = Field(ge=0, le=10)
    issues: list[str] = []
    suggestions: list[str] = []


SYSTEM_PROMPT = (
    "You are a senior LaTeX template art director and typography reviewer. "
    "The images are pages rendered from a LaTeX template generated from a "
    "natural-language user request. Score each dimension from 0 (unusable) "
    "to 10 (professional, benchmark-grade): "
    "typography (clear font hierarchy across heading/body/caption sizes and "
    "weights; consistent spacing; no awkward line breaks or overfull text), "
    "layout (margins, page structure, alignment, whitespace balance, "
    "information density appropriate to the document type), "
    "aesthetics (color palette restraint, visual polish, modern professional "
    "look, decorations purposeful), "
    "requirement_adherence (how faithfully the rendered document follows the "
    "user request: margins, fonts, colors, structure, style). "
    "IMPORTANT GROUND RULES:\n"
    "- You can ONLY see the first few rendered pages of a multi-page "
    "document. A FACTUAL file list and README excerpt are provided in the "
    "user message — they exist as stated, so never assert files are "
    "missing or penalize their absence based on the images.\n"
    "- Later pages (TOC, chapters, bibliography, appendices, back matter) "
    "are not shown; do not claim they are missing or judge them.\n"
    "- Judge only what is actually visible: typography, layout, colors, "
    "spacing, alignment, page headers/footers, and the visible content "
    "against the user request. When a requirement cannot be verified from "
    "the images, do NOT penalize it and do NOT list it as an issue.\n"
    "- The document may be typeset in Chinese; evaluate it as Chinese "
    "academic typesetting. Minor pixelation from low-resolution rendering "
    "is not a template defect.\n"
    "Return JSON only with keys: typography, layout, aesthetics, "
    "requirement_adherence, overall, issues (list of concrete problems), "
    "suggestions (list of concrete fixes). Keep issues and suggestions "
    "grounded in visible evidence."
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


def _render_pdf_pages(pdf_path: Path, out_dir: Path, max_pages: int) -> list[Path]:
    """pdftoppm 渲染 PDF 前 max_pages 页为 PNG，返回 PNG 路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"{pdf_path.stem}_page"
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                "100",
                "-f",
                "1",
                "-l",
                str(max_pages),
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return []
    return sorted(out_dir.glob(f"{prefix.name}-*.png"))


def _pick_main_pdf(sandbox: Path) -> Path | None:
    pdfs = list(sandbox.rglob("*.pdf")) if sandbox.exists() else []
    if not pdfs:
        return None
    preferred = [
        p for p in pdfs if p.name.startswith(("main", "resume", "cv", "thesis"))
    ]
    return (preferred or pdfs)[0]


def _build_user_content(
    description: str,
    images: list[tuple[str, str]],
    files_text: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "User request for this LaTeX template:\n\n"
                f"{description}\n\n"
                "FACTUAL context about the generated project (file names "
                "and README excerpt, provided by the evaluation harness; "
                "these files DO exist — do not question them):\n\n"
                f"{files_text}\n\n"
                "Now review the rendered pages below (first pages of the "
                "PDF). Judge visual quality and visible requirement "
                "adherence from the images; use the file list only as "
                "evidence that structural pieces exist."
            ),
        }
    ]
    for data_uri, _mime in images:
        content.append(
            {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}}
        )
    return content


def _files_context(sandbox: Path) -> str:
    """收集沙箱内的源文件清单与 README 摘要，作为评审的事实上下文。"""
    src_suffixes = {".tex", ".sty", ".cls", ".bib", ".md", ".txt", ".cfg"}
    paths: list[str] = []
    if sandbox.exists():
        for p in sorted(sandbox.rglob("*")):
            if p.is_file() and p.suffix.lower() in src_suffixes:
                paths.append(p.relative_to(sandbox).as_posix())
    lines = ["Files:", *[f"- {p}" for p in paths]]
    readme = next(
        (
            p
            for p in (sandbox.rglob("README*") if sandbox.exists() else [])
            if p.is_file()
        ),
        None,
    )
    if readme:
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
            lines.append("\nREADME excerpt:")
            lines.append(text[:2000])
        except OSError:
            pass
    return "\n".join(lines)


async def review_scenario(llm: Any, key: str, result_dir: Path) -> dict[str, Any]:
    summary_path = result_dir / "summary.json"
    if not summary_path.exists():
        print(f"[{key}] SKIP: no summary.json")
        return {}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    sandbox = result_dir / "sandbox"
    pdf = _pick_main_pdf(sandbox)
    if pdf is None:
        print(f"[{key}] SKIP: no PDF in sandbox")
        return {}

    max_pages = 3 if key != "resume" else 2
    pages = _render_pdf_pages(pdf, result_dir / "_review", max_pages)
    if not pages:
        print(f"[{key}] SKIP: pdftoppm render failed")
        return {}

    images = [_image_to_base64(p.read_bytes()) for p in pages]
    files_text = _files_context(sandbox)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_content(SCENARIOS.get(key, ""), images, files_text),
        },
    ]
    try:
        review: TemplateVisualReview = await retry_with_validation(
            chat_fn=_review_chat,
            messages=messages,
            model_cls=TemplateVisualReview,
            max_retries=3,
            logger_name="template_gen.visual_review",
            temperature=0.1,
            # Responses 视觉模型的推理 token 也计入 max_output_tokens；1024
            # 容易在尚未输出 JSON 前耗尽，导致空 output_text。
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[{key}] REVIEW FAILED: {e}")
        return {}

    record = {
        "pdf": str(pdf),
        "pages_rendered": [p.name for p in pages],
        **review.model_dump(),
    }
    summary["visual_review"] = record
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[{key}] overall={review.overall}/10 "
        f"(type={review.typography} layout={review.layout} "
        f"aes={review.aesthetics} req={review.requirement_adherence}) "
        f"issues={len(review.issues)}"
    )
    return {"scenario": key, **record}


async def main() -> None:
    ensure_tex_path()
    if len(sys.argv) < 2:
        print(__doc__)
        return
    tag = sys.argv[1]
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    out_root = TOOLS_ROOT / "templategen_opt" / "results" / tag
    if not out_root.exists():
        print(f"no results for tag '{tag}'")
        return
    scenarios = keys or sorted(p.name for p in out_root.iterdir() if p.is_dir())
    llm = get_llm_client()
    print(
        f"visual review model: {_active_review_model()} "
        f"(env overrides: model={bool(REVIEW_MODEL)} "
        f"base_url={bool(REVIEW_BASE_URL)} key={bool(REVIEW_API_KEY)})"
    )
    reviews_path = out_root / "visual_reviews.json"
    all_reviews: dict[str, Any] = {}
    if reviews_path.exists():
        try:
            all_reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            all_reviews = {}
    for key in scenarios:
        result_dir = out_root / key
        rec = await review_scenario(llm, key, result_dir)
        if rec:
            all_reviews[key] = rec
    reviews_path.write_text(
        json.dumps(all_reviews, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if all_reviews:
        avg = sum(r["overall"] for r in all_reviews.values()) / len(all_reviews)
        print(f"\n=== {tag} visual review avg overall = {avg:.1f}/10 ===")


if __name__ == "__main__":
    asyncio.run(main())

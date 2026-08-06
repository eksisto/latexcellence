"""公式 OCR 人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

职责：
    1. 加载 tests/fixtures/formula_ocr 语料（manifest.json + 每条公式一个 .tex 片段）
    2. 将 LaTeX 公式片段渲染为 PNG（pdflatex + preview + pdftocairo）
    3. 清洗 OCR 输出（去宏包注释、去数学包裹），便于渲染比对
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "formula_ocr"

# 自带显示模式的顶层环境：渲染时不再额外套 \[...\]（与 formula_ocr service 的约定一致）
_STANDALONE_DISPLAY_ENV_RE = re.compile(
    r"^\s*\\begin\{(equation|equation\*|displaymath|align|align\*|alignat|alignat\*|"
    r"flalign|flalign\*|gather|gather\*|multline|multline\*|eqnarray|eqnarray\*)\}"
)


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def iter_formulas(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 公式 LaTeX 文本)。"""
    entries = manifest["formulas"]
    if limit is not None:
        entries = entries[:limit]
    for entry in entries:
        tex = (FIXTURES_DIR / entry["file"]).read_text(encoding="utf-8").strip()
        yield entry, tex


def strip_ocr_latex(latex: str) -> str:
    """清洗 OCR 输出：去掉宏包注释行与 $ / $$ / \\[ \\] / \\( \\) 数学包裹。"""
    lines = [line for line in latex.splitlines() if not line.lstrip().startswith("%")]
    s = "\n".join(lines).strip()
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2].strip()
    if s.startswith(r"\[") and s.endswith(r"\]"):
        s = s[2:-2].strip()
    if s.startswith(r"\(") and s.endswith(r"\)"):
        s = s[2:-2].strip()
    if s.startswith("$") and s.endswith("$"):
        s = s[1:-1].strip()
    return s


def build_document(latex: str, inline: bool, packages: list[str]) -> str:
    """把裸公式包进最小可编译文档（preview 紧裁白边）。"""
    usepackage = "\n".join(f"\\usepackage{{{pkg}}}" for pkg in packages)
    if _STANDALONE_DISPLAY_ENV_RE.match(latex):
        body = latex
    elif inline:
        body = f"${latex}$"
    else:
        body = rf"\[{latex}\]"
    return (
        "\\documentclass[12pt]{article}\n"
        "\\usepackage[active,tightpage]{preview}\n"
        f"{usepackage}\n"
        "\\begin{document}\n"
        "\\begin{preview}\n"
        f"{body}\n"
        "\\end{preview}\n"
        "\\end{document}\n"
    )


def _trim_white(img: Image.Image) -> Image.Image:
    """去掉四周纯白边（preview 已紧裁，这里做二次保险）。"""
    if img.mode != "RGB":
        img = img.convert("RGB")
    bbox = img.point(lambda p: 255 - p).getbbox()
    if bbox:
        img = img.crop(bbox)
    return img


def render_latex_png(
    latex: str,
    inline: bool,
    out_png: Path,
    packages: list[str] | None = None,
    dpi: int = 300,
    engine: str = "pdflatex",
    timeout: int = 120,
) -> None:
    """将 LaTeX 公式片段渲染为 PNG。

    依赖本机 TeX Live（pdflatex + pdftocairo），未安装时抛出 RuntimeError。
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which(engine) is None or shutil.which("pdftocairo") is None:
        raise RuntimeError(
            "未找到 TeX 工具链（pdflatex / pdftocairo），请先安装 TeX Live"
        )

    doc = build_document(latex, inline, packages or ["amsmath", "amssymb"])
    with tempfile.TemporaryDirectory(prefix="formula_render_") as tmp_dir:
        tmp = Path(tmp_dir)
        (tmp / "formula.tex").write_text(doc, encoding="utf-8")
        result = subprocess.run(
            [engine, "-interaction=nonstopmode", "-halt-on-error", "formula.tex"],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        pdf = tmp / "formula.pdf"
        if result.returncode != 0 or not pdf.exists():
            out_tail = (result.stdout or "")[-2000:]
            err_tail = (result.stderr or "")[-2000:]
            raise RuntimeError(
                f"pdflatex 编译失败：\n--- stdout ---\n{out_tail}\n--- stderr ---\n{err_tail}"
            )
        png_prefix = tmp / "formula"
        subprocess.run(
            [
                "pdftocairo",
                "-png",
                "-singlefile",
                "-r",
                str(dpi),
                str(pdf),
                str(png_prefix),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rendered = tmp / "formula.png"
        img = _trim_white(Image.open(rendered))
        img.save(out_png)

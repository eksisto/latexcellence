"""表格 OCR 人工评估共享逻辑（维护者工具，不进入运行时与 pytest）。

职责：
    1. 加载 tests/fixtures/table_ocr 语料（manifest.json + 每条表格一个 .tex 片段）
    2. 将 LaTeX 表格片段渲染为 PNG（pdflatex + preview + pdftocairo），
       渲染前去掉浮动体外壳（table / table* / sidewaystable / wraptable），
       保证 preview 紧裁只包含表格本体
    3. 清洗 OCR 输出（去宏包注释行），便于渲染比对
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "table_ocr"

# 浮动体外壳：table / table* / sidewaystable / wraptable（可选定位参数）
_FLOAT_ENV_RE = re.compile(
    r"^\s*\\begin\{(table\*?|sidewaystable|wraptable)\}(\[[^\]]*\])?"
)

# 剥壳后 \caption 不再处于浮动体内部，渲染时改写为 \captionof{table}
_CAPTION_RE = re.compile(r"\\caption\s*\{")


def load_manifest() -> dict:
    """读取语料 manifest.json。"""
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def iter_tables(manifest: dict, limit: int | None = None):
    """逐条返回 (manifest 条目, 表格 LaTeX 文本)。"""
    entries = manifest["tables"]
    if limit is not None:
        entries = entries[:limit]
    for entry in entries:
        tex = (FIXTURES_DIR / entry["file"]).read_text(encoding="utf-8").strip()
        yield entry, tex


def strip_ocr_latex(latex: str) -> str:
    """清洗 OCR 输出：去掉宏包注释行（% \\usepackage{...}），其余原样保留。"""
    lines = [line for line in latex.splitlines() if not line.lstrip().startswith("%")]
    return "\n".join(lines).strip()


def unwrap_float(latex: str) -> tuple[str, bool]:
    """去掉浮动体外壳，返回 (表格本体, 是否横向)。

    table / table* / wraptable 直接取内部内容；
    sidewaystable 取内部内容并标记横向，渲染时包 \\begin{sideways}。
    非浮动体（longtable / supertabular / 裸 tabular 等）原样返回。
    """
    match = _FLOAT_ENV_RE.match(latex)
    if not match:
        return latex, False
    env = match.group(1)
    end_tag = f"\\end{{{env}}}"
    end_pos = latex.rfind(end_tag)
    if end_pos == -1:
        return latex, False
    inner = latex[match.end() : end_pos].strip()
    return inner, env == "sidewaystable"


def build_document(latex: str, packages: list[str]) -> str:
    """把表格片段包进最小可编译文档（preview 紧裁白边）。"""
    usepackage = "\n".join(f"\\usepackage{{{pkg}}}" for pkg in packages)
    body, rotated = unwrap_float(latex)
    if rotated:
        body = "\\begin{sideways}\n" + body + "\n\\end{sideways}"
    body = _CAPTION_RE.sub(r"\\captionof{table}{", body)
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
    out_png: Path,
    packages: list[str] | None = None,
    dpi: int = 300,
    engine: str = "pdflatex",
    timeout: int = 120,
) -> None:
    """将 LaTeX 表格片段渲染为 PNG。

    依赖本机 TeX Live（pdflatex + pdftocairo），未安装时抛出 RuntimeError。
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which(engine) is None or shutil.which("pdftocairo") is None:
        raise RuntimeError(
            "未找到 TeX 工具链（pdflatex / pdftocairo），请先安装 TeX Live"
        )

    doc = build_document(latex, packages or ["booktabs", "array"])
    with tempfile.TemporaryDirectory(prefix="table_render_") as tmp_dir:
        tmp = Path(tmp_dir)
        (tmp / "table.tex").write_text(doc, encoding="utf-8")
        result = subprocess.run(
            [engine, "-interaction=nonstopmode", "-halt-on-error", "table.tex"],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        pdf = tmp / "table.pdf"
        if result.returncode != 0 or not pdf.exists():
            out_tail = (result.stdout or "")[-2000:]
            err_tail = (result.stderr or "")[-2000:]
            raise RuntimeError(
                f"pdflatex 编译失败：\n--- stdout ---\n{out_tail}\n--- stderr ---\n{err_tail}"
            )
        png_prefix = tmp / "table"
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
        rendered = tmp / "table.png"
        img = _trim_white(Image.open(rendered))
        img.save(out_png)

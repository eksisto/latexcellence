"""
内置模板编译验证与元资产生成脚本（维护者专用，不进入运行时）。

作用：
    遍历 backend/modules/template_library/builtin_templates/ 下的全部模板，
    按 manifest.json 声明的引擎（xelatex / pdflatex）用 latexmk 在临时目录
    中编译验证，报告每个模板的编译结果与耗时；配合 --gen-assets 可把
    编译产物回填为模板元资源（assets/preview.pdf + assets/thumbnail.png）。

用法：
    python tools/builtin_templates/verify_templates.py                 # 验证全部模板
    python tools/builtin_templates/verify_templates.py --gen-assets    # 验证并生成元资源
    python tools/builtin_templates/verify_templates.py --only thesis-cn

说明：
    - 依赖本机 TeX Live（latexmk / xelatex / pdflatex）；
      生成缩略图额外依赖 pdftoppm（poppler-utils）
    - 模板先复制到临时目录（剔除 assets/ 与 manifest.json）再编译，
      模拟"从模板创建项目"的干净文件树，同时避免产物污染源码树
    - 全部模板编译成功时退出码为 0，任一失败为 1（可接入 CI）
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_ROOT = Path(__file__).resolve().parents[1]  # tools/
PROJECT_ROOT = TOOLS_ROOT.parent  # 项目根（backend 所在）
sys.path.insert(0, str(TOOLS_ROOT))

from tex_path import ensure_tex_path  # noqa: E402

TEMPLATES_ROOT = (
    PROJECT_ROOT / "backend" / "modules" / "template_library" / "builtin_templates"
)

ENGINES = {"xelatex": "-xelatex", "pdflatex": "-pdf"}
LATEXMK_TIMEOUT_SECONDS = 300

# manifest.json 必填字段（与模板库 Pydantic 模型对齐，name_en 可省略）
REQUIRED_FIELDS = (
    "id",
    "name",
    "description",
    "category",
    "main_file",
    "engine",
    "author",
    "license",
    "source",
    "created_at",
)
CATEGORY_ENUM = {
    "paper",
    "thesis",
    "report",
    "slides",
    "resume",
    "book",
    "poster",
    "notes",
    "other",
}


def _load_manifest(template_dir: Path) -> dict[str, Any]:
    """读取并基础校验 manifest.json，返回解析后的字典。"""
    manifest_path = template_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{template_dir.name}: 缺少 manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    missing = [field for field in REQUIRED_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"{template_dir.name}: manifest 缺少字段 {missing}")
    if manifest["id"] != template_dir.name:
        raise ValueError(
            f"{template_dir.name}: manifest id={manifest['id']} 与目录名不一致"
        )
    if manifest["category"] not in CATEGORY_ENUM:
        raise ValueError(f"{template_dir.name}: 非法分类 {manifest['category']}")
    if manifest["engine"] not in ENGINES:
        raise ValueError(f"{template_dir.name}: 非法引擎 {manifest['engine']}")
    if manifest["source"] != "builtin":
        raise ValueError(
            f"{template_dir.name}: 内置模板 source 必须为 builtin，"
            f"当前为 {manifest['source']}"
        )
    try:
        date.fromisoformat(manifest["created_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{template_dir.name}: created_at 非 ISO 日期（YYYY-MM-DD）："
            f"{manifest['created_at']}"
        ) from exc
    if not (template_dir / manifest["main_file"]).is_file():
        raise ValueError(f"{template_dir.name}: 主文件不存在 {manifest['main_file']}")
    return manifest


def _generate_thumbnail(pdf_path: Path, assets_dir: Path) -> None:
    """用 pdftoppm 把 PDF 首页渲染为宽约 800px 的 PNG 缩略图。"""
    tmp_prefix = assets_dir / "_thumbnail_tmp"
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-f",
            "1",
            "-l",
            "1",
            "-scale-to-x",
            "800",
            "-scale-to-y",
            "-1",  # 高度按比例自动缩放
            str(pdf_path),
            str(tmp_prefix),
        ],
        capture_output=True,
        timeout=120,
        check=True,
    )
    pages = sorted(assets_dir.glob("_thumbnail_tmp*.png"))
    if not pages:
        raise RuntimeError("pdftoppm 未生成缩略图")
    pages[0].replace(assets_dir / "thumbnail.png")
    for extra in pages[1:]:
        extra.unlink()


def verify_template(
    template_dir: Path, gen_assets: bool, tmp_root: Path
) -> dict[str, Any]:
    """编译单个模板，返回 {id, engine, status, seconds, message} 报告条目。"""
    manifest = _load_manifest(template_dir)
    engine = manifest["engine"]
    entry: dict[str, Any] = {"id": manifest["id"], "engine": engine}

    # 复制到临时目录：剔除元资源目录与 manifest，模拟创建项目的干净文件树
    work_dir = tmp_root / template_dir.name
    shutil.copytree(
        template_dir,
        work_dir,
        ignore=shutil.ignore_patterns("assets", "manifest.json", "build"),
    )

    build_dir = work_dir / "build"
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "latexmk",
                ENGINES[engine],
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={build_dir.name}",
                manifest["main_file"],
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=LATEXMK_TIMEOUT_SECONDS,
        )
        seconds = time.monotonic() - start
    except subprocess.TimeoutExpired:
        entry["status"] = "fail"
        entry["seconds"] = time.monotonic() - start
        entry["message"] = f"编译超时（>{LATEXMK_TIMEOUT_SECONDS}s）"
        return entry

    pdf_path = build_dir / (Path(manifest["main_file"]).stem + ".pdf")
    if proc.returncode != 0 or not pdf_path.is_file():
        entry["status"] = "fail"
        entry["seconds"] = seconds
        errors = [
            ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.startswith("!")
        ]
        entry["message"] = " | ".join(errors[:3]) or f"latexmk 退出码 {proc.returncode}"
        return entry

    entry["status"] = "pass"
    entry["seconds"] = seconds
    entry["message"] = ""

    if gen_assets:
        assets_dir = template_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, assets_dir / "preview.pdf")
        _generate_thumbnail(pdf_path, assets_dir)
        entry["message"] = "已生成 assets/preview.pdf 与 assets/thumbnail.png"
    return entry


def verify_all(gen_assets: bool, only: str | None) -> list[dict[str, Any]]:
    """遍历模板目录逐个验证，返回报告列表。"""
    if not TEMPLATES_ROOT.is_dir():
        raise FileNotFoundError(f"模板目录不存在：{TEMPLATES_ROOT}")

    if only is not None:
        template_dirs = [TEMPLATES_ROOT / only]
        if not template_dirs[0].is_dir():
            raise FileNotFoundError(f"--only 指定的模板不存在：{only}")
    else:
        template_dirs = sorted(p for p in TEMPLATES_ROOT.iterdir() if p.is_dir())

    report: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="builtin_templates_verify_") as tmp:
        tmp_root = Path(tmp)
        for template_dir in template_dirs:
            if not (template_dir / "manifest.json").is_file():
                continue  # 跳过非模板目录
            entry = verify_template(template_dir, gen_assets, tmp_root)
            report.append(entry)
    return report


def _print_report(report: list[dict[str, Any]], gen_assets: bool) -> int:
    """打印验证报告，返回失败数。"""
    failed = sum(1 for r in report if r["status"] == "fail")
    passed = sum(1 for r in report if r["status"] == "pass")

    print("=" * 72)
    print("内置模板编译验证报告")
    print("=" * 72)
    for entry in report:
        status = entry["status"].upper()
        line = (
            f"[{status}] {entry['id']}  engine={entry['engine']}  "
            f"{entry['seconds']:.1f}s"
        )
        if entry["message"]:
            line += f"  {entry['message']}"
        print(line)
    print("=" * 72)
    print(f"通过 {passed} / 失败 {failed}")
    if gen_assets and passed:
        print("元资源（preview.pdf / thumbnail.png）已回填到各模板 assets/ 目录。")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="内置模板编译验证与元资产生成")
    parser.add_argument(
        "--gen-assets",
        action="store_true",
        help="编译成功后回填 assets/preview.pdf 与 assets/thumbnail.png",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="只处理指定目录名的模板（如 --only thesis-cn）",
    )
    args = parser.parse_args()

    ensure_tex_path()
    if shutil.which("latexmk") is None:
        print(
            "未检测到 TeX（latexmk），跳过验证。本脚本仅供维护者在装有 TeX 的机器上运行。"
        )
        return 0
    if args.gen_assets and shutil.which("pdftoppm") is None:
        print("未检测到 pdftoppm，无法生成缩略图（poppler-utils）。")
        return 1

    try:
        report = verify_all(gen_assets=args.gen_assets, only=args.only)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"验证中止：{exc}")
        return 1

    failed = _print_report(report, gen_assets=args.gen_assets)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

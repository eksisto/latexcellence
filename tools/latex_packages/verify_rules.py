"""
宏包规则表离线编译验证脚本（维护者专用，不进入运行时）。

作用：
    把语料片段包进最小文档，用本机 TeX 编译，验证"规则表给出的宏包"
    是否真的能让片段编译通过，并输出"实际需要 vs 规则表给出"差异报告。

用法：
    python tools/latex_packages/verify_rules.py

说明：
    - 需要本机安装 TeX（pdflatex / xelatex），未安装时跳过并提示
    - 编译验证只出现在维护阶段，运行时路径不依赖 TeX
    - manifest 中可标记 skip_compile（对抗样本/需要外部工具）或
      compile_extra_packages / compile_preamble / engine / aux_files
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "latex_packages"

ERROR_PATTERNS = (
    "Undefined control sequence",
    "Environment",
    "File",
    "! LaTeX Error",
    "! Package",
    "! Undefined",
)

# 注意：_extract_errors 只扫描子进程的 stdout/stderr，不读 <job>.log 文件。
# 因此这里的宽松模式（如 "File" / "Environment"）不会匹配到仅出现在 .log 里的
# 信息行（如 `File: umsa.fd ...` 字体定义行），只匹配打到终端的错误摘要。


def _load_manifest() -> dict[str, dict]:
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _build_document(
    fixture_text: str,
    packages: list[str],
    meta: dict,
) -> str:
    """把片段包进最小文档；整篇文档片段则原样返回。"""
    if meta.get("full_document"):
        if not packages:
            return fixture_text
        usepackage_block = "\n".join(f"\\usepackage{{{pkg}}}" for pkg in packages)
        lines = fixture_text.splitlines()
        for idx, line in enumerate(lines):
            if line.strip().startswith("\\documentclass"):
                lines.insert(idx + 1, usepackage_block)
                break
        else:
            return usepackage_block + "\n" + fixture_text
        return "\n".join(lines)

    ordered = meta.get("compile_package_order") or sorted(packages)
    usepackage_lines = "\n".join(f"\\usepackage{{{pkg}}}" for pkg in ordered)
    preamble = meta.get("compile_preamble", "")
    preamble_block = f"\n{preamble}" if preamble else ""
    return (
        "\\documentclass{article}\n"
        f"{usepackage_lines}{preamble_block}\n"
        "\\begin{document}\n"
        f"{fixture_text}\n"
        "\\end{document}\n"
    )


def _write_aux_files(work_dir: Path, meta: dict) -> None:
    """写入 fixture 需要的辅助文件（如 .py / .pdf）。"""
    for filename, content in (meta.get("aux_files") or {}).items():
        target = work_dir / filename
        if content == "__GENERATE_BLANK_PDF__":
            _generate_blank_pdf(work_dir, target)
        else:
            target.write_text(content, encoding="utf-8")


def _generate_blank_pdf(work_dir: Path, target: Path) -> None:
    """通过编译一个空文档生成占位 PDF。"""
    blank = work_dir / "_blank.tex"
    blank.write_text(
        "\\documentclass{article}\n\\begin{document}blank\\end{document}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(work_dir),
            str(blank),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    pdf = work_dir / "_blank.pdf"
    if pdf.exists():
        pdf.rename(target)


def _compile(engine: str, work_dir: Path, tex_path: Path) -> str:
    """编译并返回完整日志。"""
    proc = subprocess.run(
        [
            engine,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(work_dir),
            str(tex_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout + proc.stderr


def _extract_errors(log: str) -> list[str]:
    """从编译日志提取错误特征行（去重、保留顺序）。"""
    errors: list[str] = []
    for line in log.splitlines():
        if any(pattern in line for pattern in ERROR_PATTERNS):
            cleaned = line.strip()
            if cleaned and cleaned not in errors:
                errors.append(cleaned)
    return errors[:8]


def verify_all() -> list[dict]:
    """跑全部语料的编译验证，返回逐条报告。"""
    manifest = _load_manifest()
    report: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="latex_pkg_verify_") as tmp:
        work_dir = Path(tmp)
        for rel_path, meta in manifest.items():
            entry = {
                "fixture": rel_path,
                "expected": meta["expected"],
                "domain": meta.get("domain", "general"),
            }
            if meta.get("skip_compile"):
                entry["status"] = "skipped"
                entry["reason"] = meta.get("skip_reason", "")
                report.append(entry)
                continue

            fixture_text = (FIXTURES_DIR / rel_path).read_text(encoding="utf-8")
            _write_aux_files(work_dir, meta)

            engine = meta.get("engine", "pdflatex")
            recommended = list(meta["expected"])
            extra = list(meta.get("compile_extra_packages", []))
            compile_packages = recommended + extra

            # 第一遍：不带任何宏包，收集"实际需要"（未定义命令/环境）
            bare_doc = _build_document(fixture_text, [], meta)
            bare_tex = work_dir / "_bare.tex"
            bare_tex.write_text(bare_doc, encoding="utf-8")
            bare_log = _compile(engine, work_dir, bare_tex)
            actual_needs = _extract_errors(bare_log)

            # 第二遍：加载规则表推荐的宏包（+ 编译期额外包），检查残留错误
            with_doc = _build_document(fixture_text, compile_packages, meta)
            with_tex = work_dir / "_with.tex"
            with_tex.write_text(with_doc, encoding="utf-8")
            with_log = _compile(engine, work_dir, with_tex)
            residual = _extract_errors(with_log)

            entry["actual_needs"] = actual_needs
            entry["residual_errors"] = residual
            entry["status"] = "pass" if not residual else "fail"
            if meta.get("compile_note"):
                entry["note"] = meta["compile_note"]
            report.append(entry)

    return report


def _print_report(report: list[dict]) -> None:
    passed = sum(1 for r in report if r["status"] == "pass")
    skipped = sum(1 for r in report if r["status"] == "skipped")
    failed = sum(1 for r in report if r["status"] == "fail")

    print("=" * 72)
    print("宏包规则表离线编译验证报告")
    print("=" * 72)
    for entry in report:
        status = entry["status"].upper()
        print(f"\n[{status}] {entry['fixture']} (domain={entry['domain']})")
        print(f"  规则表给出: {entry['expected']}")
        if entry["status"] == "skipped":
            print(f"  跳过原因: {entry.get('reason', '')}")
            continue
        if entry.get("note"):
            print(f"  说明: {entry['note']}")
        print(f"  实际需要(无包编译错误): {entry.get('actual_needs', [])}")
        if entry["status"] == "fail":
            print(f"  残留错误(加载推荐包后): {entry.get('residual_errors', [])}")
    print("=" * 72)
    print(f"通过 {passed} / 跳过 {skipped} / 失败 {failed}")
    return failed


def main() -> int:
    if not shutil.which("pdflatex"):
        print("未检测到 TeX（pdflatex），跳过编译验证。")
        print("本脚本仅供维护者在装有 TeX 的机器上运行。")
        return 0
    report = verify_all()
    failed = _print_report(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

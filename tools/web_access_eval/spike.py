"""Run offline browser, HTML extractor and PDF parser capability spikes."""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent.parent
FIXTURE = TOOL_ROOT / "datasets" / "pages" / "technical_article.html"


def _html_spike() -> dict[str, Any]:
    import trafilatura
    from bs4 import BeautifulSoup

    html = FIXTURE.read_text(encoding="utf-8")
    started = time.perf_counter()
    soup = BeautifulSoup(html, "html.parser")
    selector = "main article h1, main article h2, main article p, main article li, main article pre"
    blocks = [node.get_text(" ", strip=True) for node in soup.select(selector)]
    bs_time = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    legacy = trafilatura.extract(html, output_format="txt") or ""
    traf_time = (time.perf_counter() - started) * 1000
    return {
        "fixture": str(FIXTURE.relative_to(PROJECT_ROOT)),
        "beautifulsoup": {
            "status": "ok",
            "block_count": len(blocks),
            "text_chars": len(" ".join(blocks)),
            "latency_ms": round(bs_time, 3),
        },
        "trafilatura_legacy": {
            "status": "ok" if legacy else "empty",
            "text_chars": len(legacy),
            "latency_ms": round(traf_time, 3),
        },
    }


def _browser_spike() -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}
    started = time.perf_counter()
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(FIXTURE.as_uri(), wait_until="domcontentloaded")
                title = page.title()
                text = page.locator("main article").inner_text()
            finally:
                browser.close()
                browser = None
        return {
            "status": "ok",
            "title": title,
            "text_chars": len(text),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:
        detail = str(exc)
        reason = (
            "browser_executable_missing"
            if "Executable doesn't exist" in detail
            else type(exc).__name__
        )
        return {"status": "unavailable", "reason": reason}
    finally:
        if browser is not None:
            browser.close()


def _pdf_spike(pdf_path: Path) -> dict[str, Any]:
    executable = shutil.which("pdftotext")
    if executable is None:
        return {"status": "unavailable", "reason": "pdftotext_not_found"}
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [executable, "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "reason": type(exc).__name__}
    return {
        "status": "ok",
        "parser": "pdftotext",
        "text_chars": len(result.stdout),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {
        "system": "stage0-spike",
        "html": _html_spike(),
        "browser": _browser_spike(),
        "pdf": _pdf_spike(args.pdf),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

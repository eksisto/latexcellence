"""
git_diff 模块纯函数单元测试。

覆盖内容：
    - _normalize_include_path 路径安全校验
    - _split_document 文档分块（preamble / body / end）
    - _diff_lines 行级差异标记
    - _build_diff_document 完整差异文档生成
    - get_pdf_path job_id 校验
"""

from pathlib import Path

import pytest

from backend.core.config import load_config, get_config
from backend.modules.git_diff.service import (
    WORKTREE_REF,
    _build_diff_document,
    _diff_lines,
    _normalize_include_path,
    _split_document,
    get_pdf_path,
)


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _ensure_config():
    try:
        get_config()
    except RuntimeError:
        project_root = Path(__file__).resolve().parent.parent.parent
        config_path = project_root / "config.json"
        if config_path.exists():
            load_config(str(config_path))
        else:
            load_config("config.json")


# ── _normalize_include_path ────────────────────────────────────────────────


class TestNormalizeIncludePath:
    """路径安全校验测试。"""

    def test_simple_relative(self):
        assert _normalize_include_path("main.tex") == "main.tex"

    def test_subdirectory(self):
        assert _normalize_include_path("chapters/intro.tex") == "chapters/intro.tex"

    def test_backslash_converted(self):
        assert _normalize_include_path("chapters\\intro.tex") == "chapters/intro.tex"

    def test_strips_whitespace(self):
        assert _normalize_include_path("  main.tex  ") == "main.tex"

    def test_empty_returns_none(self):
        assert _normalize_include_path("") is None
        assert _normalize_include_path("   ") is None

    def test_absolute_returns_none(self):
        assert _normalize_include_path("/etc/passwd") is None

    def test_parent_traversal_returns_none(self):
        assert _normalize_include_path("../secret.tex") is None
        assert _normalize_include_path("chapters/../secret.tex") is None


# ── _split_document ────────────────────────────────────────────────────────


class TestSplitDocument:
    """文档分块测试。"""

    def test_normal_document(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{amsmath}" + "\n"
            r"\begin{document}" + "\n"
            r"Hello world." + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert begin == r"\begin{document}"
        assert "Hello world." in body
        assert end == r"\end{document}"

    def test_no_begin_document(self):
        content = "just text"
        preamble, begin, body, end, tail = _split_document(content)
        assert preamble == ""
        assert begin == ""
        assert body == "just text"
        assert end == ""

    def test_no_end_document(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"body without end"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert begin == r"\begin{document}"
        assert "body without end" in body
        assert end == ""

    def test_begin_document_in_comment_ignored(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{amsmath}" + "\n"
            r"%  \begin{document} 到 \end{document} 之间的内容会输出到 PDF" + "\n"
            r"\begin{document}" + "\n"
            r"Hello world." + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert r"\documentclass" in preamble
        assert r"\usepackage{amsmath}" in preamble
        assert begin == r"\begin{document}"
        assert "Hello world." in body
        assert end == r"\end{document}"
        assert body.count(r"\begin{document}") == 0

    def test_end_document_in_comment_ignored(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"% comment with \end{document} inside" + "\n"
            r"body text" + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert begin == r"\begin{document}"
        assert "body text" in body
        assert "% comment with" in body
        assert end == r"\end{document}"

    def test_escaped_percent_not_treated_as_comment(self):
        content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"100\% \end{document} is not a real end" + "\n"
            r"\end{document}" + "\n"
        )
        preamble, begin, body, end, tail = _split_document(content)
        assert begin == r"\begin{document}"
        assert end == r"\end{document}"
        assert r"100\%" in body


# ── _diff_lines ────────────────────────────────────────────────────────────


class TestDiffLines:
    """行级差异标记测试。"""

    def test_identical_lines(self):
        base = ["line1", "line2"]
        target = ["line1", "line2"]
        output, added, deleted = _diff_lines(base, target)
        assert output == ["line1", "line2"]
        assert added == 0
        assert deleted == 0

    def test_pure_insert(self):
        base = ["line1"]
        target = ["line1", "line2"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 1
        assert deleted == 0
        assert "\\begin{LXDiffAdded}" in output
        assert "line2" in output
        assert "\\end{LXDiffAdded}" in output

    def test_pure_delete(self):
        base = ["line1", "line2"]
        target = ["line1"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 0
        assert deleted == 1
        assert "\\begin{LXDiffDeleted}" in output
        assert "line2" in output
        assert "\\end{LXDiffDeleted}" in output

    def test_replace(self):
        base = ["old line"]
        target = ["new line"]
        output, added, deleted = _diff_lines(base, target)
        assert added == 1
        assert deleted == 1
        assert "\\begin{LXDiffDeleted}" in output
        assert "\\begin{LXDiffAdded}" in output
        assert "old line" in output
        assert "new line" in output

    def test_empty_inputs(self):
        output, added, deleted = _diff_lines([], [])
        assert output == []
        assert added == 0
        assert deleted == 0


# ── _build_diff_document ───────────────────────────────────────────────────


class TestBuildDiffDocument:
    """完整差异文档生成测试。"""

    def test_diff_document_is_valid_latex(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Old content." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"New content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, target)
        assert diff.added_lines == 1
        assert diff.deleted_lines == 1
        assert r"\documentclass{article}" in diff.content
        assert r"\begin{document}" in diff.content
        assert r"\end{document}" in diff.content
        assert "LXDiffAdded" in diff.content
        assert "LXDiffDeleted" in diff.content
        assert "New content." in diff.content
        assert "Old content." in diff.content

    def test_no_changes(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Same content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, base)
        assert diff.added_lines == 0
        assert diff.deleted_lines == 0
        assert "\\begin{LXDiffAdded}" not in diff.content
        assert "\\begin{LXDiffDeleted}" not in diff.content

    def test_preamble_from_target(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{oldpkg}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\usepackage{newpkg}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, target)
        assert r"\usepackage{newpkg}" in diff.content
        assert r"\usepackage{oldpkg}" not in diff.content

    def test_diff_preamble_injected(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Content." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(base, base)
        assert r"\definecolor{LXDiffAddBg}" in diff.content
        assert r"\definecolor{LXDiffDelBg}" in diff.content
        assert r"\usepackage{xcolor}" in diff.content
        assert r"\usepackage{framed}" in diff.content


# ── get_pdf_path ───────────────────────────────────────────────────────────


class TestGetPdfPath:
    """PDF 路径校验测试。"""

    def test_invalid_job_id_rejected(self):
        with pytest.raises(ValueError):
            get_pdf_path("not-a-hex-id")

    def test_short_job_id_rejected(self):
        with pytest.raises(ValueError):
            get_pdf_path("abc123")

    def test_valid_job_id_format(self):
        with pytest.raises(FileNotFoundError):
            get_pdf_path("a" * 32)

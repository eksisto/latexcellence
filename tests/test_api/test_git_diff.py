"""
git_diff 模块纯函数单元测试。

覆盖内容：
    - _normalize_include_path 格式规范化
    - _validate_project_path 路径安全校验（委托 path_guard）
    - _split_document 文档分块（preamble / body / end）
    - _diff_lines 行级差异标记
    - _build_diff_document 完整差异文档生成
    - _resolve_color_hex 颜色格式转换
    - _build_diff_preamble 自定义颜色注入
    - _count_latexdiff_changes latexdiff 输出统计
    - get_pdf_path job_id 校验
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.modules.git_diff.service import (
    WORKTREE_REF,
    _build_diff_document,
    _build_diff_preamble,
    _count_latexdiff_changes,
    _diff_lines,
    _normalize_include_path,
    _resolve_color_hex,
    _split_document,
    _strip_blank_lines,
    _validate_project_path,
    get_pdf_path,
)

# ── _normalize_include_path ────────────────────────────────────────────────


class TestNormalizeIncludePath:
    """路径格式规范化测试。"""

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

    def test_absolute_preserved(self):
        assert _normalize_include_path("/etc/passwd") == "/etc/passwd"

    def test_parent_traversal_preserved(self):
        assert _normalize_include_path("../secret.tex") == "../secret.tex"
        assert (
            _normalize_include_path("chapters/../secret.tex")
            == "chapters/../secret.tex"
        )


# ── _validate_project_path ─────────────────────────────────────────────────


class TestValidateProjectPath:
    """路径安全校验测试（委托 path_guard）。"""

    @pytest.fixture
    def project_root(self, tmp_path):
        (tmp_path / "main.tex").write_text("test", encoding="utf-8")
        subdir = tmp_path / "chapters"
        subdir.mkdir()
        (subdir / "intro.tex").write_text("intro", encoding="utf-8")
        return tmp_path

    def test_valid_relative(self, project_root):
        result = _validate_project_path(project_root, "main.tex")
        assert result == "main.tex"

    def test_valid_subdir(self, project_root):
        result = _validate_project_path(project_root, "chapters/intro.tex")
        assert result == "chapters/intro.tex"

    def test_require_tex_pass(self, project_root):
        result = _validate_project_path(project_root, "main.tex", require_tex=True)
        assert result == "main.tex"

    def test_require_tex_reject(self, project_root):
        with pytest.raises(ValueError, match="must end with"):
            _validate_project_path(project_root, "refs.bib", require_tex=True)

    def test_empty_rejected(self, project_root):
        with pytest.raises(ValueError):
            _validate_project_path(project_root, "")

    def test_absolute_rejected(self, project_root):
        import os

        # 使用 os.path.isabs 一致的平台绝对路径
        abs_path = "C:/secret.tex" if os.name == "nt" else "/etc/passwd"
        with pytest.raises(ValueError, match="must not be an absolute path"):
            _validate_project_path(project_root, abs_path)

    def test_dotdot_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_project_path(project_root, "../secret.tex")

    def test_escape_root_rejected(self, project_root):
        outside = project_root.parent / "outside.tex"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(ValueError, match="must not contain"):
            _validate_project_path(project_root, "../outside.tex")


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


# ── _resolve_color_hex ──────────────────────────────────────────────────────


class TestResolveColorHex:
    """颜色格式转换测试。"""

    def test_empty_uses_fallback(self):
        assert _resolve_color_hex("", "DFF6DD") == "DFF6DD"

    def test_hash_prefix_stripped(self):
        assert _resolve_color_hex("#FF0000", "DFF6DD") == "FF0000"

    def test_three_char_expanded(self):
        assert _resolve_color_hex("#F00", "DFF6DD") == "FF0000"

    def test_six_char_plain(self):
        assert _resolve_color_hex("00FF00", "DFF6DD") == "00FF00"

    def test_lowercase_uppercased(self):
        assert _resolve_color_hex("#abcdef", "DFF6DD") == "ABCDEF"

    def test_invalid_hex_uses_fallback(self):
        assert _resolve_color_hex("ZZZZZZ", "DFF6DD") == "DFF6DD"

    def test_wrong_length_uses_fallback(self):
        assert _resolve_color_hex("FF00", "DFF6DD") == "DFF6DD"


# ── _build_diff_preamble ────────────────────────────────────────────────────


class TestBuildDiffPreamble:
    """差异 preamble 构建测试。"""

    def test_default_colors(self):
        preamble = _build_diff_preamble()
        assert "DFF6DD" in preamble
        assert "FFE3E3" in preamble
        assert r"\usepackage{xcolor}" in preamble
        assert r"\usepackage{framed}" in preamble

    def test_custom_colors(self):
        preamble = _build_diff_preamble("FF0000", "00FF00")
        assert "FF0000" in preamble
        assert "00FF00" in preamble
        assert "DFF6DD" not in preamble
        assert "FFE3E3" not in preamble


# ── _build_diff_document with colors ────────────────────────────────────────


class TestBuildDiffDocumentColors:
    """差异文档自定义颜色测试。"""

    def test_custom_colors_propagated(self):
        base = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"Old." + "\n"
            r"\end{document}" + "\n"
        )
        target = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"New." + "\n"
            r"\end{document}" + "\n"
        )
        diff = _build_diff_document(
            base, target, color_add="FF0000", color_del="00FF00"
        )
        assert "FF0000" in diff.content
        assert "00FF00" in diff.content
        assert "LXDiffAdded" in diff.content
        assert "LXDiffDeleted" in diff.content


# ── _count_latexdiff_changes ────────────────────────────────────────────────


class TestCountLatexdiffChanges:
    """latexdiff 输出变更统计测试。"""

    def test_no_changes(self):
        added, deleted = _count_latexdiff_changes("just plain text")
        assert added == 0
        assert deleted == 0

    def test_count_additions(self):
        content = (
            r"normal line" + "\n"
            r"\DIFadd{new content}" + "\n"
            r"\DIFadd{another addition}" + "\n"
        )
        added, deleted = _count_latexdiff_changes(content)
        assert added == 2
        assert deleted == 0

    def test_count_deletions(self):
        content = r"normal line" + "\n" r"\DIFdel{old content}" + "\n"
        added, deleted = _count_latexdiff_changes(content)
        assert added == 0
        assert deleted == 1

    def test_count_mixed(self):
        content = (
            r"\DIFdel{removed}" + "\n"
            r"kept line" + "\n"
            r"\DIFadd{added}" + "\n"
            r"\DIFDELTAMARK" + "\n"
        )
        added, deleted = _count_latexdiff_changes(content)
        assert added == 1
        assert deleted == 2


# ── _strip_blank_lines ───────────────────────────────────────────────────


class TestStripBlankLines:
    """空白行清理测试。"""

    def test_empty_content(self):
        assert _strip_blank_lines("") == ""

    def test_no_blank_lines(self):
        content = "line1\nline2\nline3"
        assert _strip_blank_lines(content) == content

    def test_single_blank_line_removed(self):
        assert _strip_blank_lines("a\n\nb") == "a\nb"

    def test_consecutive_blank_lines_removed(self):
        assert _strip_blank_lines("a\n\n\n\nb") == "a\nb"

    def test_blank_lines_with_whitespace_only(self):
        assert _strip_blank_lines("a\n   \n  \t\nb") == "a\nb"

    def test_blank_lines_inside_math_mode(self):
        content = "$$\n\nE = mc^2\n\n$$"
        assert _strip_blank_lines(content) == "$$\nE = mc^2\n$$"

    def test_blank_lines_inside_textbf(self):
        content = "\\textbf{hello\n\nworld}"
        assert _strip_blank_lines(content) == "\\textbf{hello\nworld}"

    def test_blank_lines_inside_lstdefinelanguage(self):
        content = (
            r"\lstdefinelanguage{DIFcode}{" + "\n"
            "  moredelim=...\n"
            "\n"
            "  moredelim=...\n"
            "\n"
            "}"
        )
        expected = (
            r"\lstdefinelanguage{DIFcode}{" + "\n"
            "  moredelim=...\n"
            "  moredelim=...\n"
            "}"
        )
        assert _strip_blank_lines(content) == expected

    def test_latexdiff_output_with_blank_lines(self):
        content = (
            r"\DIFadd{" + "\n"
            "new line\n"
            "\n"
            "}\n"
            "unchanged\n"
            r"\DIFdel{" + "\n"
            "old line\n"
            "\n"
            "}"
        )
        expected = (
            r"\DIFadd{" + "\n"
            "new line\n"
            "}\n"
            "unchanged\n"
            r"\DIFdel{" + "\n"
            "old line\n"
            "}"
        )
        assert _strip_blank_lines(content) == expected

    def test_preserves_normal_content(self):
        content = "\\begin{document}\nHello world.\n\\end{document}"
        assert _strip_blank_lines(content) == content


# ── /api/git-diff/pdf 端点 ─────────────────────────────────────────────────


class TestDownloadPdfEndpoint:
    """/api/git-diff/pdf/{job_id} 端点测试。

    验证 PDF 以 ``Content-Disposition: inline`` 返回，便于浏览器 iframe
    内联预览，而不是触发自动下载。
    """

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_pdf_served_inline(self, client, tmp_path):
        """PDF 存在时返回 200 且 Content-Disposition 为 inline。"""
        import hashlib

        job_id = "a" * 32
        pdf_dir = tmp_path / "diff_pdfs"
        pdf_dir.mkdir()
        pdf_path = pdf_dir / f"{job_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake diff pdf")

        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            return_value=pdf_path,
        ):
            response = client.get(f"/api/git-diff/pdf/{job_id}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        disposition = response.headers.get("content-disposition", "")
        assert "inline" in disposition
        assert "attachment" not in disposition
        assert b"%PDF" in response.content

    def test_pdf_invalid_job_id_returns_400(self, client):
        """非法 job_id 返回 400。"""
        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            side_effect=ValueError("invalid job id"),
        ):
            response = client.get("/api/git-diff/pdf/not-a-hex-id")

        assert response.status_code == 400

    def test_pdf_not_found_returns_404(self, client):
        """PDF 不存在时返回 404。"""
        with patch(
            "backend.modules.git_diff.router.get_pdf_path",
            side_effect=FileNotFoundError("missing"),
        ):
            response = client.get(f"/api/git-diff/pdf/{'a' * 32}")

        assert response.status_code == 404

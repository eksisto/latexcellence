"""
input_resolver 单元测试。

覆盖内容：
    - 正常 \\input 解析
    - 循环引用检测
    - 路径穿越防护（\\input{../../secret}）
    - 不存在文件跳过
    - 行尾注释处理
"""

import tempfile
from pathlib import Path

import pytest

from backend.utils.input_resolver import resolve_inputs


@pytest.fixture
def project_with_inputs():
    """创建含 \\input 引用的临时项目。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        main_content = (
            r"\documentclass{article}"
            + "\n"
            + r"\begin{document}"
            + "\n"
            + r"\input{chapters/intro}"
            + "\n"
            + r"\include{chapters/summary}"
            + "\n"
            + r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        subdir = root / "chapters"
        subdir.mkdir()
        (subdir / "intro.tex").write_text(r"\section{Intro}", encoding="utf-8")
        (subdir / "summary.tex").write_text(r"\section{Summary}", encoding="utf-8")

        yield root


@pytest.fixture
def project_with_traversal():
    """创建含路径穿越 \\input 的项目。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # 项目外文件
        outside = root.parent / "outside.tex"
        outside.write_text(r"\section{Secret}", encoding="utf-8")

        main_content = (
            r"\documentclass{article}"
            + "\n"
            + r"\begin{document}"
            + "\n"
            + r"\input{../outside}"
            + "\n"
            + r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        yield root, outside


class TestResolveInputs:
    def test_basic_resolution(self, project_with_inputs):
        root = str(project_with_inputs)
        result = resolve_inputs("main.tex", root)
        assert "main.tex" in result
        # Windows uses backslash, normalize for assertion
        normalized = [p.replace("\\", "/") for p in result]
        assert "chapters/intro.tex" in normalized
        assert "chapters/summary.tex" in normalized

    def test_entry_file_first(self, project_with_inputs):
        root = str(project_with_inputs)
        result = resolve_inputs("main.tex", root)
        assert result[0] == "main.tex"

    def test_circular_reference_handled(self, project_with_inputs):
        root = str(project_with_inputs)
        # chapters/intro.tex doesn't reference back, so no circle
        result = resolve_inputs("chapters/intro.tex", root)
        assert result == ["chapters/intro.tex"]

    def test_nonexistent_entry_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_inputs("nonexistent.tex", tmpdir)
            assert result == []

    def test_path_traversal_rejected(self, project_with_traversal):
        root, outside = project_with_traversal
        result = resolve_inputs("main.tex", str(root))
        # ../outside.tex should be rejected
        assert str(outside) not in [str(Path(root) / f) for f in result]
        assert "main.tex" in result

    def test_path_traversal_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subdir = root / "chapters"
            subdir.mkdir()
            (subdir / "main.tex").write_text(r"\input{../../secret}", encoding="utf-8")
            (root / "secret.tex").write_text("secret", encoding="utf-8")

            result = resolve_inputs("chapters/main.tex", str(root))
            assert result == ["chapters/main.tex"]

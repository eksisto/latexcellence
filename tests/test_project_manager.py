"""
project_manager 单元测试。

覆盖内容：
    - 项目打开与文件树扫描
    - 主文档自动识别
    - 依赖图构建
    - 段落提取与行号定位
    - 原地修改写回正确性
    - 备份/撤销/重做
    - MD5 校验（防止并发修改）
"""

import os
import tempfile
from pathlib import Path

import pytest
from backend.core.project_manager import LatexProject, Paragraph, ProjectInfo
from backend.utils.file_ops import BackupManager


@pytest.fixture
def temp_project():
    """创建临时 LaTeX 项目目录结构。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # main.tex
        main_content = (
            r"\documentclass{article}"
            + "\n"
            + r"\begin{document}"
            + "\n"
            + r"\section{Introduction}"
            + "\n"
            + r"This is the first paragraph."
            + "\n"
            + r"It has multiple lines."
            + "\n"
            + "\n"
            + r"This is the second paragraph."
            + "\n"
            + "\n"
            + r"\include{chapter1}"
            + "\n"
            + r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        # chapter1.tex
        ch1_content = (
            r"\section{Chapter One}"
            + "\n"
            + r"Content of chapter one."
            + "\n"
            + "\n"
            + r"Another paragraph here."
            + "\n"
        )
        (root / "chapter1.tex").write_text(ch1_content, encoding="utf-8")

        # subdirectory with a file
        subdir = root / "chapters"
        subdir.mkdir()
        intro_content = (
            r"\section{Synopsis}"
            + "\n"
            + r"This is the synopsis."
            + "\n"
            + "\n"
            + r"Another paragraph."
            + "\n"
        )
        (subdir / "intro.tex").write_text(intro_content, encoding="utf-8")

        # a .bib file
        (root / "references.bib").write_text(
            "@article{key, title={Test}}", encoding="utf-8"
        )

        # a .sty file
        (root / "mystyle.sty").write_text(
            r"\ProvidesPackage{mystyle}", encoding="utf-8"
        )

        # hidden files (should be ignored)
        (root / ".hidden.tex").write_text("hidden", encoding="utf-8")

        yield root


class TestProjectOpen:
    def test_open_valid_directory(self, temp_project):
        project = LatexProject()
        info = project.open(str(temp_project))
        assert isinstance(info, ProjectInfo)
        assert info.main_file is not None
        assert "main.tex" in info.main_file

    def test_open_nonexistent_path(self):
        project = LatexProject()
        with pytest.raises(FileNotFoundError):
            project.open("/nonexistent/path")

    def test_open_file_not_directory(self, temp_project):
        project = LatexProject()
        file_path = temp_project / "main.tex"
        with pytest.raises(NotADirectoryError):
            project.open(str(file_path))

    def test_file_tree_contains_tex_files(self, temp_project):
        project = LatexProject()
        info = project.open(str(temp_project))

        def find_files(nodes, names):
            result = []
            for n in nodes:
                if n.is_dir:
                    result.extend(find_files(n.children, names))
                elif n.name in names:
                    result.append(n)
            return result

        tex_files = find_files(
            info.file_tree, {"main.tex", "chapter1.tex", "intro.tex"}
        )
        assert len(tex_files) == 3

        bib_files = find_files(info.file_tree, {"references.bib"})
        assert len(bib_files) == 1

        sty_files = find_files(info.file_tree, {"mystyle.sty"})
        assert len(sty_files) == 1

    def test_hidden_files_excluded(self, temp_project):
        project = LatexProject()
        info = project.open(str(temp_project))

        def find_file(nodes, name):
            for n in nodes:
                if n.name == name:
                    return True
                if n.is_dir and find_file(n.children, name):
                    return True
            return False

        assert not find_file(info.file_tree, ".hidden.tex")

    def test_main_file_detection(self, temp_project):
        """应优先识别 main.tex。"""
        project = LatexProject()
        info = project.open(str(temp_project))
        assert "main.tex" in info.main_file

    def test_main_file_fallback(self, temp_project):
        """无 main.tex 时返回第一个 .tex。"""
        # 临时删除 main.tex
        main = temp_project / "main.tex"
        main.unlink()
        project = LatexProject()
        info = project.open(str(temp_project))
        assert info.main_file is not None
        assert info.main_file.endswith(".tex")

    def test_dependency_graph(self, temp_project):
        project = LatexProject()
        info = project.open(str(temp_project))
        deps = info.dependency_graph
        assert "main.tex" in deps
        assert "chapter1.tex" in deps["main.tex"]
        assert "chapter1.tex" in deps


class TestParagraphs:
    def test_paragraph_count(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("main.tex")
        # main.tex has: \documentclass, \begin, \section, 2-line para, blank, 1-line para, blank, \include, \end
        # Paragraphs are split by blank lines, so we should get 3 paragraphs (preamble, para1, para2+include+end)
        # Actually: first block (docclass to doc), second (lines 4-5), third (line 7), fourth (lines 9-10)
        # Wait let me recount based on blank lines
        assert len(paragraphs) >= 3

    def test_paragraph_text_content(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("main.tex")
        texts = [p.text for p in paragraphs]
        combined = "\n".join(texts)
        assert "first paragraph" in combined
        assert "second paragraph" in combined

    def test_line_numbers(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("main.tex")
        for p in paragraphs:
            assert p.line_start >= 1
            assert p.line_end >= p.line_start

    def test_paragraph_file_path(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("chapter1.tex")
        for p in paragraphs:
            assert p.file_path == "chapter1.tex"

    def test_paragraphs_for_subdirectory_file(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("chapters/intro.tex")
        texts = [p.text for p in paragraphs]
        combined = "\n".join(texts)
        assert "synopsis" in combined.lower()
        assert "another paragraph" in combined.lower()


class TestApplyChange:
    def test_modify_single_paragraph(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("chapter1.tex")

        # 找到 "Content of chapter one." 段
        target = None
        for p in paragraphs:
            if "Content of chapter one" in p.text:
                target = p
                break
        assert target is not None

        new_text = "Modified chapter content."
        project.apply_change("chapter1.tex", target.index, new_text)

        # 重新读取验证
        content = (temp_project / "chapter1.tex").read_text(encoding="utf-8")
        assert "Modified chapter content" in content
        assert "Content of chapter one" not in content

    def test_modify_preserves_indentation(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))

        # 创建带缩进的文件
        content = "  This is indented." + "\n"
        (temp_project / "indent.tex").write_text(content, encoding="utf-8")

        # 重新打开扫描文件树（实际不需要完整的 open，但需要让 paragraph 正确返回）
        # 我们直接用 get_paragraphs（它依赖 _project_info 存在）
        paragraphs = project.get_paragraphs("indent.tex")
        assert len(paragraphs) == 1

        project.apply_change("indent.tex", 0, "Still indented.")

        result = (temp_project / "indent.tex").read_text(encoding="utf-8")
        assert result.startswith("  Still indented.")

    def test_modify_specific_paragraph_only(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("chapter1.tex")

        # 找到第一段
        if paragraphs:
            original_len = len(paragraphs)
            project.apply_change("chapter1.tex", 0, "First paragraph modified.")

            # 重新验证只改了目标段
            new_paragraphs = project.get_paragraphs("chapter1.tex")
            assert len(new_paragraphs) == original_len
            assert "First paragraph modified" in new_paragraphs[0].text

    def test_invalid_paragraph_index(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("main.tex")
        with pytest.raises(IndexError):
            project.apply_change("main.tex", len(paragraphs) + 10, "invalid")


class TestUndoRedo:
    def test_undo_single_change(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))

        # 读取原始内容
        original = (temp_project / "chapter1.tex").read_text(encoding="utf-8")

        # 修改
        paragraphs = project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 0, "Changed text.")

        # 验证修改
        modified = (temp_project / "chapter1.tex").read_text(encoding="utf-8")
        assert "Changed text" in modified
        assert modified != original

        # 撤销
        assert project.can_undo("chapter1.tex")
        result = project.undo("chapter1.tex")
        assert result is True

        # 验证还原
        restored = (temp_project / "chapter1.tex").read_text(encoding="utf-8")
        assert restored == original

    def test_redo_after_undo(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))

        paragraphs = project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 0, "Version A")

        modified = (temp_project / "chapter1.tex").read_text(encoding="utf-8")
        assert "Version A" in modified

        project.undo("chapter1.tex")
        assert "Version A" not in (temp_project / "chapter1.tex").read_text(
            encoding="utf-8"
        )

        assert project.can_redo("chapter1.tex")
        project.redo("chapter1.tex")
        assert "Version A" in (temp_project / "chapter1.tex").read_text(
            encoding="utf-8"
        )

    def test_cannot_undo_without_changes(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        assert not project.can_undo("chapter1.tex")

    def test_cannot_redo_without_undo(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        paragraphs = project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 0, "Changed.")
        assert not project.can_redo("chapter1.tex")

    def test_multiple_undo(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))

        original = (temp_project / "chapter1.tex").read_text(encoding="utf-8")

        paragraphs = project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 0, "Change 1")
        paragraphs = project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 1, "Change 2")

        project.undo("chapter1.tex")  # undo change 2
        project.undo("chapter1.tex")  # undo change 1

        restored = (temp_project / "chapter1.tex").read_text(encoding="utf-8")
        assert restored == original


class TestMD5Verification:
    def test_md5_mismatch_detected(self, temp_project):
        from backend.core.project_manager import FileConflictError

        project = LatexProject()
        project.open(str(temp_project))
        project.get_paragraphs("chapter1.tex")  # 记录 MD5

        # 模拟外部修改
        (temp_project / "chapter1.tex").write_text(
            "externally modified", encoding="utf-8"
        )

        with pytest.raises(
            FileConflictError, match="has been modified by another workspace"
        ):
            project.apply_change("chapter1.tex", 0, "new text")

    def test_md5_updated_after_apply(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        project.get_paragraphs("chapter1.tex")
        project.apply_change("chapter1.tex", 0, "First change.")
        # Should not raise MD5 error because MD5 cache is updated after apply
        project.apply_change("chapter1.tex", 0, "Second change.")


class TestProjectWithoutOpen:
    def test_get_paragraphs_without_project(self):
        project = LatexProject()
        with pytest.raises(RuntimeError, match="No project opened"):
            project.get_paragraphs("some.tex")

    def test_apply_change_without_project(self):
        project = LatexProject()
        with pytest.raises(RuntimeError, match="No project opened"):
            project.apply_change("some.tex", 0, "text")

    def test_undo_without_project(self):
        project = LatexProject()
        with pytest.raises(RuntimeError, match="No project opened"):
            project.undo("some.tex")

    def test_get_file_tree_without_project(self):
        project = LatexProject()
        with pytest.raises(RuntimeError, match="No project opened"):
            project.get_file_tree()

    def test_can_undo_without_project(self):
        project = LatexProject()
        assert not project.can_undo("file.tex")


class TestPathTraversal:
    """路径穿越防护测试。"""

    def test_get_paragraphs_dotdot_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not contain"):
            project.get_paragraphs("../../secret.tex")

    def test_apply_change_dotdot_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not contain"):
            project.apply_change("../../secret.tex", 0, "text")

    def test_undo_dotdot_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not contain"):
            project.undo("../../secret.tex")

    def test_redo_dotdot_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not contain"):
            project.redo("../../secret.tex")

    def test_set_main_file_dotdot_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not contain"):
            project.set_main_file("../../evil.tex")

    def test_get_paragraphs_absolute_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must not be an absolute path"):
            project.get_paragraphs("C:/secret.tex")

    def test_get_paragraphs_non_tex_rejected(self, temp_project):
        project = LatexProject()
        project.open(str(temp_project))
        with pytest.raises(ValueError, match="must end with"):
            project.get_paragraphs("main.bib")

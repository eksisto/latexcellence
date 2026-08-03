"""段落编辑工具（edit_paragraph）LaTeX 内容校验测试。"""

import pytest

from backend.utils.project_tools.edit_tools_manager import _edit_paragraph_executor


@pytest.fixture
def temp_project(tmp_path):
    root = tmp_path / "tex_project"
    root.mkdir()
    (root / "main.tex").write_text(
        r"\documentclass{article}"
        + "\n"
        + r"\begin{document}"
        + "\n"
        + r"Hello world."
        + "\n"
        + r"\end{document}",
        encoding="utf-8",
    )
    return root


def _open_project(root):
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace("test-ws")
    pm.open(str(root))
    return pm


class TestEditParagraphLatexGuard:
    """写盘前 LaTeX 内容校验：损坏内容拒绝落盘，合法内容正常应用。"""

    @pytest.mark.asyncio
    async def test_rejects_corrupt_new_content(self, temp_project):
        _open_project(temp_project)

        result = await _edit_paragraph_executor(
            "edit_paragraph",
            {
                "_workspace_id": "test-ws",
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_content": "R^" + "\r" + "ho",  # \\rho 被 JSON 转义损坏
            },
        )

        assert "未通过 LaTeX 内容校验" in result
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "Hello world." in content  # 文件未被写入损坏内容
        assert "\r" not in content

    @pytest.mark.asyncio
    async def test_accepts_escaped_latex(self, temp_project):
        _open_project(temp_project)

        result = await _edit_paragraph_executor(
            "edit_paragraph",
            {
                "_workspace_id": "test-ws",
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_content": r"\textbf{Hello world.}",
            },
        )

        assert "成功修改" in result
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert r"\textbf{Hello world.}" in content

    @pytest.mark.asyncio
    async def test_rejects_unbalanced_braces(self, temp_project):
        _open_project(temp_project)

        result = await _edit_paragraph_executor(
            "edit_paragraph",
            {
                "_workspace_id": "test-ws",
                "file_path": "main.tex",
                "paragraph_index": 0,
                "new_content": r"\textbf{hello",
            },
        )

        assert "unbalanced braces" in result
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "Hello world." in content

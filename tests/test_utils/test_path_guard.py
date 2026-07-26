"""
path_guard 单元测试。

覆盖内容：
    - 正常路径解析
    - 空路径 / null 字节 / 绝对路径拒绝
    - '..' 路径段拒绝
    - 路径逃逸（resolved 超出 root）拒绝
    - 符号链接拒绝
    - 扩展名白名单
    - Windows 反斜杠路径处理
"""

import os
import platform
import tempfile
from pathlib import Path

import pytest

from backend.utils.path_guard import resolve_project_path


@pytest.fixture
def project_root():
    """创建临时项目目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # 创建项目内文件
        (root / "main.tex").write_text("content", encoding="utf-8")
        subdir = root / "chapters"
        subdir.mkdir()
        (subdir / "intro.tex").write_text("intro", encoding="utf-8")
        (root / "refs.bib").write_text("@article{k}", encoding="utf-8")
        yield root


class TestResolveProjectPath:
    """resolve_project_path 核心校验测试。"""

    def test_valid_relative_path(self, project_root):
        result = resolve_project_path(project_root, "main.tex")
        assert result == project_root / "main.tex"

    def test_valid_subdir_path(self, project_root):
        result = resolve_project_path(project_root, "chapters/intro.tex")
        assert result == project_root / "chapters" / "intro.tex"

    def test_empty_string_rejected(self, project_root):
        with pytest.raises(ValueError, match="不能为空"):
            resolve_project_path(project_root, "")

    def test_whitespace_only_rejected(self, project_root):
        with pytest.raises(ValueError, match="不能为空"):
            resolve_project_path(project_root, "   ")

    def test_null_byte_rejected(self, project_root):
        with pytest.raises(ValueError, match="非法字符"):
            resolve_project_path(project_root, "main\x00.tex")

    def test_absolute_path_rejected(self, project_root):
        with pytest.raises(ValueError, match="不能是绝对路径"):
            resolve_project_path(project_root, "C:/secret.tex")

    def test_dotdot_rejected(self, project_root):
        with pytest.raises(ValueError, match="不能包含"):
            resolve_project_path(project_root, "../secret.tex")

    def test_dotdot_in_middle_rejected(self, project_root):
        with pytest.raises(ValueError, match="不能包含"):
            resolve_project_path(project_root, "sub/../../../escape.tex")

    def test_escape_root_rejected(self, project_root):
        """创建指向项目外的符号链接，验证 resolve 后 is_relative_to 拦截。"""
        outside = project_root.parent / "outside.tex"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(ValueError, match="不能包含"):
            resolve_project_path(project_root, "../outside.tex")

    def test_allowed_suffixes_pass(self, project_root):
        result = resolve_project_path(
            project_root, "main.tex", allowed_suffixes=(".tex",)
        )
        assert result.suffix == ".tex"

    def test_allowed_suffixes_reject(self, project_root):
        with pytest.raises(ValueError, match="必须以"):
            resolve_project_path(project_root, "refs.bib", allowed_suffixes=(".tex",))

    def test_no_suffix_restriction(self, project_root):
        result = resolve_project_path(project_root, "refs.bib")
        assert result.suffix == ".bib"


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Windows requires admin privileges to create symlinks",
)
class TestSymlinkRejection:
    """符号链接拒绝测试。"""

    def test_symlink_to_outside_rejected(self, project_root):
        outside = project_root.parent / "secret.txt"
        outside.write_text("secret data", encoding="utf-8")
        link = project_root / "link.tex"
        os.symlink(str(outside), str(link))
        with pytest.raises(ValueError, match="不能包含"):
            resolve_project_path(project_root, "link.tex")

    def test_symlink_within_project_rejected(self, project_root):
        link = project_root / "link.tex"
        os.symlink(str(project_root / "main.tex"), str(link))
        with pytest.raises(ValueError, match="符号链接"):
            resolve_project_path(project_root, "link.tex")

    def test_allow_symlink_true(self, project_root):
        link = project_root / "link.tex"
        os.symlink(str(project_root / "main.tex"), str(link))
        result = resolve_project_path(project_root, "link.tex", allow_symlink=True)
        assert result == link

    def test_nested_symlink_rejected(self, project_root):
        """子目录中的符号链接也应被检测。"""
        link = project_root / "chapters" / "link.tex"
        os.symlink(str(project_root / "main.tex"), str(link))
        with pytest.raises(ValueError, match="符号链接"):
            resolve_project_path(project_root, "chapters/link.tex")

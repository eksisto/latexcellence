"""测试 ProjectToolsManager（项目文件浏览工具集）。"""

import tempfile
from pathlib import Path

import pytest

from backend.utils.project_tools.manager import (
    ProjectToolsManager,
    get_project_tools_manager,
    reset_project_tools_manager,
    _list_files_executor,
    _read_file_content_executor,
    _get_section_structure_executor,
    _search_across_files_executor,
)

# ── fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir():
    """创建临时 LaTeX 项目。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.tex").write_text(
            r"\documentclass{article}"
            + "\n"
            + r"\begin{document}"
            + "\n"
            + r"\section{Introduction}"
            + "\n"
            + r"Hello world."
            + "\n"
            + r"\subsection{Background}"
            + "\n"
            + r"Some text."
            + "\n"
            + r"\end{document}",
            encoding="utf-8",
        )
        chaps = root / "chapters"
        chaps.mkdir()
        (chaps / "chap1.tex").write_text(
            r"\chapter{First Chapter}"
            r"\section{Section One}"
            r"Content here."
            r"\label{sec:one}",
            encoding="utf-8",
        )
        (chaps / "chap2.tex").write_text(
            r"\chapter{Second Chapter}" r"\section{Details}" r"\cite{author2023}",
            encoding="utf-8",
        )
        (root / "refs.bib").write_text(
            r"@article{author2023, title={A}, year={2023}}",
            encoding="utf-8",
        )
        yield root


@pytest.fixture(autouse=True)
def _reset_state():
    """每条测试前重置 manager 单例，避免跨测试污染。"""
    reset_project_tools_manager()
    yield


# ── Schema 测试 ─────────────────────────────────────────────────────────


class TestSchema:
    def test_has_four_tools(self):
        schemas = ProjectToolsManager.get_tools_schema()
        assert len(schemas) == 4

    def test_tool_names(self):
        schemas = ProjectToolsManager.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert names == [
            "list_files",
            "read_file_content",
            "get_section_structure",
            "search_across_files",
        ]

    def test_list_files_parameters(self):
        schemas = ProjectToolsManager.get_tools_schema()
        sf = next(s for s in schemas if s["function"]["name"] == "list_files")
        params = sf["function"]["parameters"]
        assert params["type"] == "object"
        assert "pattern" in params["properties"]

    def test_read_file_content_required(self):
        schemas = ProjectToolsManager.get_tools_schema()
        sf = next(s for s in schemas if s["function"]["name"] == "read_file_content")
        assert "file_path" in sf["function"]["parameters"]["required"]


# ── list_files ──────────────────────────────────────────────────────────


class TestListFiles:
    @pytest.mark.asyncio
    async def test_list_tex_default(self, project_dir):
        ws_id = "test-list-default"
        pm = _open_project(ws_id, project_dir)
        result = await _list_files_executor("list_files", {"_workspace_id": ws_id})
        assert "main.tex" in result
        assert "chap1.tex" in result
        assert "chap2.tex" in result

    @pytest.mark.asyncio
    async def test_list_bib_pattern(self, project_dir):
        ws_id = "test-list-bib"
        _open_project(ws_id, project_dir)
        result = await _list_files_executor(
            "list_files", {"_workspace_id": ws_id, "pattern": "*.bib"}
        )
        assert "refs.bib" in result

    @pytest.mark.asyncio
    async def test_no_project_returns_error(self):
        result = await _list_files_executor(
            "list_files", {"_workspace_id": "nonexistent"}
        )
        assert "错误" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_parent_traversal_pattern_rejected(self, project_dir):
        ws_id = "test-list-traversal"
        _open_project(ws_id, project_dir)
        outside = project_dir.parent / "outside.tex"
        outside.write_text("secret", encoding="utf-8")
        result = await _list_files_executor(
            "list_files",
            {"_workspace_id": ws_id, "pattern": "../*.tex"},
        )
        assert "outside.tex" not in result
        assert "无效" in result or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_absolute_pattern_rejected(self, project_dir):
        ws_id = "test-list-absolute"
        _open_project(ws_id, project_dir)
        result = await _list_files_executor(
            "list_files",
            {"_workspace_id": ws_id, "pattern": str(project_dir / "*.tex")},
        )
        assert "无效" in result or "invalid" in result.lower()


# ── read_file_content ───────────────────────────────────────────────────


class TestReadFileContent:
    @pytest.mark.asyncio
    async def test_read_full_file(self, project_dir):
        ws_id = "test-read-full"
        _open_project(ws_id, project_dir)
        result = await _read_file_content_executor(
            "read_file_content",
            {"_workspace_id": ws_id, "file_path": "main.tex"},
        )
        assert "Hello world" in result
        assert "Introduction" in result
        assert "Background" in result

    @pytest.mark.asyncio
    async def test_read_line_range(self, project_dir):
        ws_id = "test-read-range"
        _open_project(ws_id, project_dir)
        result = await _read_file_content_executor(
            "read_file_content",
            {
                "_workspace_id": ws_id,
                "file_path": "main.tex",
                "start_line": 2,
                "end_line": 3,
            },
        )
        assert "Introduction" in result

    @pytest.mark.asyncio
    async def test_file_not_found(self, project_dir):
        ws_id = "test-read-notfound"
        _open_project(ws_id, project_dir)
        result = await _read_file_content_executor(
            "read_file_content",
            {"_workspace_id": ws_id, "file_path": "nonexistent.tex"},
        )
        assert "不存在" in result or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_file_path(self, project_dir):
        ws_id = "test-read-missing"
        _open_project(ws_id, project_dir)
        result = await _read_file_content_executor(
            "read_file_content", {"_workspace_id": ws_id}
        )
        assert "缺少" in result or "missing" in result.lower()

    @pytest.mark.asyncio
    async def test_start_line_beyond_total(self, project_dir):
        ws_id = "test-read-beyond"
        _open_project(ws_id, project_dir)
        result = await _read_file_content_executor(
            "read_file_content",
            {
                "_workspace_id": ws_id,
                "file_path": "main.tex",
                "start_line": 9999,
            },
        )
        assert "超出" in result or "exceed" in result.lower()

    @pytest.mark.asyncio
    async def test_unlisted_suffix_rejected(self, project_dir):
        ws_id = "test-read-secret"
        _open_project(ws_id, project_dir)
        (project_dir / "config.json").write_text(
            '{"api_key": "SECRET"}', encoding="utf-8"
        )
        result = await _read_file_content_executor(
            "read_file_content",
            {"_workspace_id": ws_id, "file_path": "config.json"},
        )
        assert "SECRET" not in result
        assert "路径验证失败" in result or "path" in result.lower()

    @pytest.mark.asyncio
    async def test_parent_traversal_rejected(self, project_dir):
        ws_id = "test-read-traversal"
        _open_project(ws_id, project_dir)
        outside = project_dir.parent / "outside.tex"
        outside.write_text("secret", encoding="utf-8")
        result = await _read_file_content_executor(
            "read_file_content",
            {"_workspace_id": ws_id, "file_path": "../outside.tex"},
        )
        assert "secret" not in result
        assert "路径验证失败" in result or "path" in result.lower()


# ── get_section_structure ───────────────────────────────────────────────


class TestGetSectionStructure:
    @pytest.mark.asyncio
    async def test_scan_all_files(self, project_dir):
        ws_id = "test-section-all"
        _open_project(ws_id, project_dir)
        result = await _get_section_structure_executor(
            "get_section_structure", {"_workspace_id": ws_id}
        )
        assert "Introduction" in result
        assert "Background" in result
        assert "First Chapter" in result
        assert "section" in result or "Section" in result

    @pytest.mark.asyncio
    async def test_scan_single_file(self, project_dir):
        ws_id = "test-section-single"
        _open_project(ws_id, project_dir)
        result = await _get_section_structure_executor(
            "get_section_structure",
            {"_workspace_id": ws_id, "file_path": "chapters/chap1.tex"},
        )
        assert "First Chapter" in result
        assert "Second Chapter" not in result

    @pytest.mark.asyncio
    async def test_file_not_found(self, project_dir):
        ws_id = "test-section-notfound"
        _open_project(ws_id, project_dir)
        result = await _get_section_structure_executor(
            "get_section_structure",
            {"_workspace_id": ws_id, "file_path": "nope.tex"},
        )
        assert "不存在" in result or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_no_sections_in_file(self, project_dir):
        ws_id = "test-section-nosect"
        _open_project(ws_id, project_dir)
        (project_dir / "empty.tex").write_text("no commands", encoding="utf-8")
        result = await _get_section_structure_executor(
            "get_section_structure",
            {"_workspace_id": ws_id, "file_path": "empty.tex"},
        )
        assert "未找到" in result or "no" in result.lower()


# ── search_across_files ─────────────────────────────────────────────────


class TestSearchAcrossFiles:
    @pytest.mark.asyncio
    async def test_search_pattern(self, project_dir):
        ws_id = "test-search-pattern"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {"_workspace_id": ws_id, "pattern": r"\\label"},
        )
        assert "sec:one" in result
        assert "chap1.tex" in result

    @pytest.mark.asyncio
    async def test_search_cite(self, project_dir):
        ws_id = "test-search-cite"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {"_workspace_id": ws_id, "pattern": r"\\cite"},
        )
        assert "author2023" in result

    @pytest.mark.asyncio
    async def test_no_match(self, project_dir):
        ws_id = "test-search-nomatch"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {"_workspace_id": ws_id, "pattern": "ZZZZNOSUCH"},
        )
        assert "未找到" in result or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_regex(self, project_dir):
        ws_id = "test-search-invalid"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {"_workspace_id": ws_id, "pattern": r"***"},
        )
        assert "无效" in result or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_pattern(self, project_dir):
        ws_id = "test-search-missing"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files", {"_workspace_id": ws_id}
        )
        assert "缺少" in result or "missing" in result.lower()

    @pytest.mark.asyncio
    async def test_file_pattern_filter(self, project_dir):
        ws_id = "test-search-filefilter"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {
                "_workspace_id": ws_id,
                "pattern": r"Content",
                "file_pattern": "**/chap1.tex",
            },
        )
        assert "Content" in result
        assert "chap2" not in result

    @pytest.mark.asyncio
    async def test_max_results_limit(self, project_dir):
        ws_id = "test-search-max"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {
                "_workspace_id": ws_id,
                "pattern": r".",
                "max_results": 3,
            },
        )
        lines = [
            l for l in result.split("\n") if l.strip() and not l.startswith("搜索")
        ]
        assert len(lines) <= 4  # 3 results + header

    @pytest.mark.asyncio
    async def test_file_pattern_traversal_rejected(self, project_dir):
        ws_id = "test-search-traversal"
        _open_project(ws_id, project_dir)
        outside = project_dir.parent / "outside.tex"
        outside.write_text("SECRETMARK", encoding="utf-8")
        result = await _search_across_files_executor(
            "search_across_files",
            {
                "_workspace_id": ws_id,
                "pattern": "SECRETMARK",
                "file_pattern": "../*.tex",
            },
        )
        assert "SECRETMARK" not in result
        assert "无效" in result or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_search_skips_unlisted_suffixes(self, project_dir):
        ws_id = "test-search-suffix"
        _open_project(ws_id, project_dir)
        (project_dir / "secret.json").write_text(
            '{"secret": "SECRETMARK"}', encoding="utf-8"
        )
        result = await _search_across_files_executor(
            "search_across_files",
            {
                "_workspace_id": ws_id,
                "pattern": "SECRETMARK",
                "file_pattern": "*.json",
            },
        )
        assert "SECRETMARK" not in result
        assert "没有匹配" in result or "未找到" in result or "no" in result.lower()

    @pytest.mark.asyncio
    async def test_max_results_is_clamped(self, project_dir):
        ws_id = "test-search-clamp"
        _open_project(ws_id, project_dir)
        result = await _search_across_files_executor(
            "search_across_files",
            {
                "_workspace_id": ws_id,
                "pattern": r".",
                "max_results": 10**9,
            },
        )
        lines = [
            l for l in result.split("\n") if l.strip() and not l.startswith("搜索")
        ]
        assert len(lines) <= 201  # 上限 200 条 + header


# ── 全局单例与注册 ─────────────────────────────────────────────────────


class TestSingleton:
    def test_singleton(self):
        m1 = get_project_tools_manager()
        m2 = get_project_tools_manager()
        assert m1 is m2

    def test_reset(self):
        m1 = get_project_tools_manager()
        reset_project_tools_manager()
        m2 = get_project_tools_manager()
        assert m1 is not m2


# ── 辅助 ────────────────────────────────────────────────────────────────


def _open_project(workspace_id: str, root: Path) -> None:
    """在指定工作区打开一个 LaTeX 项目。"""
    from backend.core.workspace_manager import get_project_manager_for_workspace

    pm = get_project_manager_for_workspace(workspace_id)
    pm.open(str(root))

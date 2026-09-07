"""
compile 模块服务层 + 路由测试。

覆盖内容：
    - CompileResult / ErrorTranslation Pydantic 模型校验
    - _extract_key_errors 错误过滤
    - clean_project_artifacts 编译前产物清理
    - compile_latex 编译流程（Mock run_tex_compile + LLM，含 clean_first）
    - /api/compile/run HTTP 端点测试
    - /api/compile/pdf HTTP 端点测试
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.compile.service import (
    CompileResult,
    _validate_main_file,
    clean_project_artifacts,
    compile_latex,
)
from backend.utils.compile_error_translator import (
    ErrorTranslation,
    extract_key_errors,
)
from backend.utils.tex_compile import TexCompileResult

# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def mock_project():
    """创建带有 temp 项目目录的 mock project_manager。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.tex").write_text(
            r"\documentclass{article}\begin{document}test\end{document}",
            encoding="utf-8",
        )
        pm = MagicMock()
        pm.is_open = True
        pm.root_path = str(root)
        yield pm


@pytest.fixture
def mock_tex_success():
    """Mock 成功编译的 TexCompileResult。"""
    return TexCompileResult(
        success=True,
        filtered_output="Output file: main.pdf\nTranscript written.",
        raw_output="Output file: main.pdf\nTranscript written.",
    )


@pytest.fixture
def mock_tex_failure():
    """Mock 失败编译的 TexCompileResult。"""
    return TexCompileResult(
        success=False,
        filtered_output="! Undefined control sequence.\nl.42 \\badcommand\n! Emergency stop.\n",
        raw_output="! Undefined control sequence.\nl.42 \\badcommand\n! Emergency stop.\n",
    )


# ── Pydantic model tests ───────────────────────────────────────────────────


class TestCompileResultModel:
    """CompileResult 模型校验测试。"""

    def test_valid_success(self):
        data = {"success": True, "stdout": "ok", "stderr": ""}
        result = CompileResult.model_validate(data)
        assert result.success is True
        assert result.explanation == ""

    def test_valid_failure_with_explanation(self):
        data = {
            "success": False,
            "stdout": "",
            "stderr": "error",
            "explanation": "编译失败",
            "fixes": ["修复建议1", "修复建议2"],
        }
        result = CompileResult.model_validate(data)
        assert result.success is False
        assert len(result.fixes) == 2

    def test_defaults(self):
        data = {"success": True}
        result = CompileResult.model_validate(data)
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.fixes == []

    def test_missing_success_raises(self):
        with pytest.raises(ValidationError):
            CompileResult.model_validate({})


class TestErrorTranslationModel:
    """ErrorTranslation 模型校验测试。"""

    def test_valid(self):
        data = {"explanation": "缺少宏包", "fixes": ["添加 \\usepackage{amsmath}"]}
        result = ErrorTranslation.model_validate(data)
        assert result.explanation == "缺少宏包"
        assert len(result.fixes) == 1

    def test_empty_fixes(self):
        data = {"explanation": "无建议", "fixes": []}
        result = ErrorTranslation.model_validate(data)
        assert result.fixes == []

    def test_missing_explanation_raises(self):
        with pytest.raises(ValidationError):
            ErrorTranslation.model_validate({"fixes": []})


# ── _extract_key_errors ───────────────────────────────────────────────────


class TestExtractKeyErrors:
    """extract_key_errors 错误日志过滤测试。"""

    def test_extracts_undefined_control_sequence(self):
        log = (
            "Some preamble\n! Undefined control sequence.\nl.42 \\badcommand\nMore text"
        )
        result = extract_key_errors(log)
        assert "Undefined control sequence" in result
        assert "\\badcommand" in result

    def test_extracts_latex_error(self):
        log = "Header\n! LaTeX Error: File `missing.sty' not found.\nTrailer"
        result = extract_key_errors(log)
        assert "LaTeX Error" in result
        assert "missing.sty" in result

    def test_extracts_emergency_stop(self):
        log = "Text\n! Emergency stop.\nl.10 \\end{document}\n"
        result = extract_key_errors(log)
        assert "Emergency stop" in result

    def test_no_error_patterns_returns_tail(self):
        log = "a" * 4000
        result = extract_key_errors(log)
        assert len(result) <= 3000

    def test_empty_log(self):
        result = extract_key_errors("")
        assert result == ""


# ── _validate_main_file ──────────────────────────────────────────────────


class TestValidateMainFile:
    """_validate_main_file 参数校验测试。"""

    @pytest.fixture
    def project_root(self, tmp_path):
        (tmp_path / "main.tex").write_text("test", encoding="utf-8")
        return tmp_path

    def test_valid_file_passes(self, project_root):
        _validate_main_file("main.tex", project_root)

    def test_valid_subdir_file_passes(self, project_root):
        subdir = project_root / "chapters"
        subdir.mkdir()
        (subdir / "ch1.tex").write_text("test", encoding="utf-8")
        _validate_main_file("chapters/ch1.tex", project_root)

    def test_empty_string_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_main_file("", project_root)

    def test_whitespace_only_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_main_file("   ", project_root)

    def test_starts_with_dash_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not start with '-'"):
            _validate_main_file("-output-directory=/tmp/evil", project_root)

        with pytest.raises(ValueError, match="must not start with '-'"):
            _validate_main_file("--xelatex", project_root)

    def test_path_traversal_rejected(self, project_root):
        with pytest.raises(ValueError, match="must not contain"):
            _validate_main_file("../../etc/passwd.tex", project_root)

        with pytest.raises(ValueError, match="must not contain"):
            _validate_main_file("sub/../../../escape.tex", project_root)

    def test_non_tex_extension_rejected(self, project_root):
        with pytest.raises(ValueError, match="must end with one of: .tex"):
            _validate_main_file("main.pdf", project_root)

        with pytest.raises(ValueError, match="must end with one of: .tex"):
            _validate_main_file("main.bib", project_root)

    def test_null_byte_rejected(self, project_root):
        with pytest.raises(ValueError, match="illegal characters"):
            _validate_main_file("main\x00.tex", project_root)

    def test_escape_root_rejected(self, project_root):
        outside = project_root.parent / "evil.tex"
        outside.write_text("evil", encoding="utf-8")
        with pytest.raises(ValueError, match="must not contain"):
            _validate_main_file("../evil.tex", project_root)

    def test_non_tex_extension_rejected_by_validate(self, project_root):
        with pytest.raises(ValueError, match="must end with one of: .tex"):
            _validate_main_file("main.pdf", project_root)


# ── clean_project_artifacts ───────────────────────────────────────────────


class TestCleanProjectArtifacts:
    """编译前产物清理测试。"""

    @pytest.fixture
    def project(self, tmp_path):
        """带再生产物（根目录 + 子目录）、源文件与 .git 的项目目录。"""
        root = tmp_path
        (root / "main.tex").write_text("x", encoding="utf-8")
        (root / "main.pdf").write_bytes(b"%PDF fake")
        (root / "refs.bib").write_text("@article{x}", encoding="utf-8")
        for name in (
            "main.aux",
            "main.log",
            "main.fdb_latexmk",
            "main.fls",
            "main.out",
            "main.toc",
            "main.bbl",
            "main.blg",
            "main.synctex.gz",
            "main.run.xml",
        ):
            (root / name).write_text("artifact", encoding="utf-8")
        sub = root / "chapters"
        sub.mkdir()
        (sub / "ch1.tex").write_text("x", encoding="utf-8")
        (sub / "ch1.aux").write_text("artifact", encoding="utf-8")
        git = root / ".git"
        git.mkdir()
        (git / "packed.log").write_text("keep me", encoding="utf-8")
        return root

    def test_removes_artifacts_in_root_and_subdirs(self, project):
        removed = clean_project_artifacts(project)
        # 根目录 10 个 + 子目录 ch1.aux 共 11 个
        assert removed == 11
        assert not (project / "main.aux").exists()
        assert not (project / "main.log").exists()
        assert not (project / "main.fdb_latexmk").exists()
        assert not (project / "main.synctex.gz").exists()
        assert not (project / "main.run.xml").exists()
        assert not (project / "chapters" / "ch1.aux").exists()

    def test_keeps_sources_and_pdf(self, project):
        clean_project_artifacts(project)
        assert (project / "main.tex").exists()
        assert (project / "main.pdf").exists()
        assert (project / "refs.bib").exists()
        assert (project / "chapters" / "ch1.tex").exists()

    def test_skips_git_dir(self, project):
        clean_project_artifacts(project)
        assert (project / ".git" / "packed.log").read_text(
            encoding="utf-8"
        ) == "keep me"

    def test_skips_symlinks(self, project):
        link = project / "external.log"
        os.symlink(project / "chapters" / "ch1.tex", link)
        clean_project_artifacts(project)
        assert link.is_symlink()

    def test_empty_project(self, tmp_path):
        assert clean_project_artifacts(tmp_path) == 0


# ── compile_latex ──────────────────────────────────────────────────────────


class TestCompileLatex:
    """compile_latex 编译流程测试。"""

    @pytest.mark.asyncio
    async def test_no_project_raises(self):
        """未打开项目时抛出 RuntimeError。"""
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace"
        ) as mock_pm:
            mock_pm.return_value = MagicMock(is_open=False)
            with pytest.raises(RuntimeError, match="No project opened"):
                await compile_latex("main.tex")

    @pytest.mark.asyncio
    async def test_file_not_found(self, mock_project):
        """主文件不存在时抛出 FileNotFoundError。"""
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with pytest.raises(FileNotFoundError, match="not found"):
                await compile_latex("nonexistent.tex")

    @pytest.mark.asyncio
    async def test_compile_success(self, mock_project, mock_tex_success):
        """编译成功场景。"""
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=mock_tex_success,
            ):
                result = await compile_latex("main.tex")

        assert result.success is True
        assert "Transcript written" in result.stdout

    @pytest.mark.asyncio
    async def test_compile_clean_first_removes_artifacts(
        self, mock_project, mock_tex_success
    ):
        """clean_first=True：编译前清理再生产物，源文件保留。"""
        root = Path(mock_project.root_path)
        for name in ("main.aux", "main.log", "main.fdb_latexmk"):
            (root / name).write_text("stale", encoding="utf-8")

        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=mock_tex_success,
            ) as mock_run:
                result = await compile_latex("main.tex", clean_first=True)

        assert result.success is True
        mock_run.assert_awaited_once()
        assert not (root / "main.aux").exists()
        assert not (root / "main.log").exists()
        assert not (root / "main.fdb_latexmk").exists()
        assert (root / "main.tex").exists()

    @pytest.mark.asyncio
    async def test_compile_default_keeps_artifacts(
        self, mock_project, mock_tex_success
    ):
        """默认 clean_first=False 不清理，既有调用方行为零变化。"""
        root = Path(mock_project.root_path)
        (root / "main.aux").write_text("stale", encoding="utf-8")

        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=mock_tex_success,
            ):
                await compile_latex("main.tex")

        assert (root / "main.aux").exists()

    @pytest.mark.asyncio
    async def test_compile_timeout(self, mock_project):
        """编译超时场景。"""
        tex_timeout = TexCompileResult(
            success=False,
            filtered_output="",
            raw_output="partial log",
            timed_out=True,
        )

        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=tex_timeout,
            ):
                result = await compile_latex("main.tex")

        assert result.success is False
        assert "timed out" in result.explanation.lower()
        assert len(result.fixes) > 0

    @pytest.mark.asyncio
    async def test_compile_failure_with_llm_translation(
        self, mock_project, mock_tex_failure
    ):
        """编译失败 → LLM 翻译错误信息。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value='{"explanation": "未定义命令", "fixes": ["检查拼写"]}'
        )

        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=mock_tex_failure,
            ):
                with patch(
                    "backend.utils.compile_error_translator.get_llm_client",
                    return_value=mock_llm,
                ):
                    result = await compile_latex("main.tex")

        assert result.success is False
        assert "Undefined control sequence" in result.stdout
        assert result.explanation == "未定义命令"
        assert result.fixes == ["检查拼写"]
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_compile_failure_llm_translation_fails(
        self, mock_project, mock_tex_failure
    ):
        """编译失败 + LLM 翻译也失败时的回退。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM offline"))

        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=mock_project,
        ):
            with patch(
                "backend.modules.compile.service.run_tex_compile",
                new_callable=AsyncMock,
                return_value=mock_tex_failure,
            ):
                with patch(
                    "backend.utils.compile_error_translator.get_llm_client",
                    return_value=mock_llm,
                ):
                    result = await compile_latex("main.tex")

        assert result.success is False
        assert "LLM" in result.explanation


# ── Router HTTP endpoint tests ─────────────────────────────────────────────


class TestCompileRouter:
    """/api/compile/run 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_run_success(self, client):
        """编译成功返回 200。"""
        mock_result = CompileResult(
            success=True,
            stdout="Output file: main.pdf",
            stderr="",
        )

        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.return_value = mock_result
            response = client.post(
                "/api/compile/run?workspace_id=test", json={"main_file": "main.tex"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_run_forwards_clean_flag(self, client):
        """clean=true 请求透传为 compile_latex(clean_first=True)。"""
        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.return_value = CompileResult(
                success=True, stdout="", stderr=""
            )
            response = client.post(
                "/api/compile/run?workspace_id=test",
                json={"main_file": "main.tex", "clean": True},
            )

        assert response.status_code == 200
        assert mock_compile.call_args.kwargs["clean_first"] is True

    def test_run_clean_defaults_false(self, client):
        """不传 clean 时默认 False，既有调用方行为零变化。"""
        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.return_value = CompileResult(
                success=True, stdout="", stderr=""
            )
            response = client.post(
                "/api/compile/run?workspace_id=test", json={"main_file": "main.tex"}
            )

        assert response.status_code == 200
        assert mock_compile.call_args.kwargs["clean_first"] is False

    def test_run_no_project_returns_400(self, client):
        """无项目时返回 400。"""
        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.side_effect = RuntimeError("No project opened")
            response = client.post(
                "/api/compile/run?workspace_id=test", json={"main_file": "main.tex"}
            )

        assert response.status_code == 400
        assert "No project opened" in response.json()["detail"]

    def test_run_file_not_found_returns_404(self, client):
        """文件不存在时返回 404。"""
        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.side_effect = FileNotFoundError("not found")
            response = client.post(
                "/api/compile/run?workspace_id=test", json={"main_file": "missing.tex"}
            )

        assert response.status_code == 404

    def test_run_compile_failure(self, client):
        """编译失败但 HTTP 仍返回 200（编译完成但有错误）。"""
        mock_result = CompileResult(
            success=False,
            stderr="! Emergency stop.",
            explanation="编译失败",
            fixes=["检查代码"],
        )

        with patch(
            "backend.modules.compile.router.compile_latex", new_callable=AsyncMock
        ) as mock_compile:
            mock_compile.return_value = mock_result
            response = client.post(
                "/api/compile/run?workspace_id=test", json={"main_file": "main.tex"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["explanation"] == "编译失败"


class TestCompilePdfEndpoint:
    """/api/compile/pdf 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_pdf_served_when_exists(self, client):
        """PDF 存在时返回 200 + application/pdf。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.tex").write_text(
                r"\documentclass{article}\begin{document}test\end{document}",
                encoding="utf-8",
            )
            (root / "main.pdf").write_bytes(b"%PDF-1.4 fake pdf content")

            pm = MagicMock()
            pm.is_open = True
            pm.root_path = str(root)

            with patch(
                "backend.modules.compile.router.get_project_manager_for_workspace",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf",
                    params={"main_file": "main.tex", "workspace_id": "test"},
                )

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            disposition = response.headers.get("content-disposition", "")
            assert "inline" in disposition
            assert "attachment" not in disposition
            assert b"%PDF" in response.content

    def test_pdf_no_project_returns_400(self, client):
        """未打开项目时返回 400。"""
        pm = MagicMock()
        pm.is_open = False

        with patch(
            "backend.modules.compile.router.get_project_manager_for_workspace",
            return_value=pm,
        ):
            response = client.get(
                "/api/compile/pdf",
                params={"main_file": "main.tex", "workspace_id": "test"},
            )

        assert response.status_code == 400

    def test_pdf_not_found_returns_404(self, client):
        """PDF 不存在时返回 404。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.tex").write_text("dummy", encoding="utf-8")

            pm = MagicMock()
            pm.is_open = True
            pm.root_path = str(root)

            with patch(
                "backend.modules.compile.router.get_project_manager_for_workspace",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf",
                    params={"main_file": "main.tex", "workspace_id": "test"},
                )

            assert response.status_code == 404


class TestCompileRequestValidation:
    """CompileRequest Pydantic 字段校验测试。"""

    def test_valid_request(self):
        from backend.modules.compile.router import CompileRequest

        req = CompileRequest(main_file="main.tex")
        assert req.main_file == "main.tex"

    def test_strips_whitespace(self):
        from backend.modules.compile.router import CompileRequest

        req = CompileRequest(main_file="  main.tex  ")
        assert req.main_file == "main.tex"

    def test_empty_rejected(self):
        from backend.modules.compile.router import CompileRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompileRequest(main_file="")

    def test_starts_with_dash_rejected(self):
        from backend.modules.compile.router import CompileRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompileRequest(main_file="--output=/tmp")

    def test_path_traversal_accepted_at_pydantic_level(self):
        from backend.modules.compile.router import CompileRequest

        req = CompileRequest(main_file="../evil.tex")
        assert req.main_file == "../evil.tex"

    def test_non_tex_accepted_at_pydantic_level(self):
        from backend.modules.compile.router import CompileRequest

        req = CompileRequest(main_file="main.pdf")
        assert req.main_file == "main.pdf"


class TestCompilePdfEndpointValidation:
    """/api/compile/pdf 参数校验测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_pdf_dangerous_main_file_rejected(self, client):
        """pdf 端点拒绝以 '-' 开头的 main_file。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = MagicMock()
            pm.is_open = True
            pm.root_path = tmpdir

            with patch(
                "backend.modules.compile.router.get_project_manager_for_workspace",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf",
                    params={
                        "main_file": "--output-directory=/tmp",
                        "workspace_id": "test",
                    },
                )

            assert response.status_code == 400
            assert "must not start with '-'" in response.json()["detail"]

    def test_pdf_path_traversal_rejected(self, client):
        """pdf 端点拒绝路径穿越。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = MagicMock()
            pm.is_open = True
            pm.root_path = tmpdir

            with patch(
                "backend.modules.compile.router.get_project_manager_for_workspace",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf",
                    params={"main_file": "../../escape.tex", "workspace_id": "test"},
                )

            assert response.status_code == 400

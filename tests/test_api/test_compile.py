"""
compile 模块服务层 + 路由测试。

覆盖内容：
    - CompileResult / ErrorTranslation Pydantic 模型校验
    - _extract_key_errors 错误过滤
    - compile_latex 编译流程（Mock subprocess + LLM）
    - /api/compile/run HTTP 端点测试
    - /api/compile/pdf HTTP 端点测试
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.compile.service import (
    CompileResult,
    ErrorTranslation,
    _extract_key_errors,
    compile_latex,
)


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
        pm._project_info = MagicMock()
        pm._project_info.root_path = str(root)
        yield pm


@pytest.fixture
def mock_subprocess_success():
    """Mock 成功编译的 subprocess。"""
    process = MagicMock()
    process.returncode = 0

    async def communicate():
        return (b"Output file: main.pdf\nTranscript written.", b"")

    process.communicate = communicate
    return process


@pytest.fixture
def mock_subprocess_failure():
    """Mock 失败编译的 subprocess。"""
    process = MagicMock()
    process.returncode = 1

    async def communicate():
        stdout = (
            b"! Undefined control sequence.\n"
            b"l.42 \\badcommand\n"
            b"! Emergency stop.\n"
        )
        return (stdout, b"")

    process.communicate = communicate
    return process


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
    """_extract_key_errors 错误日志过滤测试。"""

    def test_extracts_undefined_control_sequence(self):
        log = "Some preamble\n! Undefined control sequence.\nl.42 \\badcommand\nMore text"
        result = _extract_key_errors(log)
        assert "Undefined control sequence" in result
        assert "\\badcommand" in result

    def test_extracts_latex_error(self):
        log = "Header\n! LaTeX Error: File `missing.sty' not found.\nTrailer"
        result = _extract_key_errors(log)
        assert "LaTeX Error" in result
        assert "missing.sty" in result

    def test_extracts_emergency_stop(self):
        log = "Text\n! Emergency stop.\nl.10 \\end{document}\n"
        result = _extract_key_errors(log)
        assert "Emergency stop" in result

    def test_no_error_patterns_returns_tail(self):
        log = "a" * 4000
        result = _extract_key_errors(log)
        assert len(result) <= 3000

    def test_empty_log(self):
        result = _extract_key_errors("")
        assert result == ""


# ── compile_latex ──────────────────────────────────────────────────────────


class TestCompileLatex:
    """compile_latex 编译流程测试。"""

    @pytest.mark.asyncio
    async def test_no_project_raises(self):
        """未打开项目时抛出 RuntimeError。"""
        with patch("backend.modules.compile.service.get_project_manager") as mock_pm:
            mock_pm.return_value = MagicMock(_project_info=None)
            with pytest.raises(RuntimeError, match="No project opened"):
                await compile_latex("main.tex")

    @pytest.mark.asyncio
    async def test_file_not_found(self, mock_project):
        """主文件不存在时抛出 FileNotFoundError。"""
        with patch("backend.modules.compile.service.get_project_manager", return_value=mock_project):
            with pytest.raises(FileNotFoundError, match="not found"):
                await compile_latex("nonexistent.tex")

    @pytest.mark.asyncio
    async def test_compile_success(self, mock_project, mock_subprocess_success):
        """编译成功场景。"""
        with patch("backend.modules.compile.service.get_project_manager", return_value=mock_project):
            with patch("asyncio.create_subprocess_exec", return_value=mock_subprocess_success):
                result = await compile_latex("main.tex")

        assert result.success is True
        assert "Transcript written" in result.stdout

    @pytest.mark.asyncio
    async def test_compile_timeout(self, mock_project):
        """编译超时场景。"""
        timeout_error = asyncio.TimeoutError()

        with patch("backend.modules.compile.service.get_project_manager", return_value=mock_project):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_create:
                mock_process = MagicMock()
                mock_create.return_value = mock_process

                with patch("asyncio.wait_for", side_effect=timeout_error):
                    result = await compile_latex("main.tex")

        assert result.success is False
        assert "timed out" in result.stderr.lower()
        assert len(result.fixes) > 0

    @pytest.mark.asyncio
    async def test_compile_failure_with_llm_translation(self, mock_project, mock_subprocess_failure):
        """编译失败 → LLM 翻译错误信息。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value='{"explanation": "未定义命令", "fixes": ["检查拼写"]}')

        with patch("backend.modules.compile.service.get_project_manager", return_value=mock_project):
            with patch("asyncio.create_subprocess_exec", return_value=mock_subprocess_failure):
                with patch("backend.modules.compile.service.get_llm_client", return_value=mock_llm):
                    result = await compile_latex("main.tex")

        assert result.success is False
        assert "Undefined control sequence" in result.stdout
        assert result.explanation == "未定义命令"
        assert result.fixes == ["检查拼写"]
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_compile_failure_llm_translation_fails(self, mock_project, mock_subprocess_failure):
        """编译失败 + LLM 翻译也失败时的回退。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM offline"))

        with patch("backend.modules.compile.service.get_project_manager", return_value=mock_project):
            with patch("asyncio.create_subprocess_exec", return_value=mock_subprocess_failure):
                with patch("backend.modules.compile.service.get_llm_client", return_value=mock_llm):
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

        with patch("backend.modules.compile.router.compile_latex", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = mock_result
            response = client.post("/api/compile/run", json={"main_file": "main.tex"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_run_no_project_returns_400(self, client):
        """无项目时返回 400。"""
        with patch("backend.modules.compile.router.compile_latex", new_callable=AsyncMock) as mock_compile:
            mock_compile.side_effect = RuntimeError("No project opened")
            response = client.post("/api/compile/run", json={"main_file": "main.tex"})

        assert response.status_code == 400
        assert "No project opened" in response.json()["detail"]

    def test_run_file_not_found_returns_404(self, client):
        """文件不存在时返回 404。"""
        with patch("backend.modules.compile.router.compile_latex", new_callable=AsyncMock) as mock_compile:
            mock_compile.side_effect = FileNotFoundError("not found")
            response = client.post("/api/compile/run", json={"main_file": "missing.tex"})

        assert response.status_code == 404

    def test_run_compile_failure(self, client):
        """编译失败但 HTTP 仍返回 200（编译完成但有错误）。"""
        mock_result = CompileResult(
            success=False,
            stderr="! Emergency stop.",
            explanation="编译失败",
            fixes=["检查代码"],
        )

        with patch("backend.modules.compile.router.compile_latex", new_callable=AsyncMock) as mock_compile:
            mock_compile.return_value = mock_result
            response = client.post("/api/compile/run", json={"main_file": "main.tex"})

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
            pm._project_info = MagicMock()
            pm._project_info.root_path = str(root)

            with patch(
                "backend.modules.compile.router.get_project_manager",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf", params={"main_file": "main.tex"}
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
        pm._project_info = None

        with patch(
            "backend.modules.compile.router.get_project_manager",
            return_value=pm,
        ):
            response = client.get(
                "/api/compile/pdf", params={"main_file": "main.tex"}
            )

        assert response.status_code == 400

    def test_pdf_not_found_returns_404(self, client):
        """PDF 不存在时返回 404。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.tex").write_text("dummy", encoding="utf-8")

            pm = MagicMock()
            pm._project_info = MagicMock()
            pm._project_info.root_path = str(root)

            with patch(
                "backend.modules.compile.router.get_project_manager",
                return_value=pm,
            ):
                response = client.get(
                    "/api/compile/pdf", params={"main_file": "main.tex"}
                )

        assert response.status_code == 404

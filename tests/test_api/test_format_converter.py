"""
format_converter 模块服务层 + 路由测试。

覆盖内容：
    - FormatConverterService.convert_text 多格式文本转换
    - FormatConverterService.convert_file 文件上传转换
    - FormatConverterService.convert 向后兼容接口
    - Pandoc 不可用/错误时的处理
    - 宏包分析集成
    - markdown reader 数学定界符扩展（tex_math_single_backslash）
    - /api/format-converter/* HTTP 端点测试
"""

import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.modules.format_converter.service import (
    FormatConverterService,
)
from backend.utils.sandbox.models import ExecResult


def make_fake_runner(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> MagicMock:
    """构造带 execute() 的假 SandboxRunner（Service 层测试统一在此接缝 mock）。"""
    fake = MagicMock()
    fake.execute = AsyncMock(
        return_value=ExecResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            backend="local",
        )
    )
    return fake


# ── FormatConverterService.convert_text ──────────────────────────────────


class TestConvertText:
    """convert_text 业务逻辑测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.mark.asyncio
    async def test_empty_text_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert_text("")

    @pytest.mark.asyncio
    async def test_whitespace_only_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert_text("   \n\t  ")

    @pytest.mark.asyncio
    async def test_same_format_raises(self, service):
        with pytest.raises(ValueError, match="different"):
            await service.convert_text("# Hello", "markdown", "markdown")

    @pytest.mark.asyncio
    async def test_invalid_source_format_raises(self, service):
        with pytest.raises(ValueError, match="Unsupported source"):
            await service.convert_text("# Hello", "yaml", "latex")

    @pytest.mark.asyncio
    async def test_invalid_target_format_raises(self, service):
        with pytest.raises(ValueError, match="Unsupported target"):
            await service.convert_text("# Hello", "markdown", "yaml")

    @pytest.mark.asyncio
    async def test_pandoc_not_installed_raises(self, service):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="not installed"):
                await service.convert_text("# Hello")


class TestConvertTextWithPandoc:
    """convert_text + 宏包分析集成测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    def _patch_pandoc(self, stdout: str):
        fake_runner = make_fake_runner(stdout=stdout)
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "backend.modules.format_converter.service.get_sandbox_runner",
            return_value=fake_runner,
        )

    @pytest.mark.asyncio
    async def test_markdown_to_latex_with_packages(self, service, mock_llm):
        mock_llm.chat.return_value = {"packages": ["booktabs"]}

        with patch(
            "backend.modules.format_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(
                r"\begin{tabular}{lr}\toprule A & B \\\bottomrule\end{tabular}"
            )
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert_text("# Table", "markdown", "latex")

        assert "result" in result
        assert r"% \usepackage{booktabs}" in result["result"]

    @pytest.mark.asyncio
    async def test_markdown_to_html(self, service):
        pandoc_patches = self._patch_pandoc("<h1>Hello</h1>")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_text("# Hello", "markdown", "html")

        assert result["result"] == "<h1>Hello</h1>"

    @pytest.mark.asyncio
    async def test_html_to_latex(self, service):
        pandoc_patches = self._patch_pandoc(r"\textbf{bold}")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_text("<b>bold</b>", "html", "latex")

        assert result["result"] == r"\textbf{bold}"

    @pytest.mark.asyncio
    async def test_latex_to_markdown(self, service):
        pandoc_patches = self._patch_pandoc("**bold**")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_text(r"\textbf{bold}", "latex", "markdown")

        assert result["result"] == "**bold**"

    @pytest.mark.asyncio
    async def test_no_api_key_skips_analysis(self, service, monkeypatch):
        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.llm, "api_key", "")

        pandoc_patches = self._patch_pandoc(r"\section{Hello}")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_text("# Hello", "markdown", "latex")

        assert result["result"] == r"\section{Hello}"

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, service, mock_llm):
        mock_llm.chat.side_effect = RuntimeError("API error")

        with patch(
            "backend.modules.format_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(r"\section{Hello}")
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert_text("# Hello", "markdown", "latex")

        assert result["result"] == r"\section{Hello}"

    @pytest.mark.asyncio
    async def test_non_latex_target_skips_analysis(self, service, mock_llm):
        """目标格式非 LaTeX 时不应触发宏包分析。"""
        pandoc_patches = self._patch_pandoc("<h1>Hello</h1>")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_text("# Hello", "markdown", "html")

        assert result["result"] == "<h1>Hello</h1>"


# ── FormatConverterService.convert_file ───────────────────────────────────


class TestConvertFile:
    """convert_file 业务逻辑测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    def _patch_pandoc(self, stdout: str):
        fake_runner = make_fake_runner(stdout=stdout)
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "backend.modules.format_converter.service.get_sandbox_runner",
            return_value=fake_runner,
        )

    @pytest.mark.asyncio
    async def test_empty_file_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert_file(b"", "test.md", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_invalid_extension_raises(self, service):
        with pytest.raises(ValueError, match="Unsupported file type"):
            await service.convert_file(b"data", "test.pdf", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_extension_mismatch_raises(self, service):
        with pytest.raises(ValueError, match="does not match"):
            await service.convert_file(b"data", "test.md", "html", "latex")

    @pytest.mark.asyncio
    async def test_html_file_upload(self, service):
        html_content = "<h1>Hello</h1>"
        pandoc_patches = self._patch_pandoc(r"\section{Hello}")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_file(
                html_content.encode("utf-8"), "page.html", "html", "latex"
            )

        assert "result" in result
        assert result["output_filename"] == "page.tex"

    @pytest.mark.asyncio
    async def test_output_filename_derivation(self, service):
        pandoc_patches = self._patch_pandoc("output")
        with pandoc_patches[0], pandoc_patches[1]:
            result = await service.convert_file(
                b"# Hello", "document.md", "markdown", "html"
            )

        assert result["output_filename"] == "document.html"

    @pytest.mark.asyncio
    async def test_pandoc_error_raises(self, service):
        fake_runner = make_fake_runner(stderr="pandoc: error", returncode=1)

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch(
                "backend.modules.format_converter.service.get_sandbox_runner",
                return_value=fake_runner,
            ):
                with pytest.raises(RuntimeError, match="Pandoc conversion failed"):
                    await service.convert_file(b"data", "test.md", "markdown", "latex")


# ── FormatConverterService.convert (backward compat) ──────────────────────


class TestConvertBackwardCompat:
    """向后兼容 convert() 接口测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.mark.asyncio
    async def test_empty_markdown_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert("")

    @pytest.mark.asyncio
    async def test_pandoc_not_installed_raises(self, service):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="not installed"):
                await service.convert("# Hello")


class TestConvertCompatWithPandoc:
    """向后兼容 convert() + Pandoc 集成测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    def _patch_pandoc(self, stdout: str):
        fake_runner = make_fake_runner(stdout=stdout)
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "backend.modules.format_converter.service.get_sandbox_runner",
            return_value=fake_runner,
        )

    @pytest.mark.asyncio
    async def test_successful_conversion(self, service, mock_llm):
        mock_llm.chat.return_value = {"packages": []}

        with patch(
            "backend.modules.format_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(r"\section{Hello}")
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert("# Hello")

        assert "latex" in result
        assert result["latex"] == r"\section{Hello}"


# ── Pandoc 错误/边界条件 ────────────────────────────────────────────────


class TestPandocErrors:
    """Pandoc 自身错误处理测试。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.mark.asyncio
    async def test_pandoc_error_raises(self, service):
        fake_runner = make_fake_runner(stderr="pandoc: syntax error", returncode=1)
        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch(
                "backend.modules.format_converter.service.get_sandbox_runner",
                return_value=fake_runner,
            ):
                with pytest.raises(RuntimeError, match="Pandoc conversion failed"):
                    await service.convert_text("# Hello", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_pandoc_empty_output_raises(self, service):
        fake_runner = make_fake_runner(stdout="", stderr="")
        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch(
                "backend.modules.format_converter.service.get_sandbox_runner",
                return_value=fake_runner,
            ):
                with pytest.raises(RuntimeError, match="empty output"):
                    await service.convert_text("# Hello", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_pandoc_stderr_warning(self, service):
        fake_runner = make_fake_runner(
            stdout=r"\textbf{bold}", stderr="warning: something"
        )
        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch(
                "backend.modules.format_converter.service.get_sandbox_runner",
                return_value=fake_runner,
            ):
                result = await service.convert_text("**bold**", "markdown", "latex")

        assert result["result"] == r"\textbf{bold}"


# ── Markdown 数学定界符 reader 扩展（tex_math_single_backslash）──────────


_PANDOC_AVAILABLE = shutil.which("pandoc") is not None


class TestMarkdownMathReaderExtension:
    """markdown reader 启用 tex_math_single_backslash 的命令构造断言。"""

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    async def _capture_command(
        self, service, text: str, source: str, target: str
    ) -> list:
        fake_runner = make_fake_runner(stdout="converted")
        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch(
                "backend.modules.format_converter.service.get_sandbox_runner",
                return_value=fake_runner,
            ):
                await service.convert_text(text, source, target)
        spec = fake_runner.execute.call_args[0][0]
        return spec.command

    @pytest.mark.asyncio
    async def test_markdown_reader_enables_math_extension(self, service):
        """markdown 源：-f 必须为 markdown+tex_math_single_backslash。"""
        cmd = await self._capture_command(service, "x", "markdown", "latex")
        assert cmd[cmd.index("-f") + 1] == "markdown+tex_math_single_backslash"

    @pytest.mark.asyncio
    async def test_math_extension_not_applied_to_writer(self, service):
        """扩展只加在 reader 侧，-t latex 保持默认输出格式。"""
        cmd = await self._capture_command(service, "x", "markdown", "latex")
        assert cmd[cmd.index("-t") + 1] == "latex"

    @pytest.mark.asyncio
    async def test_math_extension_only_for_markdown_source(self, service):
        """非 markdown 源（html）不追加该扩展。"""
        cmd = await self._capture_command(service, "<b>x</b>", "html", "latex")
        assert cmd[cmd.index("-f") + 1] == "html"


class TestBackslashMathRealPandoc:
    """真实 pandoc（conftest 强制 local 模式）验证 \\(...\\) / \\[...\\] 公式识别。

    直接调用 _run_pandoc_text（与 test_docker 冒烟一致），完全绕开
    宏包分析，保证不发起任何 LLM 请求。
    """

    @pytest.fixture
    def service(self):
        return FormatConverterService()

    @pytest.mark.skipif(not _PANDOC_AVAILABLE, reason="宿主未安装 pandoc")
    @pytest.mark.asyncio
    async def test_inline_backslash_paren_math(self, service):
        """\\(E=mc^2\\) 应识别为行内公式（未被转义为字面文本）。"""
        result = await service._run_pandoc_text(r"\(E=mc^2\)", "markdown", "latex")
        assert r"\(E=mc^2\)" in result
        assert r"\^{}" not in result

    @pytest.mark.skipif(not _PANDOC_AVAILABLE, reason="宿主未安装 pandoc")
    @pytest.mark.asyncio
    async def test_display_backslash_bracket_math(self, service):
        """\\[E=mc^2\\] 应识别为行间公式。"""
        result = await service._run_pandoc_text(r"\[E=mc^2\]", "markdown", "latex")
        assert r"\[E=mc^2\]" in result
        assert r"\^{}" not in result

    @pytest.mark.skipif(not _PANDOC_AVAILABLE, reason="宿主未安装 pandoc")
    @pytest.mark.asyncio
    async def test_dollar_math_regression(self, service):
        """常规 $...$ 公式不受扩展影响（latex writer 统一输出 \\(...\\) 形式）。"""
        result = await service._run_pandoc_text(r"$E=mc^2$", "markdown", "latex")
        assert r"\(E=mc^2\)" in result
        assert r"\^{}" not in result

    @pytest.mark.skipif(not _PANDOC_AVAILABLE, reason="宿主未安装 pandoc")
    @pytest.mark.asyncio
    async def test_code_span_not_treated_as_math(self, service):
        """行内代码中的 \\(...\\) 保持字面文本，不被误判为公式。"""
        result = await service._run_pandoc_text(
            r"代码 `\(\w+\)` 演示", "markdown", "latex"
        )
        assert r"\texttt{" in result

    @pytest.mark.skipif(not _PANDOC_AVAILABLE, reason="宿主未安装 pandoc")
    @pytest.mark.asyncio
    async def test_markdown_to_html_math_span(self, service):
        """markdown → html 时公式输出 math span 而非字面括号。"""
        result = await service._run_pandoc_text(r"\(x^2\)", "markdown", "html")
        assert "math inline" in result


# ── Router HTTP endpoint tests ─────────────────────────────────────────────


class TestRouterConvert:
    """/api/format-converter/convert 端点测试（向后兼容）。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_convert_success(self, client):
        result = {"latex": r"\section{Hello}"}
        with patch.object(
            FormatConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.return_value = result
            response = client.post(
                "/api/format-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 200
        assert response.json()["latex"] == r"\section{Hello}"

    def test_convert_empty_returns_400(self, client):
        with patch.object(
            FormatConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.side_effect = ValueError("Markdown text is empty")
            response = client.post(
                "/api/format-converter/convert",
                json={"markdown": ""},
            )
        assert response.status_code == 400

    def test_convert_pandoc_not_found_returns_500(self, client):
        with patch.object(
            FormatConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.side_effect = RuntimeError("Pandoc is not installed")
            response = client.post(
                "/api/format-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 500


class TestRouterConvertText:
    """/api/format-converter/convert-text 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_convert_text_success(self, client):
        result = {"result": r"\section{Hello}"}
        with patch.object(
            FormatConverterService, "convert_text", new_callable=AsyncMock
        ) as mock:
            mock.return_value = result
            response = client.post(
                "/api/format-converter/convert-text",
                json={
                    "text": "# Hello",
                    "source_format": "markdown",
                    "target_format": "latex",
                },
            )
        assert response.status_code == 200
        assert response.json()["result"] == r"\section{Hello}"

    def test_convert_text_empty_returns_400(self, client):
        with patch.object(
            FormatConverterService, "convert_text", new_callable=AsyncMock
        ) as mock:
            mock.side_effect = ValueError("Input text is empty")
            response = client.post(
                "/api/format-converter/convert-text",
                json={
                    "text": "",
                    "source_format": "markdown",
                    "target_format": "latex",
                },
            )
        assert response.status_code == 400

    def test_convert_text_invalid_format_returns_400(self, client):
        with patch.object(
            FormatConverterService, "convert_text", new_callable=AsyncMock
        ) as mock:
            mock.side_effect = ValueError("Unsupported source format: 'yaml'")
            response = client.post(
                "/api/format-converter/convert-text",
                json={
                    "text": "hello",
                    "source_format": "yaml",
                    "target_format": "latex",
                },
            )
        assert response.status_code == 400


class TestRouterConvertFile:
    """/api/format-converter/convert-file 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_convert_file_success(self, client):
        result = {"result": r"\section{Hello}", "output_filename": "test.tex"}
        with patch.object(
            FormatConverterService, "convert_file", new_callable=AsyncMock
        ) as mock:
            mock.return_value = result
            response = client.post(
                "/api/format-converter/convert-file"
                "?source_format=markdown&target_format=latex",
                files={"file": ("test.md", b"# Hello", "text/markdown")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == r"\section{Hello}"
        assert data["output_filename"] == "test.tex"

    def test_convert_file_no_file_returns_422(self, client):
        response = client.post(
            "/api/format-converter/convert-file"
            "?source_format=markdown&target_format=latex",
        )
        assert response.status_code == 422

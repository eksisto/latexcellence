"""
format_converter 模块服务层 + 路由测试。

覆盖内容：
    - FormatConverterService.convert_text 多格式文本转换
    - FormatConverterService.convert_file 文件上传转换
    - FormatConverterService.convert 向后兼容接口
    - Pandoc 不可用/错误时的处理
    - 宏包分析集成
    - /api/format-converter/* HTTP 端点测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.modules.format_converter.service import (
    FormatConverterService,
    SUPPORTED_FORMATS,
)

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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "subprocess.run", return_value=mock_proc
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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "subprocess.run", return_value=mock_proc
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
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "pandoc: error"

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "subprocess.run", return_value=mock_proc
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
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "pandoc: syntax error"

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="Pandoc conversion failed"):
                    await service.convert_text("# Hello", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_pandoc_empty_output_raises(self, service):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="empty output"):
                    await service.convert_text("# Hello", "markdown", "latex")

    @pytest.mark.asyncio
    async def test_pandoc_stderr_warning(self, service):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = r"\textbf{bold}"
        mock_proc.stderr = "warning: something"

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                result = await service.convert_text("**bold**", "markdown", "latex")

        assert result["result"] == r"\textbf{bold}"


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

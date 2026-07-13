"""
markdown_converter 模块服务层 + 路由测试。

覆盖内容：
    - MarkdownConverterService.convert 业务逻辑（Mock pandoc）
    - Pandoc 不可用时的错误处理
    - /api/markdown-converter/convert HTTP 端点测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.modules.markdown_converter.service import MarkdownConverterService


# ── MarkdownConverterService ───────────────────────────────────────────────


class TestMarkdownConverterService:
    """MarkdownConverterService 业务逻辑测试。"""

    @pytest.fixture
    def service(self):
        return MarkdownConverterService()

    @pytest.mark.asyncio
    async def test_empty_markdown_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert("")

    @pytest.mark.asyncio
    async def test_whitespace_only_raises(self, service):
        with pytest.raises(ValueError, match="empty"):
            await service.convert("   \n\t  ")

    @pytest.mark.asyncio
    async def test_pandoc_not_installed_raises(self, service):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="not installed"):
                await service.convert("# Hello")

    @pytest.mark.asyncio
    async def test_successful_conversion(self, service):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = r"\section{Hello}"
        mock_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                result = await service.convert("# Hello")

        assert "latex" in result
        assert result["latex"] == r"\section{Hello}"

    @pytest.mark.asyncio
    async def test_pandoc_error_raises(self, service):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "pandoc: syntax error"

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="Pandoc conversion failed"):
                    await service.convert("# Hello")

    @pytest.mark.asyncio
    async def test_pandoc_empty_output_raises(self, service):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="empty output"):
                    await service.convert("# Hello")

    @pytest.mark.asyncio
    async def test_pandoc_stderr_warning(self, service):
        """Pandoc 有 stderr 输出但 returncode=0 → 正常返回，stderr 被警告。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = r"\textbf{bold}"
        mock_proc.stderr = "warning: something"

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                result = await service.convert("**bold**")

        assert result["latex"] == r"\textbf{bold}"


# ── Router HTTP endpoint tests ─────────────────────────────────────────────


class TestMarkdownConverterRouter:
    """/api/markdown-converter/convert 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_convert_success(self, client):
        result = {"latex": r"\section{Hello}"}
        with patch.object(MarkdownConverterService, "convert", new_callable=AsyncMock) as mock_convert:
            mock_convert.return_value = result
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["latex"] == r"\section{Hello}"

    def test_convert_empty_markdown_returns_400(self, client):
        """空 markdown 由路由层转发 400。"""
        with patch.object(MarkdownConverterService, "convert", new_callable=AsyncMock) as mock_convert:
            mock_convert.side_effect = ValueError("Markdown text is empty")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": ""},
            )
        assert response.status_code == 400

    def test_convert_pandoc_not_found_returns_500(self, client):
        with patch.object(MarkdownConverterService, "convert", new_callable=AsyncMock) as mock_convert:
            mock_convert.side_effect = RuntimeError("Pandoc is not installed")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 500

    def test_convert_internal_error_returns_500(self, client):
        with patch.object(MarkdownConverterService, "convert", new_callable=AsyncMock) as mock_convert:
            mock_convert.side_effect = RuntimeError("Something went wrong")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 500

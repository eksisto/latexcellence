"""
markdown_converter 模块服务层 + 路由测试。

覆盖内容：
    - MarkdownConverterService.convert 业务逻辑（Mock pandoc + Mock LLM）
    - Pandoc 不可用时的错误处理
    - 宏包分析集成测试
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

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

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


class TestMarkdownConverterWithPandoc:
    """Pandoc 转换 + 宏包分析集成测试（Mock pandoc + Mock LLM）。"""

    @pytest.fixture
    def service(self):
        return MarkdownConverterService()

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    def _patch_pandoc(self, stdout: str):
        """辅助函数：mock pandoc 调用返回指定 stdout。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = stdout
        mock_proc.stderr = ""
        return patch("shutil.which", return_value="/usr/bin/pandoc"), patch(
            "subprocess.run", return_value=mock_proc
        )

    @pytest.mark.asyncio
    async def test_successful_conversion_with_packages(self, service, mock_llm):
        """Pandoc 转换后，宏包分析检测到 booktabs。"""
        mock_llm.chat.return_value = {"packages": ["booktabs"]}

        with patch(
            "backend.modules.markdown_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(
                r"\begin{table}\centering\begin{tabular}{lr}\toprule A & B \\\midrule 1 & 2 \\\bottomrule\end{tabular}\end{table}"
            )
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert("# Table")

        assert "latex" in result
        assert r"% \usepackage{booktabs}" in result["latex"]
        assert r"\toprule" in result["latex"]

    @pytest.mark.asyncio
    async def test_successful_conversion_no_packages(self, service, mock_llm):
        """Pandoc 转换后，宏包分析返回空列表 → 无注释。"""
        mock_llm.chat.return_value = {"packages": []}

        with patch(
            "backend.modules.markdown_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(r"\section{Hello}")
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert("# Hello")

        assert "latex" in result
        assert result["latex"] == r"\section{Hello}"
        assert not result["latex"].startswith(r"%")

    @pytest.mark.asyncio
    async def test_no_api_key_skips_analysis(self, service, monkeypatch):
        """无 API Key 时跳过宏包分析，直接返回 Pandoc 结果。"""
        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        monkeypatch.setattr(cfg.llm, "api_key", "")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = r"\section{Hello}"
        mock_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/pandoc"):
            with patch("subprocess.run", return_value=mock_proc):
                result = await service.convert("# Hello")

        assert result["latex"] == r"\section{Hello}"

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, service, mock_llm):
        """LLM 宏包分析失败 → 返回原始 Pandoc 结果。"""
        mock_llm.chat.side_effect = RuntimeError("API error")

        with patch(
            "backend.modules.markdown_converter.service.get_llm_client",
            return_value=mock_llm,
        ):
            pandoc_patches = self._patch_pandoc(r"\section{Hello}")
            with pandoc_patches[0], pandoc_patches[1]:
                result = await service.convert("# Hello")

        assert result["latex"] == r"\section{Hello}"


# ── Pandoc 错误/边界条件 ────────────────────────────────────────────────


class TestPandocErrors:
    """Pandoc 自身错误处理测试（不涉及宏包分析）。"""

    @pytest.fixture
    def service(self):
        return MarkdownConverterService()

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
        with patch.object(
            MarkdownConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
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
        with patch.object(
            MarkdownConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.side_effect = ValueError("Markdown text is empty")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": ""},
            )
        assert response.status_code == 400

    def test_convert_pandoc_not_found_returns_500(self, client):
        with patch.object(
            MarkdownConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.side_effect = RuntimeError("Pandoc is not installed")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 500

    def test_convert_internal_error_returns_500(self, client):
        with patch.object(
            MarkdownConverterService, "convert", new_callable=AsyncMock
        ) as mock_convert:
            mock_convert.side_effect = RuntimeError("Something went wrong")
            response = client.post(
                "/api/markdown-converter/convert",
                json={"markdown": "# Hello"},
            )
        assert response.status_code == 500

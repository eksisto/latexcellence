"""
style_modify 模块服务层 + 路由测试。

覆盖内容：
    - 分隔符协议提取（<<<LATEX>>>...<<<END>>>）
    - StyleModifyService.modify 业务逻辑（Mock LLM）
    - /api/style-modify/modify HTTP 端点测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.modules.style_modify.service import StyleModifyService


# ── 分隔符协议提取 ─────────────────────────────────────────────────────────


class TestExtractLatexBlock:
    """<<<LATEX>>>...<<<END>>> 块提取测试。"""

    def test_extract_simple(self):
        resp = r"<<<LATEX>>>" "\n" r"\textbf{Hello}" "\n" r"<<<END>>>"
        assert StyleModifyService._extract_latex_block(resp) == r"\textbf{Hello}"

    def test_extract_with_surrounding_text(self):
        resp = (
            "Here is the result:\n"
            r"<<<LATEX>>>" "\n" r"\begin{document}hello\end{document}" "\n"
            r"<<<END>>>" "\n"
            "Hope this helps."
        )
        assert StyleModifyService._extract_latex_block(resp) == (
            r"\begin{document}hello\end{document}"
        )

    def test_extract_multiline(self):
        resp = r"<<<LATEX>>>" "\nline1\nline2\n" r"<<<END>>>"
        assert StyleModifyService._extract_latex_block(resp) == "line1\nline2"

    def test_extract_case_insensitive(self):
        resp = r"<<<latex>>>" "\n" r"\alpha" "\n" r"<<<end>>>"
        assert StyleModifyService._extract_latex_block(resp) == r"\alpha"

    def test_missing_block_returns_none(self):
        assert StyleModifyService._extract_latex_block("no block here") is None


# ── StyleModifyService.modify ──────────────────────────────────────────────


class TestStyleModifyService:
    """StyleModifyService 业务逻辑测试。"""

    @pytest.fixture
    def service(self):
        return StyleModifyService()

    @pytest.fixture
    def sample_latex(self):
        return (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"Hello world." + "\n"
            r"\end{document}"
        )

    @staticmethod
    def _block(latex: str) -> str:
        """按分隔符协议包装一段 LaTeX。"""
        return f"<<<LATEX>>>\n{latex}\n<<<END>>>"

    @pytest.mark.asyncio
    async def test_no_api_key_raises(self, service, sample_latex):
        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = ""

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with pytest.raises(RuntimeError, match="No API key"):
                await service.modify(sample_latex, "bold")

    @pytest.mark.asyncio
    async def test_successful_modification(self, service, sample_latex):
        mock_llm = MagicMock()
        modified_latex = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"\textbf{Hello world.}" + "\n"
            r"\end{document}"
        )
        mock_llm.chat = AsyncMock(return_value=self._block(modified_latex))

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                result = await service.modify(sample_latex, "make it bold")

        assert result["latex"] == modified_latex
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_literal_backslashes_preserved(self, service, sample_latex):
        """分隔符协议下反斜杠原样传输，不再经过 JSON 转义（旧版 \\rho 会被解析成回车）。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=self._block(r"R^\rho is fine"))

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                result = await service.modify(sample_latex, "add a formula")

        assert result["latex"] == r"R^\rho is fine"

    @pytest.mark.asyncio
    async def test_llm_empty_latex_falls_back(self, service, sample_latex):
        """LLM 返回空 LaTeX 块时回退到原始输入。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=self._block(""))

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                result = await service.modify(sample_latex, "bold")

        assert result["latex"] == sample_latex

    @pytest.mark.asyncio
    async def test_missing_block_retries_then_raises(self, service, sample_latex):
        """LLM 未按分隔符协议输出 → 重试 3 次 → RuntimeError。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value="Here is the modified code: ...")

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                with pytest.raises(RuntimeError, match="Style modification failed"):
                    await service.modify(sample_latex, "bold")

        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_validation_failure_retries(self, service, sample_latex):
        """内容校验失败（花括号不配平）→ 回喂重试 → 返回修正后的结果。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            side_effect=[
                self._block(r"\textbf{Hello"),  # 花括号不配平
                self._block(r"\textbf{Hello}"),
            ]
        )

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                result = await service.modify(sample_latex, "make it bold")

        assert result["latex"] == r"\textbf{Hello}"
        assert mock_llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_llm_exception_raises(self, service, sample_latex):
        """LLM 调用异常 → RuntimeError。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("Network error"))

        mock_cfg = MagicMock()
        mock_cfg.llm.api_key = "sk-test"

        with patch(
            "backend.modules.style_modify.service.get_config", return_value=mock_cfg
        ):
            with patch(
                "backend.modules.style_modify.service.get_llm_client",
                return_value=mock_llm,
            ):
                with pytest.raises(RuntimeError, match="Style modification failed"):
                    await service.modify(sample_latex, "bold")


# ── Router HTTP endpoint tests ─────────────────────────────────────────────


class TestStyleModifyRouter:
    """/api/style-modify/modify 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_modify_success(self, client):
        result = {"latex": r"\textbf{Hello}"}
        with patch.object(
            StyleModifyService, "modify", new_callable=AsyncMock
        ) as mock_modify:
            mock_modify.return_value = result
            response = client.post(
                "/api/style-modify/modify",
                json={"latex": "Hello", "description": "make it bold"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["latex"] == r"\textbf{Hello}"

    def test_modify_empty_latex_returns_400(self, client):
        response = client.post(
            "/api/style-modify/modify",
            json={"latex": "", "description": "bold"},
        )
        # Pydantic field_validator rejects at request level
        assert response.status_code == 422

    def test_modify_empty_description_returns_400(self, client):
        response = client.post(
            "/api/style-modify/modify",
            json={"latex": "Hello", "description": ""},
        )
        assert response.status_code == 422

    def test_modify_service_value_error_returns_400(self, client):
        with patch.object(
            StyleModifyService, "modify", new_callable=AsyncMock
        ) as mock_modify:
            mock_modify.side_effect = ValueError("Markdown text is empty")
            response = client.post(
                "/api/style-modify/modify",
                json={"latex": "Hello", "description": "bold"},
            )
        assert response.status_code == 400

    def test_modify_service_runtime_error_returns_500(self, client):
        with patch.object(
            StyleModifyService, "modify", new_callable=AsyncMock
        ) as mock_modify:
            mock_modify.side_effect = RuntimeError("Internal error")
            response = client.post(
                "/api/style-modify/modify",
                json={"latex": "Hello", "description": "bold"},
            )
        assert response.status_code == 500

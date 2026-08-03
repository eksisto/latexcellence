"""
style_modify 模块服务层 + 路由测试。

覆盖内容：
    - StyleModifyResult Pydantic 模型校验
    - StyleModifyService.modify 业务逻辑（Mock LLM）
    - /api/style-modify/modify HTTP 端点测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.style_modify.service import (
    StyleModifyResult,
    StyleModifyService,
)

# ── Pydantic model tests ───────────────────────────────────────────────────


class TestStyleModifyResultModel:
    """StyleModifyResult 模型校验测试。"""

    def test_valid(self):
        data = {"latex": r"\textbf{Hello}"}
        result = StyleModifyResult.model_validate(data)
        assert result.latex == r"\textbf{Hello}"

    def test_missing_latex_raises(self):
        with pytest.raises(ValidationError):
            StyleModifyResult.model_validate({})

    def test_empty_latex_allowed_by_model(self):
        # Pydantic 模型允许空字符串（业务逻辑层会检查）
        result = StyleModifyResult.model_validate({"latex": ""})
        assert result.latex == ""


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
        import json

        mock_llm = MagicMock()
        modified_latex = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"\textbf{Hello world.}" + "\n"
            r"\end{document}"
        )
        mock_llm.chat = AsyncMock(return_value=json.dumps({"latex": modified_latex}))

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

        assert "latex" in result
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_empty_latex_falls_back(self, service, sample_latex):
        """LLM 返回空 latex 字段时回退到原始输入。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value='{"latex": ""}')

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
    async def test_llm_validation_exhausted_raises(self, service, sample_latex):
        """LLM 返回无效 JSON → retry_with_validation 耗尽重试 → RuntimeError。"""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value='{"wrong_field": true}')

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

    @pytest.mark.asyncio
    async def test_llm_unescaped_backslash_retries(self, service, sample_latex):
        """
        LLM 输出未转义的 \\rho（JSON 解析后变成回车 + ho）→ 触发重试，
        最终结果无控制字符。
        """
        import json

        mock_llm = MagicMock()
        # 模拟 LLM 原始输出：JSON 字符串里只有一个反斜杠（\\rho 会被 JSON 解析成回车）
        corrupt = '{"latex": "R^\\rho is fine"}'
        good = json.dumps({"latex": r"R^{\rho} is fine"})
        mock_llm.chat = AsyncMock(side_effect=[corrupt, good])

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

        assert result["latex"] == r"R^{\rho} is fine"
        assert mock_llm.chat.call_count == 2


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

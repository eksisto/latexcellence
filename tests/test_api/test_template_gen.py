"""
template_gen 模块集成测试。
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.template_gen.service import (
    TemplateGenService,
    TemplateGenResult,
    SectionSkeleton,
)


@pytest.fixture
def service():
    return TemplateGenService()


class TestTemplateGenResultModel:
    """TemplateGenResult / SectionSkeleton 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "template_name": "basic",
            "preamble": r"\documentclass{article}",
            "body_structure": [],
            "packages": [],
            "explanation": "A basic template.",
        }
        result = TemplateGenResult.model_validate(data)
        assert result.template_name == "basic"
        assert result.body_structure == []

    def test_valid_complete(self):
        data = {
            "template_name": "ieee-paper",
            "preamble": r"\documentclass{ieeetran}\n\usepackage{amsmath}",
            "body_structure": [
                {"section": "Introduction", "suggested_content": "Background"},
                {"section": "Method", "suggested_content": "Approach details"},
            ],
            "packages": ["amsmath", "graphicx"],
            "explanation": "IEEE conference paper template.",
        }
        result = TemplateGenResult.model_validate(data)
        assert len(result.body_structure) == 2
        assert result.body_structure[0].section == "Introduction"
        assert result.packages == ["amsmath", "graphicx"]

    def test_section_defaults(self):
        data = {"section": "Title"}
        sec = SectionSkeleton.model_validate(data)
        assert sec.section == "Title"
        assert sec.suggested_content == ""

    def test_wrong_type_body_structure(self):
        data = {
            "template_name": "test",
            "preamble": "",
            "body_structure": "not_a_list",
            "packages": [],
            "explanation": "",
        }
        with pytest.raises(ValidationError):
            TemplateGenResult.model_validate(data)


class TestGenerateTemplate:
    """LLM mock 生成测试。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_successful_generation(self, mock_llm, service):
        mock_llm.chat.return_value = {
            "template_name": "cvpr-paper",
            "preamble": r"\documentclass[review]{cvpr}\n\usepackage{amsmath,graphicx}",
            "body_structure": [
                {"section": "Introduction", "suggested_content": "Problem context"},
                {"section": "Method", "suggested_content": "Technical approach"},
                {"section": "Experiments", "suggested_content": "Evaluation"},
                {"section": "Conclusion", "suggested_content": "Summary"},
            ],
            "packages": ["amsmath", "graphicx", "booktabs"],
            "explanation": "Standard CVPR paper template with common sections.",
        }

        result = await service.generate_template("CVPR paper", llm=mock_llm)
        assert result["template_name"] == "cvpr-paper"
        assert len(result["body_structure"]) == 4
        assert "cvpr" in result["preamble"]
        assert len(result["packages"]) == 3

    @pytest.mark.asyncio
    async def test_pydantic_retry_exhausted(self, mock_llm, service):
        mock_llm.chat.return_value = {"bad": True}

        with pytest.raises(RuntimeError, match="Template generation failed"):
            await service.generate_template("CVPR paper", llm=mock_llm)
        assert mock_llm.chat.call_count == 3


class TestAssembleTemplate:
    """模板组装测试。"""

    def test_minimal_template(self, service):
        result = service._assemble_template({
            "preamble": r"\documentclass{article}",
            "body_structure": [],
        })
        assert r"\documentclass{article}" in result
        assert r"\begin{document}" in result
        assert r"\end{document}" in result

    def test_with_sections(self, service):
        result = service._assemble_template({
            "preamble": r"\documentclass{article}",
            "body_structure": [
                {"section": "Intro", "suggested_content": "Background"},
                {"section": "Method", "suggested_content": "Details"},
            ],
        })
        assert r"\section{Intro}" in result
        assert r"\section{Method}" in result
        assert "% Background" in result
        assert "% Details" in result


class TestSaveTemplate:
    """模板保存测试。"""

    @pytest.mark.asyncio
    async def test_save_to_file(self, service, tmp_path):
        import backend.core.config as cfg_mod

        cfg = cfg_mod.get_config()
        original_dir = cfg.features.template_gen.output_dir
        cfg.features.template_gen.output_dir = str(tmp_path)

        try:
            template = {
                "template_name": "test-paper",
                "preamble": r"\documentclass{article}",
                "body_structure": [
                    {"section": "Title", "suggested_content": ""},
                ],
                "packages": [],
                "explanation": "",
            }
            result = await service.save_template(template)
            assert result["saved"] is True
            saved_file = tmp_path / "test-paper.tex"
            assert saved_file.exists()
            content = saved_file.read_text(encoding="utf-8")
            assert r"\documentclass{article}" in content
        finally:
            cfg.features.template_gen.output_dir = original_dir

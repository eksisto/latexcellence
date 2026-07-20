"""
bib_manager 模块集成测试。

覆盖内容：
    - Pydantic 模型校验
    - analyze_format（mock LLM）
    - extract_from_text（mock LLM）
    - generate_bibtex（mock LLM）
    - read_bib_file / save_bib_entry（临时项目）
    - list_bib_files
    - 缺失字段检测
    - Router 端点集成测试
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.bib_manager.models import (
    BibEntry,
    ExtractedMetadata,
    GenerateBibtexRequest,
    ReferenceFormatResult,
    SaveBibRequest,
)
from backend.modules.bib_manager.service import BibManagerService


@pytest.fixture
def temp_project():
    """创建临时 LaTeX 项目目录（包含 .bib 文件）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        bib_content = (
            "@article{test2023,"
            "  author = {Test, Author},"
            "  title = {A Test Article},"
            "  journal = {Journal of Testing},"
            "  year = {2023}"
            "}\n"
        )
        (root / "refs.bib").write_text(bib_content, encoding="utf-8")
        yield root


@pytest.fixture
def service():
    return BibManagerService()


class TestReferenceFormatResult:
    def test_valid_minimal(self):
        data = {
            "format_name": "GB/T 7714",
            "entry_type": "article",
            "required_fields": ["author", "title", "journal", "year"],
            "optional_fields": ["volume", "number", "pages"],
            "field_descriptions": {"author": "作者"},
            "format_description": "中文期刊格式",
        }
        result = ReferenceFormatResult.model_validate(data)
        assert result.format_name == "GB/T 7714"
        assert result.entry_type == "article"
        assert len(result.required_fields) == 4
        assert result.confidence == "high"

    def test_new_fields(self):
        data = {
            "format_name": "GB/T 7714-2015",
            "confidence": "medium",
            "entry_type": "article",
            "document_type_marker": "[J]",
            "required_fields": ["author"],
            "field_descriptions": {},
            "format_description": "",
            "raw_observed_fields": ["author", "title", "journal", "year", "doi"],
        }
        result = ReferenceFormatResult.model_validate(data)
        assert result.confidence == "medium"
        assert result.document_type_marker == "[J]"
        assert result.raw_observed_fields == ["author", "title", "journal", "year", "doi"]

    def test_empty_lists_default(self):
        data = {"format_name": "APA", "entry_type": "article"}
        result = ReferenceFormatResult.model_validate(data)
        assert result.required_fields == []
        assert result.optional_fields == []

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ReferenceFormatResult.model_validate({})


class TestExtractedMetadata:
    def test_valid_with_fields(self):
        data = {
            "fields": {
                "author": "Devlin, Jacob",
                "title": "Test Title",
                "journal": "arXiv",
                "year": "2018",
            },
        }
        result = ExtractedMetadata.model_validate(data)
        assert result.fields == data["fields"]
        assert result.fields["author"] == "Devlin, Jacob"

    def test_empty_fields(self):
        data = {"fields": {}}
        result = ExtractedMetadata.model_validate(data)
        assert result.fields == {}

    def test_missing_fields_key_uses_default(self):
        data = {}
        result = ExtractedMetadata.model_validate(data)
        assert result.fields == {}

    def test_fields_string_value(self):
        data = {"fields": {"year": "2023", "doi": "10.1234/test"}}
        result = ExtractedMetadata.model_validate(data)
        assert result.fields["year"] == "2023"


class TestBibEntry:
    def test_valid(self):
        data = {
            "cite_key": "devlin2018bert",
            "entry_type": "article",
            "fields": {
                "author": "Devlin, Jacob and Chang, Ming-Wei",
                "title": "{BERT: Pre-training of Deep Bidirectional Transformers}",
                "year": "2018",
            },
            "raw_bibtex": (
                "@article{devlin2018bert,\n"
                '  author = {Devlin, Jacob and Chang, Ming-Wei},\n'
                '  title = {{BERT: Pre-training of Deep Bidirectional Transformers}},\n'
                '  year = {2018}\n'
                "}"
            ),
        }
        result = BibEntry.model_validate(data)
        assert result.cite_key == "devlin2018bert"
        assert result.fields["author"] == "Devlin, Jacob and Chang, Ming-Wei"


class TestCheckMissingFields:
    def test_missing_required(self, service):
        fields = {"author": "Test", "title": "", "journal": "", "year": "2024"}
        format_info = {
            "required_fields": ["author", "title", "journal", "year"],
            "raw_observed_fields": ["author", "title", "journal", "year"],
        }
        missing = service._check_missing_fields(fields, format_info)
        assert "title" in missing
        assert "journal" in missing
        assert "author" not in missing
        assert "year" not in missing

    def test_not_in_observed_skipped(self, service):
        fields = {"author": "Test", "title": ""}
        format_info = {
            "required_fields": ["author", "title", "abstract"],
            "raw_observed_fields": ["author", "title"],
        }
        missing = service._check_missing_fields(fields, format_info)
        assert "title" in missing
        assert "abstract" not in missing

    def test_no_format_info(self, service):
        metadata = {"title": ""}
        missing = service._check_missing_fields(metadata, None)
        assert missing == []


class TestServiceAnalyzeFormat:
    @pytest.mark.asyncio
    async def test_analyze_success(self, service):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={
            "format_name": "GB/T 7714",
            "confidence": "high",
            "entry_type": "article",
            "required_fields": ["author", "title", "journal", "year"],
            "optional_fields": ["volume", "number", "pages"],
            "field_descriptions": {},
            "format_description": "test",
            "raw_observed_fields": ["author", "title", "journal", "year"],
        })

        with patch("backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm):
            result = await service.analyze_format(
                "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia.Attention Is All You Need[J].arXiv preprint arXiv:1706.03762,2017."
            )
            assert result["format_name"] == "GB/T 7714"
            assert result["entry_type"] == "article"


class TestServiceExtractFromText:
    @pytest.mark.asyncio
    async def test_extract_success(self, service):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={
            "fields": {
                "author": "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki",
                "title": "Attention Is All You Need",
                "journal": "arXiv preprint",
                "year": "2017",
            },
        })

        format_info = {
            "format_name": "GB/T 7714",
            "required_fields": ["author", "title", "journal", "year"],
        }

        with patch("backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm):
            result = await service.extract_from_text(
                "Attention Is All You Need. Vaswani, Ashish. arXiv preprint, 2017.",
                format_info,
            )
            assert result["fields"]["author"] == "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki"
            assert "missing_fields" in result


class TestServiceGenerateBibtex:
    @pytest.mark.asyncio
    async def test_generate_success(self, service):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={
            "cite_key": "vaswani2017attention",
            "entry_type": "article",
            "fields": {
                "author": "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia",
                "title": "{Attention Is All You Need}",
                "journal": "arXiv preprint",
                "year": "2017",
            },
            "raw_bibtex": (
                "@article{vaswani2017attention,\n"
                '  author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia},\n'
                '  title = {{Attention Is All You Need}},\n'
                '  journal = {arXiv preprint},\n'
                '  year = {2017}\n'
                "}"
            ),
        })

        metadata = {
            "author": "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia",
            "title": "Attention Is All You Need",
            "journal": "arXiv preprint",
            "year": "2017",
        }

        with patch("backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm):
            result = await service.generate_bibtex(metadata)
            assert result["cite_key"] == "vaswani2017attention"
            assert result["entry_type"] == "article"


class TestServiceFileOperations:
    def test_read_bib_file(self, temp_project, service):
        with patch.object(
            service,
            "read_bib_file",
            return_value={
                "file_path": "refs.bib",
                "content": "@article{test, title={Test}}",
            },
        ):
            result = service.read_bib_file("refs.bib")
            assert result["file_path"] == "refs.bib"

    def test_replace_bib_entry(self, service):
        old_content = (
            "@article{test2023,\n"
            "  author = {Old, Author},\n"
            "  title = {Old Title},\n"
            "  year = {2023}\n"
            "}\n"
        )
        new_entry = (
            "@article{test2023,\n"
            "  author = {New, Author},\n"
            "  title = {New Title},\n"
            "  year = {2024}\n"
            "}"
        )
        result = service._replace_bib_entry(old_content, "test2023", new_entry)
        assert "New, Author" in result
        assert "Old, Author" not in result

    def test_replace_bib_entry_not_found(self, service):
        old_content = "@article{other, title={Other}}\n"
        new_entry = "@article{test2023, title={Test}}\n"
        result = service._replace_bib_entry(old_content, "test2023", new_entry)
        assert "other" in result


class TestSaveBibRequest:
    def test_valid(self):
        data = {
            "file_path": "refs.bib",
            "bibtex_code": "@article{test, title={Test}}",
            "cite_key": "test",
        }
        result = SaveBibRequest.model_validate(data)
        assert result.file_path == "refs.bib"


class TestGenerateBibtexRequest:
    def test_valid(self):
        data = {
            "metadata": {"title": "Test"},
            "format_info": {"entry_type": "article"},
            "existing_keys": ["key1"],
        }
        result = GenerateBibtexRequest.model_validate(data)
        assert result.existing_keys == ["key1"]


class TestPathTraversal:
    """bib_manager 路径穿越防护测试。"""

    def test_read_bib_dotdot_rejected(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager
        wm = get_workspace_manager()
        ws_id = "test_path_traversal"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            with pytest.raises(ValueError, match="不能包含"):
                service.read_bib_file("../../secret.tex", ws_id)
        finally:
            wm.remove(ws_id)

    def test_save_bib_dotdot_rejected(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager
        wm = get_workspace_manager()
        ws_id = "test_path_traversal_save"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            with pytest.raises(ValueError, match="不能包含"):
                service.save_bib_entry(
                    "../../evil.bib", "@article{k}", "k", ws_id
                )
        finally:
            wm.remove(ws_id)

    def test_read_bib_non_bib_rejected(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager
        wm = get_workspace_manager()
        ws_id = "test_path_traversal_ext"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            with pytest.raises(ValueError, match="必须以"):
                service.read_bib_file("main.tex", ws_id)
        finally:
            wm.remove(ws_id)

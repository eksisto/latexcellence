"""
bib_manager 模块集成测试。

覆盖内容：
    - Pydantic 模型校验
    - analyze_format（mock LLM）
    - extract_from_text（mock LLM）
    - generate_bibtex（mock LLM）
    - read_bib_file / save_bib_entry（临时项目）
    - .bib 写回乐观锁冲突检测（读取后文件被改动）
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
    DoiLookupRequest,
    DoiLookupResult,
    ExtractedMetadata,
    GenerateBibtexRequest,
    GenerateLatexRequest,
    ReferenceFormatResult,
    SaveBibRequest,
    SavedFormatInfo,
)
from backend.modules.bib_manager.service import BibManagerService
from backend.modules.bib_manager.style_registry import (
    get_style_for_format,
    list_all_styles,
)
from backend.modules.bib_manager.bib_parser import (
    parse_bib_file,
    remove_entry_from_content,
    normalize_title,
    normalize_doi,
    BibParserError,
)
from backend.modules.bib_manager.providers.base import (
    clean_doi,
    is_arxiv_doi,
    extract_arxiv_id,
)
from backend.modules.bib_manager.providers.crossref import CrossrefProvider
from backend.modules.bib_manager.providers.arxiv import ArxivProvider
from backend.core.config import get_config
from backend.utils.file_ops import safe_read


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
        assert result.raw_observed_fields == [
            "author",
            "title",
            "journal",
            "year",
            "doi",
        ]

    def test_empty_lists_default(self):
        data = {"format_name": "APA", "entry_type": "article"}
        result = ReferenceFormatResult.model_validate(data)
        assert result.required_fields == []
        assert result.optional_fields == []

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ReferenceFormatResult.model_validate({})


class TestSavedFormatInfo:
    """用户保存/自定义格式配置的字段校验。"""

    def test_valid_custom_style(self):
        data = {
            "format_name": "My Style",
            "entry_type": "article",
            "latex_backend": "biblatex",
            "latex_package": "biblatex",
            "latex_preamble": (
                r"\usepackage[style=custom]{biblatex}"
                "\n" + r"\addbibresource{references.bib}"
            ),
        }
        result = SavedFormatInfo.model_validate(data)
        assert result.latex_backend == "biblatex"
        assert result.latex_package == "biblatex"

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValidationError, match="latex_backend"):
            SavedFormatInfo.model_validate(
                {"format_name": "X", "latex_backend": "natbib"}
            )

    def test_package_with_space_rejected(self):
        with pytest.raises(ValidationError, match="single identifier"):
            SavedFormatInfo.model_validate(
                {"format_name": "X", "latex_package": "biblatex chicago"}
            )

    def test_unbalanced_preamble_rejected(self):
        with pytest.raises(ValidationError, match="latex_preamble"):
            SavedFormatInfo.model_validate(
                {"format_name": "X", "latex_preamble": r"\usepackage{foo"}
            )


class TestStyleRegistry:
    """样式注册表覆盖与数据完整性。"""

    def test_new_formats_resolved(self):
        expected = {
            "Harvard": "bibtex",
            "Nature": "bibtex",
            "AMA": "bibtex",
            "ACS": "biblatex",
            "OSCOLA": "biblatex",
            "Turabian": "biblatex",
            "ACM": "bibtex",
            "APS": "bibtex",
        }
        for name, backend in expected.items():
            info = get_style_for_format(name)
            assert info is not None, f"{name} 未命中注册表"
            assert info.backend == backend

    def test_aliases_resolve(self):
        assert get_style_for_format("哈佛").package == "natbib"
        assert get_style_for_format("美国化学会").backend == "biblatex"
        assert (
            get_style_for_format("american physical society").bibliographystyle
            == "apsrev4-2"
        )

    def test_registry_entries_are_complete(self):
        for name, info in list_all_styles().items():
            assert info["backend"] in ("bibtex", "biblatex")
            assert (
                info["bibliographystyle"] or info["preamble"]
            ), f"{name} 缺少样式名和导言区配置"


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
                "  author = {Devlin, Jacob and Chang, Ming-Wei},\n"
                "  title = {{BERT: Pre-training of Deep Bidirectional Transformers}},\n"
                "  year = {2018}\n"
                "}"
            ),
        }
        result = BibEntry.model_validate(data)
        assert result.cite_key == "devlin2018bert"
        assert result.fields["author"] == "Devlin, Jacob and Chang, Ming-Wei"

    def test_rejects_control_char_in_fields_value(self):
        """\\url 经 JSON 转义损坏后变成制表符 + 'url'，fields 值必须被拦截。"""
        data = {
            "cite_key": "k",
            "entry_type": "misc",
            "fields": {"url": "\turl{https://example.com}"},
            "raw_bibtex": "@misc{k, url = {https://example.com}}",
        }
        with pytest.raises(ValidationError, match="control character"):
            BibEntry.model_validate(data)

    def test_rejects_control_char_in_raw_bibtex(self):
        data = {
            "cite_key": "k",
            "entry_type": "misc",
            "fields": {},
            "raw_bibtex": "R^" + "\r" + "ho",
        }
        with pytest.raises(ValidationError, match="control character"):
            BibEntry.model_validate(data)

    def test_rejects_unbalanced_braces_in_fields_value(self):
        data = {
            "cite_key": "k",
            "entry_type": "misc",
            "fields": {"title": "{Unbalanced"},
            "raw_bibtex": "@misc{k, title = {{Unbalanced}}",
        }
        with pytest.raises(ValidationError, match="unbalanced braces"):
            BibEntry.model_validate(data)

    def test_accepts_percent_in_fields_value(self):
        """BibTeX 字段值中的 % 是字面字符（percent-encoding URL），应通过。"""
        url = "https://doi.org/10.1000/xyz%2Fabc"
        data = {
            "cite_key": "k",
            "entry_type": "misc",
            "fields": {"url": url},
            "raw_bibtex": f"@misc{{k, url = {{{url}}}}}",
        }
        result = BibEntry.model_validate(data)
        assert result.fields["url"] == url


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
        mock_llm.chat = AsyncMock(
            return_value={
                "format_name": "GB/T 7714",
                "confidence": "high",
                "entry_type": "article",
                "required_fields": ["author", "title", "journal", "year"],
                "optional_fields": ["volume", "number", "pages"],
                "field_descriptions": {},
                "format_description": "test",
                "raw_observed_fields": ["author", "title", "journal", "year"],
            }
        )

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.analyze_format("Example reference.")
            assert result["format_name"] == "GB/T 7714"
            assert result["entry_type"] == "article"
            assert result["latex_backend"] == "bibtex"
            assert result["latex_package"] == "gbt7714"
            assert result["latex_bibliographystyle"] == "gbt7714-numerical"
            assert result["latex_preamble"] == r"\usepackage{gbt7714}"

    @pytest.mark.asyncio
    async def test_rule_hit_skips_llm(self, service):
        """规则命中时直接返回确定性结果，不调用 LLM。"""
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            side_effect=AssertionError("LLM should not be called on rule hit")
        )
        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.analyze_format(
                "张三, 李四. 题名[J]. 刊名, 2023, 46(1): 1-15."
            )
        assert result["format_name"] == "GB/T 7714-2015"
        assert result["confidence"] == "high"
        assert result["entry_type"] == "article"
        assert result["latex_package"] == "gbt7714"
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_rule_miss_falls_back_to_llm(self, service):
        """规则未命中时走 LLM 兜底。"""
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value={
                "format_name": "APA 7th",
                "confidence": "high",
                "entry_type": "article",
                "required_fields": ["author", "title"],
                "optional_fields": [],
                "field_descriptions": {},
                "format_description": "test",
                "raw_observed_fields": ["author", "title"],
            }
        )
        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.analyze_format(
                "Some unusual reference without any known format markers."
            )
        assert result["format_name"] == "APA 7th"
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_registry_fills_style_fields(self, service):
        """格式名命中注册表时，latex_* 字段由注册表填充（LLM 输出不含样式字段）。"""
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value={
                "format_name": "APA 7th",
                "confidence": "high",
                "entry_type": "article",
                "required_fields": ["author", "title"],
                "optional_fields": [],
                "field_descriptions": {},
                "format_description": "test",
                "raw_observed_fields": ["author", "title"],
            }
        )

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.analyze_format("Example reference.")

        assert result["format_name"] == "APA 7th"
        assert result["latex_backend"] == "biblatex"
        assert result["latex_package"] == "biblatex"
        assert result["latex_bibliographystyle"] is None
        assert result["latex_preamble"] == (
            r"\usepackage[backend=biber,style=apa]{biblatex}"
            "\n" + r"\addbibresource{references.bib}"
        )

    @pytest.mark.asyncio
    async def test_analyze_unknown_format_style_fields_none(self, service):
        """格式名未命中注册表时，latex_* 字段全部为 None。"""
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value={
                "format_name": "unknown",
                "confidence": "low",
                "entry_type": "misc",
                "required_fields": [],
                "optional_fields": [],
                "field_descriptions": {},
                "format_description": "test",
                "raw_observed_fields": [],
            }
        )

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.analyze_format("Example reference.")

        assert result["latex_backend"] is None
        assert result["latex_package"] is None
        assert result["latex_bibliographystyle"] is None
        assert result["latex_preamble"] is None


class TestServiceExtractFromText:
    @pytest.mark.asyncio
    async def test_extract_success(self, service):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value={
                "fields": {
                    "author": "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki",
                    "title": "Attention Is All You Need",
                    "journal": "arXiv preprint",
                    "year": "2017",
                },
            }
        )

        format_info = {
            "format_name": "GB/T 7714",
            "required_fields": ["author", "title", "journal", "year"],
        }

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.extract_from_text(
                "Attention Is All You Need. Vaswani, Ashish. arXiv preprint, 2017.",
                format_info,
            )
            assert (
                result["fields"]["author"]
                == "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki"
            )
            assert "missing_fields" in result


class TestServiceGenerateBibtex:
    @pytest.mark.asyncio
    async def test_generate_success(self, service):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value={
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
                    "  author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia},\n"
                    "  title = {{Attention Is All You Need}},\n"
                    "  journal = {arXiv preprint},\n"
                    "  year = {2017}\n"
                    "}"
                ),
            }
        )

        metadata = {
            "author": "Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia",
            "title": "Attention Is All You Need",
            "journal": "arXiv preprint",
            "year": "2017",
        }

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.generate_bibtex(metadata)
            assert result["cite_key"] == "vaswani2017attention"
            assert result["entry_type"] == "article"

    @pytest.mark.asyncio
    async def test_generate_unescaped_backslash_retries(self, service):
        """未转义的 \\rho（JSON 解析后变成回车 + ho）→ 触发重试并最终恢复。"""
        mock_llm = AsyncMock()
        corrupt = {
            "cite_key": "k2024test",
            "entry_type": "misc",
            "fields": {"note": "R^" + "\r" + "ho"},
            "raw_bibtex": "@misc{k2024test,\n  note = {R^" + "\r" + "ho}\n}",
        }
        good = {
            "cite_key": "k2024test",
            "entry_type": "misc",
            "fields": {"note": r"R^{\rho}"},
            "raw_bibtex": "@misc{k2024test,\n  note = {R^\\rho}\n}",
        }
        mock_llm.chat = AsyncMock(side_effect=[corrupt, good])

        with patch(
            "backend.modules.bib_manager.service.get_llm_client", return_value=mock_llm
        ):
            result = await service.generate_bibtex({"title": "T"})

        assert result["cite_key"] == "k2024test"
        assert mock_llm.chat.call_count == 2
        assert r"\rho" in result["raw_bibtex"]
        assert "\r" not in result["raw_bibtex"]


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


class TestServiceFormatSaveLoad:
    """格式配置保存/加载：字段校验、注册表补全与文件名安全。"""

    def _use_tmp_formats_dir(self, tmp_path, monkeypatch):
        fmt_dir = tmp_path / "formats"
        monkeypatch.setattr(
            get_config().features.bib_manager, "format_configs_dir", str(fmt_dir)
        )
        return fmt_dir

    def test_save_valid_format(self, service, tmp_path, monkeypatch):
        fmt_dir = self._use_tmp_formats_dir(tmp_path, monkeypatch)
        result = service.save_format(
            "My Style",
            {
                "format_name": "My Style",
                "entry_type": "article",
                "latex_backend": "bibtex",
                "latex_package": "natbib",
                "latex_bibliographystyle": "plainnat",
                "latex_preamble": r"\usepackage{natbib}",
            },
        )
        assert result["name"] == "MyStyle"
        assert (fmt_dir / "MyStyle.json").exists()

    def test_save_rejects_invalid_backend(self, service, tmp_path, monkeypatch):
        self._use_tmp_formats_dir(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="latex_backend"):
            service.save_format(
                "Bad",
                {"format_name": "Bad", "latex_backend": "natbib"},
            )

    def test_save_rejects_unbalanced_preamble(self, service, tmp_path, monkeypatch):
        self._use_tmp_formats_dir(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="latex_preamble"):
            service.save_format(
                "Bad",
                {"format_name": "Bad", "latex_preamble": r"\usepackage{foo"},
            )

    def test_load_enriches_style_from_registry(self, service, tmp_path, monkeypatch):
        self._use_tmp_formats_dir(tmp_path, monkeypatch)
        service.save_format(
            "APA7th",
            {"format_name": "APA 7th", "entry_type": "article"},
        )
        data = service.load_format("APA7th")
        assert data["latex_backend"] == "biblatex"
        assert data["latex_preamble"]

    def test_load_and_delete_reject_traversal_name(
        self, service, tmp_path, monkeypatch
    ):
        self._use_tmp_formats_dir(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="Invalid format name"):
            service.load_format("../secret")
        with pytest.raises(ValueError, match="Invalid format name"):
            service.delete_format("../secret")

    def test_delete_missing_format_returns_false(self, service, tmp_path, monkeypatch):
        self._use_tmp_formats_dir(tmp_path, monkeypatch)
        assert service.delete_format("missing") is False


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


class TestGenerateLatexRequest:
    def test_language_default(self):
        result = GenerateLatexRequest.model_validate({"metadata": {"cite_key": "test"}})
        assert result.language == "English"

    def test_language_passed(self):
        result = GenerateLatexRequest.model_validate(
            {"metadata": {"cite_key": "test"}, "language": "Chinese"}
        )
        assert result.language == "Chinese"


class TestServiceGenerateLatexCode:
    """LaTeX 参考文献代码生成测试（注释跟随 UI 语言）。"""

    BIBTEX_FORMAT = {
        "format_name": "IEEE",
        "latex_backend": "bibtex",
        "latex_bibliographystyle": "IEEEtran",
        "latex_preamble": r"\usepackage{IEEEtran}",
    }

    BIBLATEX_FORMAT = {
        "format_name": "APA 7th",
        "latex_backend": "biblatex",
        "latex_preamble": r"\usepackage[style=apa]{biblatex}",
    }

    def test_bibtex_workflow_english_default(self, service):
        result = service.generate_latex_code(
            {"cite_key": "devlin2018bert"},
            self.BIBTEX_FORMAT,
            "references",
        )
        assert (
            "% Preamble package configuration to add in the preamble"
            in result["full_code"]
        )
        assert r"% Citation example: \cite{devlin2018bert}" in result["full_code"]
        assert r"\bibliographystyle{IEEEtran}" in result["full_code"]

    def test_bibtex_workflow_chinese(self, service):
        result = service.generate_latex_code(
            {"cite_key": "devlin2018bert"},
            self.BIBTEX_FORMAT,
            "references",
            language="Chinese",
        )
        assert "以下宏包配置需要添加到导言区" in result["full_code"]
        assert (
            r"在 \begin{document} 之后、\end{document} 之前添加以下内容："
            in result["full_code"]
        )
        assert r"引用示例：\cite{devlin2018bert}" in result["full_code"]

    def test_biblatex_workflow_chinese(self, service):
        result = service.generate_latex_code(
            {"cite_key": "vaswani2017attention"},
            self.BIBLATEX_FORMAT,
            "references",
            language="zh",
        )
        assert "在文档末尾使用以下命令生成参考文献列表" in result["full_code"]
        assert r"\printbibliography" in result["full_code"]
        assert r"引用示例：\cite{vaswani2017attention}" in result["full_code"]


class TestPathTraversal:
    """bib_manager 路径穿越防护测试。"""

    def test_read_bib_dotdot_rejected(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        ws_id = "test_path_traversal"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            with pytest.raises(ValueError, match="must not contain"):
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
            with pytest.raises(ValueError, match="must not contain"):
                service.save_bib_entry("../../evil.bib", "@article{k}", "k", ws_id)
        finally:
            wm.remove(ws_id)

    def test_read_bib_non_bib_rejected(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        ws_id = "test_path_traversal_ext"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            with pytest.raises(ValueError, match="must end with"):
                service.read_bib_file("main.tex", ws_id)
        finally:
            wm.remove(ws_id)


class TestDoiLookupResult:
    def test_valid(self):
        data = {
            "title": "Attention Is All You Need",
            "authors": ["Vaswani, Ashish", "Shazeer, Noam"],
            "year": "2017",
            "journal": "NIPS",
            "doi": "10.48550/arXiv.1706.03762",
            "data_source": "arxiv",
        }
        result = DoiLookupResult.model_validate(data)
        assert result.title == "Attention Is All You Need"
        assert len(result.authors) == 2
        assert result.year == "2017"
        assert result.data_source == "arxiv"

    def test_empty_defaults(self):
        result = DoiLookupResult.model_validate({})
        assert result.title == ""
        assert result.authors == []
        assert result.year == ""
        assert result.data_source == ""


class TestDoiLookupRequest:
    def test_valid(self):
        data = {"doi": "10.1000/xyz123"}
        result = DoiLookupRequest.model_validate(data)
        assert result.doi == "10.1000/xyz123"


class TestDoiUtils:
    def test_clean_doi_url_prefix(self):
        assert clean_doi("https://doi.org/10.1000/test") == "10.1000/test"

    def test_clean_doi_http_prefix(self):
        assert clean_doi("http://doi.org/10.1000/test") == "10.1000/test"

    def test_clean_doi_doi_prefix(self):
        assert clean_doi("doi:10.1000/test") == "10.1000/test"

    def test_clean_doi_no_prefix(self):
        assert clean_doi("10.1000/test") == "10.1000/test"

    def test_is_arxiv_doi_true(self):
        assert is_arxiv_doi("10.48550/arXiv.1706.03762") is True

    def test_is_arxiv_doi_false(self):
        assert is_arxiv_doi("10.1000/xyz123") is False

    def test_extract_arxiv_id(self):
        assert extract_arxiv_id("10.48550/arXiv.1706.03762") == "1706.03762"

    def test_extract_arxiv_id_non_arxiv(self):
        assert extract_arxiv_id("10.1000/xyz123") == ""


class TestServiceDoiLookup:
    """通过 mock ProviderManager 测试 service.lookup_doi。"""

    def _make_doi_result(self, **kwargs):
        default = {
            "title": "Test Paper",
            "authors": ["Test, Author"],
            "year": "2023",
            "journal": "Test Journal",
            "doi": "10.1000/test",
            "data_source": "crossref",
        }
        default.update(kwargs)
        return default

    @pytest.mark.asyncio
    async def test_lookup_success(self, service):
        mock_result = self._make_doi_result()
        mock_manager = MagicMock()
        mock_manager.lookup_doi = AsyncMock(
            return_value=DoiLookupResult.model_validate(mock_result)
        )

        with patch(
            "backend.modules.bib_manager.service.get_provider_manager",
            return_value=mock_manager,
        ):
            result = await service.lookup_doi("10.1000/test")
            assert result["title"] == "Test Paper"
            assert result["data_source"] == "crossref"

    @pytest.mark.asyncio
    async def test_lookup_not_found(self, service):
        mock_manager = MagicMock()
        mock_manager.lookup_doi = AsyncMock(
            side_effect=ValueError("All providers failed for DOI: 10.9999/nonexistent")
        )

        with patch(
            "backend.modules.bib_manager.service.get_provider_manager",
            return_value=mock_manager,
        ):
            with pytest.raises(ValueError, match="All providers failed"):
                await service.lookup_doi("10.9999/nonexistent")

    @pytest.mark.asyncio
    async def test_lookup_empty_doi(self, service):
        mock_manager = MagicMock()
        mock_manager.lookup_doi = AsyncMock(side_effect=ValueError("Invalid DOI: "))

        with patch(
            "backend.modules.bib_manager.service.get_provider_manager",
            return_value=mock_manager,
        ):
            with pytest.raises(ValueError, match="Invalid DOI"):
                await service.lookup_doi("")


class TestCrossrefProvider:
    @pytest.mark.asyncio
    async def test_lookup_success(self):
        provider = CrossrefProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={
                "message": {
                    "title": ["Test Title"],
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                    ],
                    "issued": {"date-parts": [[2024]]},
                    "container-title": ["Test Journal"],
                    "volume": "1",
                    "page": "10-20",
                    "publisher": "Test Publisher",
                    "abstract": "<p>Test Abstract</p>",
                    "subject": ["testing"],
                }
            }
        )

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.lookup_by_doi("10.1000/test")
            assert result is not None
            assert result.title == "Test Title"
            assert result.authors == ["Smith Alice"]
            assert result.year == "2024"
            assert result.data_source == "crossref"

    @pytest.mark.asyncio
    async def test_lookup_404_returns_none(self):
        provider = CrossrefProvider()
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )
        mock_response.raise_for_status = MagicMock(side_effect=error)

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.lookup_by_doi("10.9999/notfound")
            assert result is None


class TestArxivProvider:
    @pytest.mark.asyncio
    async def test_lookup_success(self):
        provider = ArxivProvider()

        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>Test Preprint Title</title>
    <author><name>John Doe</name></author>
    <author><name>Jane Roe</name></author>
    <published>2025-06-15</published>
    <summary>This is a test abstract.</summary>
    <arxiv:doi>10.48550/arXiv.2506.12345</arxiv:doi>
    <category term="cs.AI"/>
    <category term="cs.CL"/>
  </entry>
</feed>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = xml_response

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.lookup_by_doi("10.48550/arXiv.2506.12345")
            assert result is not None
            assert result.title == "Test Preprint Title"
            assert result.authors == ["John Doe", "Jane Roe"]
            assert result.year == "2025"
            assert result.data_source == "arxiv"
            assert result.journal == "arXiv preprint"
            assert len(result.keywords) == 2

    @pytest.mark.asyncio
    async def test_non_arxiv_doi_returns_none(self):
        provider = ArxivProvider()
        result = await provider.lookup_by_doi("10.1000/xyz123")
        assert result is None


class TestServiceGenerateBibtexFromDoi:
    @pytest.mark.asyncio
    async def test_generate_from_doi_success(self, service):
        mock_lookup_result = {
            "title": "Test Paper",
            "authors": ["Author, Test"],
            "year": "2023",
            "journal": "Test Journal",
            "doi": "10.1000/test",
            "data_source": "crossref",
        }

        mock_bibtex_result = {
            "cite_key": "author2023test",
            "entry_type": "article",
            "fields": {"author": "Author, Test", "title": "{Test Paper}"},
            "raw_bibtex": "@article{author2023test, ...}",
        }

        with patch.object(
            service, "lookup_doi", return_value=mock_lookup_result
        ), patch.object(service, "generate_bibtex", return_value=mock_bibtex_result):
            result = await service.generate_bibtex_from_doi("10.1000/test")
            assert result["cite_key"] == "author2023test"


class TestDoiLookupRequestValidation:
    def test_empty_doi(self):
        data = {"doi": ""}
        result = DoiLookupRequest.model_validate(data)
        assert result.doi == ""


class TestGenerateBibtexRequestWithDoi:
    def test_valid_with_doi(self):
        data = {
            "doi": "10.1000/xyz123",
            "format_info": {"entry_type": "article"},
            "existing_keys": [],
        }
        result = GenerateBibtexRequest.model_validate(data)
        assert result.doi == "10.1000/xyz123"
        assert result.metadata == {}

    def test_valid_without_doi(self):
        data = {"metadata": {"title": "Test"}}
        result = GenerateBibtexRequest.model_validate(data)
        assert result.doi is None
        assert result.metadata == {"title": "Test"}


class TestProviderManager:
    """ProviderManager 集成测试。"""

    @pytest.mark.asyncio
    async def test_lookup_normal_crossref_success(self):
        from backend.modules.bib_manager.provider_manager import ProviderManager

        mgr = ProviderManager()
        mock_provider = MagicMock()
        mock_provider.name = "crossref"
        result = DoiLookupResult(
            title="Test", doi="10.1000/test", data_source="crossref"
        )
        mock_provider.lookup_by_doi = AsyncMock(return_value=result)
        mgr.register_primary(mock_provider)

        r = await mgr.lookup_doi("10.1000/test")
        assert r.title == "Test"
        assert r.data_source == "crossref"

    @pytest.mark.asyncio
    async def test_lookup_fallback_on_primary_failure(self):
        from backend.modules.bib_manager.provider_manager import ProviderManager

        mgr = ProviderManager()

        primary = MagicMock()
        primary.name = "crossref"
        primary.lookup_by_doi = AsyncMock(return_value=None)
        mgr.register_primary(primary)

        fallback = MagicMock()
        fallback.name = "datacite"
        result = DoiLookupResult(
            title="Fallback Found", doi="10.1000/test", data_source="datacite"
        )
        fallback.lookup_by_doi = AsyncMock(return_value=result)
        mgr.register_fallback(fallback)

        r = await mgr.lookup_doi("10.1000/test")
        assert r.title == "Fallback Found"

    @pytest.mark.asyncio
    async def test_lookup_all_providers_fail(self):
        from backend.modules.bib_manager.provider_manager import ProviderManager

        mgr = ProviderManager()

        primary = MagicMock()
        primary.name = "crossref"
        primary.lookup_by_doi = AsyncMock(return_value=None)
        mgr.register_primary(primary)

        with pytest.raises(ValueError, match="All providers failed"):
            await mgr.lookup_doi("10.1000/test")

    @pytest.mark.asyncio
    async def test_lookup_arxiv_routes_correctly(self):
        from backend.modules.bib_manager.provider_manager import ProviderManager

        mgr = ProviderManager()

        arxiv = MagicMock()
        arxiv.name = "arxiv"
        result = DoiLookupResult(
            title="ArXiv Paper", doi="10.48550/arXiv.2501.00001", data_source="arxiv"
        )
        arxiv.lookup_by_doi = AsyncMock(return_value=result)
        mgr.register_arxiv(arxiv)

        r = await mgr.lookup_doi("10.48550/arXiv.2501.00001")
        assert r.title == "ArXiv Paper"
        assert r.data_source == "arxiv"


# ── 文献库管理测试 ──────────────────────────────────────────────────────────


class TestBibParser:
    def test_single_entry(self):
        content = """@article{test2023,
  author = {Test, Author},
  title = {A Test Article},
  journal = {Journal of Testing},
  year = {2023}
}"""
        entries = parse_bib_file(content)
        assert len(entries) == 1
        assert entries[0]["cite_key"] == "test2023"
        assert entries[0]["entry_type"] == "article"
        assert entries[0]["fields"]["author"] == "Test, Author"
        assert entries[0]["fields"]["title"] == "A Test Article"
        assert entries[0]["fields"]["journal"] == "Journal of Testing"
        assert entries[0]["fields"]["year"] == "2023"

    def test_multiple_entries(self):
        content = """@article{key1,
  title = {First},
  year = {2020}
}

@inproceedings{key2,
  title = {Second},
  booktitle = {Some Conference},
  year = {2021}
}

@book{key3,
  title = {Third},
  publisher = {Press},
  year = {2022}
}"""
        entries = parse_bib_file(content)
        assert len(entries) == 3
        assert entries[0]["cite_key"] == "key1"
        assert entries[1]["cite_key"] == "key2"
        assert entries[2]["cite_key"] == "key3"

    def test_nested_braces_in_field(self):
        content = """@article{nested,
  title = {{BERT: Pre-training of Deep Bidirectional Transformers}},
  year = {2018}
}"""
        entries = parse_bib_file(content)
        assert len(entries) == 1
        assert (
            entries[0]["fields"]["title"]
            == "{BERT: Pre-training of Deep Bidirectional Transformers}"
        )

    def test_empty_file(self):
        entries = parse_bib_file("")
        assert len(entries) == 0

    def test_remove_entry(self):
        content = """@article{keep,
  title = {Keep Me},
  year = {2020}
}

@article{remove,
  title = {Remove Me},
  year = {2021}
}

@article{keep2,
  title = {Keep Me Too},
  year = {2022}
}"""
        result = remove_entry_from_content(content, "remove")
        assert "Remove Me" not in result
        assert "Keep Me" in result
        assert "Keep Me Too" in result

    def test_remove_entry_not_found(self):
        content = "@article{exists, title={Test}}"
        with pytest.raises(BibParserError, match="not found"):
            remove_entry_from_content(content, "nonexistent")

    def test_normalize_title(self):
        assert (
            normalize_title("BERT: Pre-training of Deep Bidirectional Transformers")
            == "bert pretraining of deep bidirectional transformers"
        )
        assert (
            normalize_title(r"\textbf{Attention} Is All You Need")
            == "attention is all you need"
        )

    def test_normalize_doi(self):
        assert normalize_doi("10.1000/test") == "10.1000/test"
        assert normalize_doi("https://doi.org/10.1000/test") == "10.1000/test"
        assert normalize_doi("doi:10.1000/test") == "10.1000/test"


class TestServiceDuplicates:
    """重复检测测试。"""

    def test_doi_duplicate(self, service):
        entries = [
            {
                "cite_key": "a",
                "entry_type": "article",
                "fields": {"doi": "10.1000/test", "title": "Paper A", "year": "2020"},
                "_file_path": "refs.bib",
            },
            {
                "cite_key": "b",
                "entry_type": "article",
                "fields": {
                    "doi": "10.1000/test",
                    "title": "Paper A Dup",
                    "year": "2020",
                },
                "_file_path": "refs.bib",
            },
        ]
        groups = service._find_duplicates(entries)
        assert len(groups) == 1
        assert groups[0]["reason"] == "doi"
        assert groups[0]["confidence"] == "high"

    def test_title_duplicate(self, service):
        entries = [
            {
                "cite_key": "a",
                "entry_type": "article",
                "fields": {"title": "Attention Is All You Need", "year": "2017"},
                "_file_path": "refs.bib",
            },
            {
                "cite_key": "b",
                "entry_type": "article",
                "fields": {"title": "Attention Is All You Need.", "year": "2017"},
                "_file_path": "refs.bib",
            },
        ]
        groups = service._find_duplicates(entries)
        assert len(groups) >= 1
        g = next(g for g in groups if g["reason"] == "title")
        assert g["confidence"] in ("high", "medium")

    def test_cite_key_duplicate(self, service):
        entries = [
            {
                "cite_key": "same_key",
                "entry_type": "article",
                "fields": {"title": "Paper A", "year": "2020"},
                "_file_path": "refs.bib",
            },
            {
                "cite_key": "same_key",
                "entry_type": "article",
                "fields": {"title": "Paper B", "year": "2021"},
                "_file_path": "refs2.bib",
            },
        ]
        groups = service._find_duplicates(entries)
        assert len(groups) == 1
        assert groups[0]["reason"] == "cite_key"

    def test_no_duplicates(self, service):
        entries = [
            {
                "cite_key": "a",
                "entry_type": "article",
                "fields": {"doi": "10.1000/a", "title": "Paper A", "year": "2020"},
                "_file_path": "refs.bib",
            },
            {
                "cite_key": "b",
                "entry_type": "article",
                "fields": {"doi": "10.1000/b", "title": "Paper B", "year": "2021"},
                "_file_path": "refs.bib",
            },
        ]
        groups = service._find_duplicates(entries)
        assert len(groups) == 0


class TestServiceEntryStats:
    """条目统计测试。"""

    def test_stats(self, service):
        entries = [
            {
                "cite_key": "a",
                "entry_type": "article",
                "fields": {"doi": "10.1000/a", "title": "A", "year": "2020"},
            },
            {
                "cite_key": "b",
                "entry_type": "article",
                "fields": {"doi": "10.1000/b", "title": "B", "year": "2020"},
            },
            {
                "cite_key": "c",
                "entry_type": "inproceedings",
                "fields": {"title": "C", "year": "2021"},
            },
        ]
        with patch.object(
            service,
            "parse_entries",
            return_value={"file_path": "refs.bib", "entries": entries, "total": 3},
        ):
            result = service.get_entry_stats("refs.bib", "test_ws")
            assert result["total"] == 3
            assert result["by_type"]["article"] == 2
            assert result["by_type"]["inproceedings"] == 1
            assert result["by_year"]["2020"] == 2
            assert result["by_year"]["2021"] == 1
            assert result["with_doi"] == 2
            assert result["without_doi"] == 1


class TestServiceDeleteEntry:
    """删除条目测试。"""

    def test_delete_success(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        ws_id = "test_delete_entry"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            result = service.delete_entry("refs.bib", "test2023", ws_id)
            assert result["status"] == "ok"
            content = safe_read(temp_project / "refs.bib")
            assert "test2023" not in content
        finally:
            wm.remove(ws_id)


class TestServiceBibUndo:
    """.bib 写盘应推入撤销栈，workspace 撤销/重做可回退。"""

    def _open_project(self, temp_project, ws_id):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        return wm, pm

    def test_save_bib_entry_is_undoable(self, temp_project, service):
        ws_id = "test_save_bib_undo"
        wm, pm = self._open_project(temp_project, ws_id)
        try:
            original = safe_read(temp_project / "refs.bib")
            service.save_bib_entry(
                "refs.bib",
                "@article{key2024,\n  title = {New Entry},\n}",
                "key2024",
                ws_id,
            )
            assert "key2024" in safe_read(temp_project / "refs.bib")
            assert pm.can_undo("refs.bib")
            assert not pm.can_redo("refs.bib")

            assert pm.undo("refs.bib")
            assert safe_read(temp_project / "refs.bib") == original
            assert pm.can_redo("refs.bib")

            assert pm.redo("refs.bib")
            assert "key2024" in safe_read(temp_project / "refs.bib")
        finally:
            wm.remove(ws_id)

    def test_update_entry_is_undoable(self, temp_project, service):
        ws_id = "test_update_bib_undo"
        wm, pm = self._open_project(temp_project, ws_id)
        try:
            original = safe_read(temp_project / "refs.bib")
            service.update_entry(
                "refs.bib",
                "test2023",
                "@article{test2023,\n  title = {Updated Title},\n}",
                ws_id,
            )
            assert "Updated Title" in safe_read(temp_project / "refs.bib")

            assert pm.undo("refs.bib")
            assert safe_read(temp_project / "refs.bib") == original

            assert pm.redo("refs.bib")
            assert "Updated Title" in safe_read(temp_project / "refs.bib")
        finally:
            wm.remove(ws_id)

    def test_delete_entry_is_undoable(self, temp_project, service):
        ws_id = "test_delete_bib_undo"
        wm, pm = self._open_project(temp_project, ws_id)
        try:
            original = safe_read(temp_project / "refs.bib")
            service.delete_entry("refs.bib", "test2023", ws_id)
            assert "test2023" not in safe_read(temp_project / "refs.bib")

            assert pm.undo("refs.bib")
            assert safe_read(temp_project / "refs.bib") == original

            assert pm.redo("refs.bib")
            assert "test2023" not in safe_read(temp_project / "refs.bib")
        finally:
            wm.remove(ws_id)


class TestServiceBibOptimisticLock:
    """.bib 写回携带读取时 MD5，读后文件被改动时抛 FileConflictError。"""

    def _open_project(self, temp_project, ws_id):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        return wm, pm

    def _assert_conflict(self, temp_project, service, ws_id, operation):
        from backend.core.project_manager import FileConflictError
        from backend.utils.file_ops import compute_md5

        wm, _ = self._open_project(temp_project, ws_id)
        try:
            original = safe_read(temp_project / "refs.bib")
            real_compute_md5 = compute_md5

            def read_then_tamper(path):
                # 模拟“读取后、写回前”文件被其他工作区/进程修改
                md5 = real_compute_md5(path)
                (temp_project / "refs.bib").write_text(
                    original + "% concurrent edit\n", encoding="utf-8"
                )
                return md5

            with patch(
                "backend.modules.bib_manager.service.compute_md5",
                side_effect=read_then_tamper,
            ):
                with pytest.raises(FileConflictError):
                    operation(service, ws_id)
        finally:
            wm.remove(ws_id)

    def test_save_entry_conflict_detected(self, temp_project, service):
        self._assert_conflict(
            temp_project,
            service,
            "test_save_bib_conflict",
            lambda s, ws: s.save_bib_entry(
                "refs.bib",
                "@article{key2024,\n  title = {New Entry},\n}",
                "key2024",
                ws,
            ),
        )

    def test_update_entry_conflict_detected(self, temp_project, service):
        self._assert_conflict(
            temp_project,
            service,
            "test_update_bib_conflict",
            lambda s, ws: s.update_entry(
                "refs.bib",
                "test2023",
                "@article{test2023,\n  title = {Updated Title},\n}",
                ws,
            ),
        )

    def test_delete_entry_conflict_detected(self, temp_project, service):
        self._assert_conflict(
            temp_project,
            service,
            "test_delete_bib_conflict",
            lambda s, ws: s.delete_entry("refs.bib", "test2023", ws),
        )


class TestServiceParseEntries:
    """解析条目测试。"""

    def test_parse_success(self, temp_project, service):
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        ws_id = "test_parse_entries"
        pm = wm.get_or_create(ws_id)
        pm.open(str(temp_project))
        try:
            result = service.parse_entries("refs.bib", ws_id)
            assert result["total"] == 1
            assert result["entries"][0]["cite_key"] == "test2023"
        finally:
            wm.remove(ws_id)

"""
latex_package_analyzer 单元测试。

覆盖内容：
    - LatexPackageAnalysis Pydantic 模型校验
    - packages_to_comments 格式转换
    - analyze_latex_packages 正常流程（mock LLM）
    - analyze_latex_packages 空输入处理
    - analyze_latex_packages LLM 调用失败降级
    - prepend_package_comments 拼接与降级
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.utils.latex_package_analyzer import (
    LatexPackageAnalysis,
    analyze_latex_packages,
    packages_to_comments,
    prepend_package_comments,
)


class TestLatexPackageAnalysisModel:
    """LatexPackageAnalysis 模型校验测试。"""

    def test_valid_with_packages(self):
        data = {"packages": ["booktabs", "multirow"]}
        result = LatexPackageAnalysis.model_validate(data)
        assert result.packages == ["booktabs", "multirow"]

    def test_valid_empty(self):
        data = {"packages": []}
        result = LatexPackageAnalysis.model_validate(data)
        assert result.packages == []

    def test_missing_packages(self):
        with pytest.raises(ValidationError):
            LatexPackageAnalysis.model_validate({})

    def test_wrong_type(self):
        with pytest.raises(ValidationError):
            LatexPackageAnalysis.model_validate({"packages": "booktabs"})


class TestPackagesToComments:
    """packages_to_comments 格式转换测试。"""

    def test_single_package(self):
        result = packages_to_comments(["booktabs"])
        assert result == r"% \usepackage{booktabs}"

    def test_multiple_packages(self):
        result = packages_to_comments(["booktabs", "multirow", "makecell"])
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == r"% \usepackage{booktabs}"
        assert lines[1] == r"% \usepackage{multirow}"
        assert lines[2] == r"% \usepackage{makecell}"

    def test_empty_list(self):
        result = packages_to_comments([])
        assert result == ""

    def test_none(self):
        result = packages_to_comments(None)
        assert result == ""


class TestAnalyzeLatexPackages:
    """analyze_latex_packages 集成测试（Mock LLM）。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_detects_booktabs(self, mock_llm):
        latex_code = r"\begin{table}\begin{tabular}{cc}\toprule A & B \\\midrule 1 & 2 \\\bottomrule\end{tabular}\end{table}"
        mock_llm.chat.return_value = {"packages": ["booktabs"]}
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert packages == ["booktabs"]

    @pytest.mark.asyncio
    async def test_detects_multiple_packages(self, mock_llm):
        latex_code = (
            r"\begin{table}\begin{tabular}{cc}\toprule A & B \\\midrule"
            r"\multirow{2}{c}{C} & D \\\bottomrule\end{tabular}\end{table}"
        )
        mock_llm.chat.return_value = {"packages": ["booktabs", "multirow"]}
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert "booktabs" in packages
        assert "multirow" in packages

    @pytest.mark.asyncio
    async def test_no_packages_needed(self, mock_llm):
        latex_code = r"\begin{table}\begin{tabular}{cc}\hline A & B \\\hline 1 & 2 \\\hline\end{tabular}\end{table}"
        mock_llm.chat.return_value = {"packages": []}
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert packages == []

    @pytest.mark.asyncio
    async def test_empty_latex(self, mock_llm):
        packages = await analyze_latex_packages("", mock_llm)
        assert packages == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_latex(self, mock_llm):
        packages = await analyze_latex_packages("   \n\n  ", mock_llm)
        assert packages == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, mock_llm):
        latex_code = r"\toprule test"
        mock_llm.chat.side_effect = RuntimeError("API unavailable")
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert packages == []

    @pytest.mark.asyncio
    async def test_invalid_json_graceful_degradation(self, mock_llm):
        latex_code = r"\toprule test"
        mock_llm.chat.return_value = {"bad_field": True}
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert packages == []


class TestPrependPackageComments:
    """prepend_package_comments 集成测试（Mock LLM）。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_with_packages_detected(self, mock_llm):
        mock_llm.chat.return_value = {"packages": ["booktabs"]}
        result = await prepend_package_comments(mock_llm, r"\toprule A \\\bottomrule")
        assert result.startswith(r"% \usepackage{booktabs}")
        assert r"\toprule A \\\bottomrule" in result

    @pytest.mark.asyncio
    async def test_without_packages(self, mock_llm):
        mock_llm.chat.return_value = {"packages": []}
        latex = r"\hline A \hline"
        result = await prepend_package_comments(mock_llm, latex)
        assert result == latex

    @pytest.mark.asyncio
    async def test_llm_failure_returns_original(self, mock_llm):
        mock_llm.chat.side_effect = RuntimeError("API error")
        latex = r"\toprule test"
        result = await prepend_package_comments(mock_llm, latex)
        assert result == latex

    @pytest.mark.asyncio
    async def test_empty_latex_returns_original(self, mock_llm):
        result = await prepend_package_comments(mock_llm, "")
        assert result == ""
        mock_llm.chat.assert_not_called()

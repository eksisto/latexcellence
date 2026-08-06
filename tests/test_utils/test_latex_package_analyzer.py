"""
latex_package_analyzer 单元测试。

覆盖内容：
    - LatexPackageAnalysis Pydantic 模型校验
    - packages_to_comments 格式转换
    - detect_required_packages 确定性规则表（命令/环境/列说明符/注释/逐字屏蔽）
    - analyze_latex_packages 规则表优先 + LLM 兜底（mock LLM）
    - prepend_package_comments 拼接与降级
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.utils.latex_package_analyzer import (
    LatexPackageAnalysis,
    analyze_latex_packages,
    detect_required_packages,
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


class TestDetectRequiredPackages:
    """detect_required_packages 确定性规则引擎测试。"""

    def test_booktabs(self):
        latex_code = (
            r"\begin{tabular}{cc}\toprule A & B \\\midrule 1 & 2 \\"
            r"\bottomrule\end{tabular}"
        )
        assert detect_required_packages(latex_code) == ["booktabs"]

    def test_multiple_table_packages(self):
        latex_code = (
            r"\begin{tabular}{>{\bfseries}l p{3cm}}\makecell{A\\B} & "
            r"\multirow{2}{*}{text}\\\end{tabular}"
        )
        assert detect_required_packages(latex_code) == ["array", "makecell", "multirow"]

    def test_kernel_only_no_packages(self):
        latex_code = (
            r"\begin{tabular}{cc}\hline A & B \\\hline 1 & 2 \\\hline\end{tabular}"
        )
        assert detect_required_packages(latex_code) == []

    def test_plain_math_no_packages(self):
        # \frac / \sum / \int / \mathcal 均为内核能力，不应误报
        latex_code = (
            r"$\frac{a}{b} + \sum_{i=1}^n x_i + \int_0^1 f(x)\,dx + \mathcal{A}$"
        )
        assert detect_required_packages(latex_code) == []

    def test_siunitx_s_column(self):
        latex_code = r"\begin{tabular}{lS}\toprule A & B\\\midrule 1 & 2.5\\\bottomrule\end{tabular}"
        assert detect_required_packages(latex_code) == ["booktabs", "siunitx"]

    def test_siunitx_num_command(self):
        assert detect_required_packages(r"\num{1.23e4}") == ["siunitx"]

    def test_tabularx_x_column(self):
        latex_code = r"\begin{tabularx}{\textwidth}{X X} a & b \\\end{tabularx}"
        assert detect_required_packages(latex_code) == ["tabularx"]

    def test_x_native_in_nicetabular(self):
        latex_code = (
            r"\begin{NiceTabular}{XX}\toprule a & b\\\bottomrule\end{NiceTabular}"
        )
        assert detect_required_packages(latex_code) == ["booktabs", "nicematrix"]

    def test_nicetabularx_width_first_colspec(self):
        """NiceTabularX 与 tabularx 同构：宽度在前、列说明符在后，S 列应触发 siunitx。"""
        latex_code = r"\begin{NiceTabularX}{\textwidth}{lS} a & b \\\end{NiceTabularX}"
        assert detect_required_packages(latex_code) == ["nicematrix", "siunitx"]

    def test_dcolumn_d_column(self):
        latex_code = r"\begin{tabular}{D{.}{.}{-1}} 1.2 \\\end{tabular}"
        assert detect_required_packages(latex_code) == ["dcolumn"]

    def test_math_environments(self):
        latex_code = (
            r"\begin{align*}a &= b\\\end{align*} "
            r"$\mathbb{R}$ $\bm{x}$ $\begin{pmatrix}1&0\end{pmatrix}$"
        )
        assert detect_required_packages(latex_code) == ["amsmath", "amssymb", "bm"]

    def test_graphics_and_color(self):
        latex_code = (
            r"\includegraphics[width=3cm]{fig.png} "
            r"\textcolor{red}{text} \rotatebox{90}{x}"
        )
        assert detect_required_packages(latex_code) == ["graphicx", "xcolor"]

    def test_comments_ignored(self):
        latex_code = (
            "% \\toprule \\multirow{2}{*}{x}\n"
            r"\begin{tabular}{cc}\hline a & b \\\hline\end{tabular}"
        )
        assert detect_required_packages(latex_code) == []

    def test_inline_comment_ignored(self):
        latex_code = r"\begin{tabular}{cc}\hline a & b % \toprule\bottomrule"
        assert detect_required_packages(latex_code) == []

    def test_escaped_percent_not_comment(self):
        latex_code = r"100\% \toprule"
        assert detect_required_packages(latex_code) == ["booktabs"]

    def test_verb_content_ignored(self):
        latex_code = r"\verb|\toprule \multirow| plus \textcolor{red}{x}"
        assert detect_required_packages(latex_code) == ["xcolor"]

    def test_lstlisting_body_ignored_but_env_detected(self):
        latex_code = r"\begin{lstlisting}\toprule \multirow\end{lstlisting}"
        assert detect_required_packages(latex_code) == ["listings"]

    def test_inline_listing_command(self):
        latex_code = r"\lstinline|\toprule \multirow| plus \textcolor{red}{x}"
        assert detect_required_packages(latex_code) == ["listings", "xcolor"]

    def test_url_percent_not_comment(self):
        latex_code = r"\url{http://example.com/a%20b} \toprule"
        assert detect_required_packages(latex_code) == ["booktabs", "url"]

    def test_float_h_placement(self):
        latex_code = r"\begin{figure}[H]\centering content\end{figure}"
        assert detect_required_packages(latex_code) == ["float"]

    def test_dedup_and_sorted(self):
        latex_code = r"\toprule \midrule \bottomrule \toprule"
        assert detect_required_packages(latex_code) == ["booktabs"]

    def test_empty_input(self):
        assert detect_required_packages("") == []
        assert detect_required_packages("   \n\n  ") == []


class TestDomainParameter:
    """领域参数（general / math / table）行为测试。"""

    def test_math_excludes_table_packages(self):
        latex_code = r"\begin{array}{cc}\toprule a & b \\\bottomrule\end{array}"
        assert detect_required_packages(latex_code, domain="math") == []

    def test_math_keeps_math_packages(self):
        latex_code = r"$\dfrac{a}{b}$ and $\mathbb{R}$ and $\bm{x}$"
        assert detect_required_packages(latex_code, domain="math") == [
            "amsmath",
            "amssymb",
            "bm",
        ]

    def test_math_excludes_float_layout(self):
        latex_code = r"\begin{figure}[H]x\end{figure}"
        assert detect_required_packages(latex_code, domain="math") == []

    def test_table_keeps_table_packages(self):
        latex_code = r"\begin{tabular}{lS}\toprule A & 1.5\\\bottomrule\end{tabular}"
        assert detect_required_packages(latex_code, domain="table") == [
            "booktabs",
            "siunitx",
        ]

    def test_table_keeps_math_in_cells(self):
        latex_code = r"\begin{tabular}{cc}$\dfrac{1}{2}$ & x\end{tabular}"
        assert detect_required_packages(latex_code, domain="table") == ["amsmath"]

    def test_table_keeps_float_and_sideways(self):
        latex_code = (
            r"\begin{table}[H]x\end{table} "
            r"\begin{sidewaystable}x\end{sidewaystable}"
        )
        assert detect_required_packages(latex_code, domain="table") == [
            "float",
            "rotating",
        ]

    def test_table_excludes_citations(self):
        latex_code = r"\citep{x}"
        assert detect_required_packages(latex_code, domain="table") == []

    def test_general_keeps_everything(self):
        latex_code = (
            r"\toprule \citep{x} \dfrac{1}{2} "
            r"\begin{sidewaystable}x\end{sidewaystable}"
        )
        assert detect_required_packages(latex_code, domain="general") == [
            "amsmath",
            "booktabs",
            "natbib",
            "rotating",
        ]

    def test_invalid_domain_raises(self):
        with pytest.raises(ValueError, match="Unknown domain"):
            detect_required_packages(r"\toprule", domain="unknown")


class TestLoadedPackageExclusion:
    """排除已加载宏包（\\usepackage / \\RequirePackage）。"""

    def test_loaded_package_excluded(self):
        latex_code = (
            r"\usepackage{booktabs}"
            r"\begin{tabular}{cc}\toprule a & b \\\bottomrule\end{tabular}"
        )
        assert detect_required_packages(latex_code) == []

    def test_loaded_package_with_options(self):
        latex_code = (
            r"\usepackage[utf8]{inputenc}"
            r"\usepackage[table]{xcolor}"
            r"\textcolor{red}{x}"
        )
        assert detect_required_packages(latex_code) == []

    def test_loaded_multiple_packages_comma_separated(self):
        latex_code = r"\usepackage{booktabs, multirow}" r"\toprule \multirow{2}{*}{x}"
        assert detect_required_packages(latex_code) == []

    def test_requirement_package_excluded(self):
        latex_code = r"\RequirePackage{amsmath}$\dfrac{1}{2}$"
        assert detect_required_packages(latex_code) == []

    def test_exclude_loaded_false_keeps_loaded(self):
        latex_code = r"\usepackage{booktabs}\toprule"
        assert detect_required_packages(latex_code, exclude_loaded=False) == [
            "booktabs"
        ]

    def test_commented_usepackage_ignored(self):
        latex_code = r"% \usepackage{booktabs}" + "\n" + r"\toprule"
        assert detect_required_packages(latex_code) == ["booktabs"]

    def test_verb_usepackage_ignored(self):
        latex_code = r"\verb|\usepackage{booktabs}| \toprule"
        assert detect_required_packages(latex_code) == ["booktabs"]

    def test_partial_loaded_only_missing_recommended(self):
        latex_code = r"\usepackage{booktabs}" r"\toprule \num{1.23}"
        assert detect_required_packages(latex_code) == ["siunitx"]

    def test_sty_suffix_normalized(self):
        latex_code = r"\usepackage{booktabs.sty}\toprule"
        assert detect_required_packages(latex_code) == []

    def test_space_between_command_and_brace(self):
        latex_code = r"\usepackage {booktabs}\toprule"
        assert detect_required_packages(latex_code) == []


class TestAnalyzeLatexPackages:
    """analyze_latex_packages 规则表优先 + LLM 兜底测试（Mock LLM）。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_rule_table_hit_skips_llm(self, mock_llm):
        latex_code = r"\begin{tabular}{cc}\toprule A & B \\\bottomrule\end{tabular}"
        packages = await analyze_latex_packages(latex_code, mock_llm)
        assert packages == ["booktabs"]
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_rule_table_miss_falls_back_to_llm(self, mock_llm):
        mock_llm.chat.return_value = {"packages": ["somefancypkg"]}
        packages = await analyze_latex_packages(r"\somefancycmd{1}", mock_llm)
        assert packages == ["somefancypkg"]
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_output_normalized(self, mock_llm):
        mock_llm.chat.return_value = {
            "packages": ["BookTabs.STY", "multirow", "booktabs", "", "  "]
        }
        packages = await analyze_latex_packages(r"\somefancycmd{1}", mock_llm)
        assert packages == ["booktabs", "multirow"]

    @pytest.mark.asyncio
    async def test_llm_none_skips_fallback(self):
        packages = await analyze_latex_packages(r"\somefancycmd{1}", None)
        assert packages == []

    @pytest.mark.asyncio
    async def test_empty_latex(self, mock_llm):
        packages = await analyze_latex_packages("", mock_llm)
        assert packages == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_degradation(self, mock_llm):
        mock_llm.chat.side_effect = RuntimeError("API unavailable")
        packages = await analyze_latex_packages(r"\somefancycmd{1}", mock_llm)
        assert packages == []

    @pytest.mark.asyncio
    async def test_invalid_json_graceful_degradation(self, mock_llm):
        mock_llm.chat.return_value = {"bad_field": True}
        packages = await analyze_latex_packages(r"\somefancycmd{1}", mock_llm)
        assert packages == []

    @pytest.mark.asyncio
    async def test_domain_excluded_rules_skip_llm(self, mock_llm):
        """math 领域规则表命中但被领域排除 → 不调用 LLM 兜底。"""
        packages = await analyze_latex_packages(r"\toprule", mock_llm, domain="math")
        assert packages == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_output_filtered_by_domain(self, mock_llm):
        """LLM 兜底输出含领域无关包 → 过滤后返回。"""
        mock_llm.chat.return_value = {"packages": ["somefancypkg", "booktabs"]}
        packages = await analyze_latex_packages(
            r"\somefancycmd{1}", mock_llm, domain="math"
        )
        assert packages == ["somefancypkg"]

    @pytest.mark.asyncio
    async def test_llm_output_filtered_by_loaded(self, mock_llm):
        """LLM 兜底输出含已加载包 → 过滤后返回。"""
        mock_llm.chat.return_value = {"packages": ["booktabs", "somefancypkg"]}
        latex = r"\usepackage{booktabs}\somefancycmd{1}"
        packages = await analyze_latex_packages(latex, mock_llm)
        assert packages == ["somefancypkg"]

    @pytest.mark.asyncio
    async def test_rule_hit_but_all_loaded_skips_llm(self, mock_llm):
        """规则表命中但全部已加载 → 返回空且不调用 LLM。"""
        latex = r"\usepackage{booktabs}\toprule"
        packages = await analyze_latex_packages(latex, mock_llm)
        assert packages == []
        mock_llm.chat.assert_not_called()


class TestPrependPackageComments:
    """prepend_package_comments 拼接与降级测试。"""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.chat = AsyncMock()
        return llm

    @pytest.mark.asyncio
    async def test_rule_table_detection_no_llm_call(self, mock_llm):
        result = await prepend_package_comments(mock_llm, r"\toprule A \\\bottomrule")
        assert result.startswith(r"% \usepackage{booktabs}")
        assert r"\toprule A \\\bottomrule" in result
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_prepends(self, mock_llm):
        mock_llm.chat.return_value = {"packages": ["somefancypkg"]}
        result = await prepend_package_comments(mock_llm, r"\somefancycmd{x}")
        assert result.startswith(r"% \usepackage{somefancypkg}")

    @pytest.mark.asyncio
    async def test_without_packages(self, mock_llm):
        mock_llm.chat.return_value = {"packages": []}
        latex = r"\hline A \hline"
        result = await prepend_package_comments(mock_llm, latex)
        assert result == latex

    @pytest.mark.asyncio
    async def test_llm_failure_returns_original(self, mock_llm):
        mock_llm.chat.side_effect = RuntimeError("API error")
        latex = r"\somefancycmd{test}"
        result = await prepend_package_comments(mock_llm, latex)
        assert result == latex

    @pytest.mark.asyncio
    async def test_empty_latex_returns_original(self, mock_llm):
        result = await prepend_package_comments(mock_llm, "")
        assert result == ""
        mock_llm.chat.assert_not_called()

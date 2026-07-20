"""
LaTeX 宏包分析器。

职责：
    1. 输入一段 LaTeX 代码
    2. 使用 LLM 分析该代码使用的 LaTeX 命令/环境
    3. 判断需要哪些额外宏包支持
    4. 返回宏包名称列表

设计原则：
    - 独立于具体业务模块（表格 OCR、公式 OCR 等均可复用）
    - 返回空列表表示不需要任何特定宏包
    - LLM 调用失败时记录警告并返回空列表（优雅降级）
"""

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.utils.json_utils import retry_with_validation

logger = get_logger("latex_package_analyzer")


class LatexPackageAnalysis(BaseModel):
    packages: list[str]


_PACKAGE_ANALYSIS_PROMPT = (
    "You are an expert LaTeX package dependency analyzer. "
    "Your task is to examine a LaTeX table code snippet and identify ALL "
    "external packages required for it to compile successfully.\n\n"

    "## Analysis Process\n"
    "1. Scan the code for every command, environment, and column specifier.\n"
    "2. Map each feature to its required package using the reference below.\n"
    "3. Exclude features that belong to the LaTeX kernel (loaded by default).\n"
    "4. Return a deduplicated, alphabetically sorted list of required packages.\n\n"

    "## Feature → Package Mapping\n"
    "- \\toprule, \\midrule, \\bottomrule, \\cmidrule, \\addlinespace → booktabs\n"
    "- \\multirow, \\multirowcell → multirow\n"
    "- \\makecell, \\thead, \\makecellbox → makecell\n"
    "- \\diagbox → diagbox\n"
    "- \\resizebox, \\includegraphics, \\rotatebox, \\scalebox → graphicx\n"
    "- >{...}, <{...}, p{{}}, m{{}}, b{{}} column specifiers → array\n"
    "- X column specifier → tabularx\n"
    "- S column specifier, \\num, \\SI, \\tablenum → siunitx\n"
    "- D column specifier → dcolumn\n"
    "- \\cellcolor, \\rowcolor, \\columncolor → colortbl\n"
    "- \\hhline → hhline\n"
    "- {{NiceTabular}}, \\Block, \\CodeBefore → nicematrix\n"
    "- {{tblr}}, \\SetRow, \\SetCell → tabularray\n"
    "- {{longtable}}, \\endhead, \\endfoot, \\longtable → longtable\n"
    "- {{threeparttable}}, \\tablenotes, \\tnote → threeparttable\n"
    "- \\ding, \\Pifont → pifont\n"
    "- Non-trivial math (\\frac, \\sum, \\begin{{equation}}, etc.) → amsmath\n\n"

    "## Rules\n"
    "1. Only include packages that are REQUIRED by features actually present.\n"
    "2. Do NOT include kernel-level commands/environments: tabular, "
    "\\multicolumn, \\cline, \\hline, \\begin{{tabular}}, standard math mode.\n"
    "3. If no extra packages are needed, return an empty list.\n"
    "4. Package names must be lowercase, without the '.sty' extension.\n"
    "5. Deduplicate the list and sort alphabetically.\n"
    "6. When a package is ambiguous (e.g., xcolor vs colortbl), prefer the "
    "one that directly provides the used command.\n\n"

    "## Examples\n"
    "Input: \\begin{{tabular}}{{lcr}}\\toprule A & B & C\\\\\\midrule 1 & 2 & 3\\\\"
    "\\bottomrule\\end{{tabular}}\n"
    "Output: {{\"packages\": [\"booktabs\"]}}\n\n"
    "Input: \\begin{{tabular}}{{>{{\\bfseries}}l p{{3cm}}}}\\makecell{{A\\\\B}} & "
    "\\multirow{{2}}{{*}}{{text}}\\\\\\end{{tabular}}\n"
    "Output: {{\"packages\": [\"array\", \"makecell\", \"multirow\"]}}\n\n"
    "Input: \\begin{{tabular}}{{ll}}a & b\\end{{tabular}}\n"
    "Output: {{\"packages\": []}}\n\n"

    "## Output Format\n"
    "Respond with valid JSON only. No markdown fences, no explanations, "
    "no trailing commas.\n"
    '{{\"packages\": [\"package1\", \"package2\"]}}'
)


async def analyze_latex_packages(latex_code: str, llm) -> list[str]:
    """
    分析 LaTeX 代码所需的宏包列表。

    参数：
        latex_code: 待分析的 LaTeX 代码
        llm: LLMClient 实例，用于调用 LLM

    返回：
        宏包名称列表。若不需要任何特定宏包，返回空列表。
        若 LLM 调用失败，记录警告并返回空列表（优雅降级）。
    """
    if not latex_code or not latex_code.strip():
        return []

    messages = [
        {"role": "system", "content": _PACKAGE_ANALYSIS_PROMPT},
        {
            "role": "user",
            "content": f"Analyze this LaTeX code and list required packages:\n\n{latex_code}",
        },
    ]

    try:
        validated: LatexPackageAnalysis = await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=LatexPackageAnalysis,
            max_retries=2,
            logger_name="latex_package_analyzer",
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        return validated.packages
    except Exception as e:
        logger.warning(
            f"Package analysis failed, falling back to empty list: {e}")
        return []


def packages_to_comments(packages: list[str]) -> str:
    """
    将宏包名称列表转换为 LaTeX 注释格式。

    参数：
        packages: 宏包名称列表

    返回：
        格式化的注释字符串，每行一个 % \\usepackage{...}
        若列表为空，返回空字符串。
    """
    if not packages:
        return ""
    return "\n".join(f"% \\usepackage{{{pkg}}}" for pkg in packages)
